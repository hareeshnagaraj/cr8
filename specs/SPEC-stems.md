# SPEC: one-click stem / acapella extraction

Implements the verdict of `reports/stem-separation-research.md` — **read that report first**;
it contains the UVR audit, model inventory, timings, argv shapes, schema, and the job-queue
design. This spec is the build order and the acceptance bar. Where the two differ, the report's
technical detail wins; where this spec narrows scope, this spec wins.

## Goal

Click a song in cr8 → its stems (vocals / instrumental / drums / bass / other) are separated
locally on Apple Silicon → they land in the catalog, playable in the app, shuffleable, and
includable in shares (so you can send someone just the acapella). No cloud, no per-use cost.

## Already done (do not redo)

- `.venv-stems/` created with `audio-separator[cpu]==0.44.5` on Python 3.13 (isolated from
  cr8's 3.14 venv — never add these deps to cr8's).
- Confirmed on disk, so the default recipe needs **zero downloads**:
  - `/Applications/Ultimate Vocal Remover.app/Contents/Resources/models/MDX_Net_Models/UVR-MDX-NET-Inst_HQ_5.onnx`
  - `.../Demucs_Models/v3_v4_repo/{htdemucs.yaml,955717e8-8726e21a.th}`
  - plus MDX Inst_HQ_3/4, VR `1_HP-UVR`, `UVR-DeNoise{,-Lite}`.
- UVR has **no headless entrypoint** (PyInstaller GUI-only, verified). Never drive the GUI.

## Build order — ship each step working

### 1. `cr8 stems separate <bounce-ulid>` (synchronous, one bounce)
- Copy UVR models into `models/uvr/` (with their `model_data` JSONs) so the app never depends on
  the .app bundle staying installed; log what was copied.
- Two passes via `run_tool` argv lists (never `shell=True`), per report §4:
  - **A:** `UVR-MDX-NET-Inst_HQ_5.onnx` → `vocals`, `instrumental`
  - **B:** `htdemucs.yaml` → `drums`, `bass`, `other` (discard its vocals; A's is better)
- `PYTORCH_ENABLE_MPS_FALLBACK=1` in the child env. **First real run must be timed and logged** —
  if pass B is far slower than ~60–150 s/track it's silently on CPU; record the measurement in
  `reports/stems-first-run.md` either way.
- Output to `stems/.work/<job-ulid>/`, verify each output (ffprobe duration within ±0.5 s of
  source, decode check), then atomically move into `stems/<bounce-ulid>/` with a `manifest.json`
  (recipe, model filenames, tool version, source sha256, per-file sha256s). Interruption leaves
  only `.work/`, swept on next start.
- Archival FLAC in `stems/`; **`stems/` is durable and joins the restic backup set** — never the
  mirror (which self-prunes; stems cost hours of compute to recreate).

### 2. Catalog model (migration; follow report §8 exactly)
New tables `stem_runs` + `stems` — do **not** fake `bounces`/`files` rows for stems (it would
break `cr8 scan`'s `missing_since` and `cr8 verify` coverage invariants). Each stem gets its
own ULID for media serving. Recipes: `default-v1` and `hq-v1` coexist per bounce.

### 3. Mirror + playback integration
`cr8 build` renders each stem to `mirror/tracks/<stem-ulid>.mp3` + `mirror/peaks/<stem-ulid>.json`
using the existing pipeline, so `/m/<ulid>`, Range serving, containment checks, and the queue
engine all work unchanged. A mirror rebuild re-transcodes from `stems/` in seconds.

### 4. Job queue + worker (report §9)
`jobs` table; lease-based claim (`BEGIN IMMEDIATE`, `lease_until`, attempts, reclaim on expiry);
**the DB connection is closed for the entire subprocess duration**. `cr8 stems worker --drain`
under a third launchd plist. Failures write an `app_alerts` row — never vanish. Optional loopback
`POST /internal/jobs/poke` for SSE freshness; if it fails, htmx polling catches up (stale, never
wrong).

### 5. UI (owner app first; ratified design tokens)
- Song page gains a **Stems** section: "rip stems" button when none exist; live job state
  (queued / separating / done / failed with reason) while running; the five stems listed, each
  tappable to play through the same queue engine, each individually shareable.
- Honest labels per report §6: "other" is described as leftovers, not dressed up as a stem.
- "Redo in high quality" action → `hq-v1` recipe (`model_bs_roformer_ep_317`, ~3× slower,
  downloads on first use). Both recipes coexist; UI shows which is playing.
- Batch: multi-select → queue stems for many songs (this is the weekend-long catalog run).

### 6. Guest/band surfaces
Stems appear only when explicitly in a share's snapshotted scope — scope is still the boundary,
and stems never widen it. A share picker option "include stems" adds the stem ULIDs to scope at
mint time.

## Acceptance

- pytest green: manifest round-trip; interrupted job leaves no partial in `stems/`; lease
  reclaim after expiry; a failed job surfaces an alert row and does not retry forever; stem
  ULIDs serve through `/m/` with Range; out-of-scope stem 404s for a guest; `cr8 verify` does
  not flag stems as coverage holes; mirror prune never deletes `stems/`.
- Real run on one bounce: five stems produced, durations match source, all five play in the app,
  timings recorded.
- `cr8 verify` green afterward; corpus untouched (marker check).

## Notes
Full-catalog run is ~21–37 h at background priority (652 bounces) — on-demand per song is under
~3 minutes, which is what "one click" needs. Storage ~64 GB for the whole catalog (1.9 TB free).
Keep compute local for now; the worker talks to the queue over loopback so moving it to the spare
Mac later is a config change.
