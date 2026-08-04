# SPEC: enrichment + mirror pipeline (Phase 2–3)

Builds on the catalog core (SPEC-catalog-core.md). Same hard rules: corpus read-only, no
shell=True, ':' path only via pathlib/argv, idempotent commands, short transactions.

## New dependencies

- `pip: mediafile` (beets' tag abstraction over mutagen — replaces raw mutagen for tag WRITING),
  `pyacoustid`, `Pillow`, `python-ulid` (or vendor a 20-line ULID impl).
- `brew: chromaprint` (fpcalc), `rsgain`, `audiowaveform`, `libkeyfinder`, `aubio`, `cmake`.
  keyfinder-cli: clone EvanPurkhiser/keyfinder-cli (>=1.2.0 required — FFmpeg-6 wrong-key fix),
  cmake build against brew libkeyfinder+ffmpeg, install to ~/Music/Catalog/bin/.
  If any brew install fails, degrade gracefully: the command reports the missing tool and skips
  that enrichment kind (never crashes the pipeline).

## Schema migration (bump schema_version)

- `bounces` ADD `public_id TEXT UNIQUE` — ULID, minted at first sight, IMMUTABLE forever (the
  adversarial ruling: all guest/app-visible identifiers are ULIDs; tags/hearts/progress reference
  ULIDs, never paths or positions). Backfill for existing rows.
- `songs` ADD `public_id TEXT UNIQUE` — same.
- `files` ADD `sha256 TEXT` (weekly-scrub anchor; md5 stays for quick change detection),
  `fingerprint TEXT` (chromaprint raw), `fp_at TEXT`.
- `mirror_files`: key by `bounce_id`; columns become
  `(bounce_id PK, mirror_relpath UNIQUE, src_sha256, encoder_settings, tag_hash, built_at)` —
  incremental key is CONTENT HASH + encoder settings, never mtime (adversarial: mtime-keyed
  incrementals freeze truncated outputs in forever).
- New table `build_state (key TEXT PRIMARY KEY, value TEXT)` for last-known-good counts.

## `cr8 import-mik`

- Copy `~/Library/Application Support/Mixedinkey/Collection10.mikdb` (+ -wal/-shm if present) to a
  scratch dir first — NEVER open the live DB (MIK is running).
- Read Core Data tables (ZSONG et al.). Match to files: absolute path match first (ZBOOKMARKDATA
  holds paths — it may be an NSKeyedArchiver bookmark blob; if undecodable, fall back to
  (basename, duration ±1 s) matching against `files`).
- Rows → `mik_tracks`; per match → `analysis` rows (key/bpm/energy, source='mik', confidence 0.9).
- Promotion under precedence `human > filename > mik > detected` into songs.key_canon/bpm/energy —
  fill empty slots only; material conflicts (pitch-class/mode differs after enharmonic fold; BPM
  off by >±2 and not 2×/½×) → `key_conflict`/`bpm_conflict` review items.
- Report: matched/unmatched counts; expected ≈ 121 songs, ~39 in-corpus.

## `cr8 detect`

For curated songs still keyless/bpmless after filename+MIK: run on the LATEST main bounce
(prefer the wav twin): key via `keyfinder-cli`, BPM via `aubio tempo` (subprocess, argv-list).
→ `analysis` (source='keyfinder'/'aubio', confidence 0.6), promoted into empty slots only.
Resumable batch (skip files already analyzed); `--limit N` flag.

## `cr8 fingerprint`

`fpcalc -raw` over curated bounces (wav preferred) → `files.fingerprint`. Then offline
near-dup/version clustering: pyacoustid `compare_fingerprints` pairwise WITHIN candidate groups
only (same song, or slug-similar songs — never all-pairs over 1,276). Output: similarity edges
into `review_queue` as `merge_suggestion`/`possible_distinct` evidence enrichment (payload gains
`fp_similarity`). Also: cross-dir twin candidates confirmed/rejected by fingerprint.

## `cr8 scrub` (weekly, data-integrity ruling)

Originals are immutable by mandate ⇒ any hash change = corruption. Maintain `files.sha256`;
each run re-hashes a rotating 1/8th of curated + project files; ANY mismatch vs stored sha256 →
CRITICAL alert (notification + nonzero exit + report). This must outrun backup retention.

## `cr8 build` — the mirror

Output: `~/Music/Catalog/mirror/` — flat content-addressed layout:
`mirror/tracks/<bounce-ulid>.mp3` + `mirror/peaks/<bounce-ulid>.json` + `mirror/art/<song-ulid>.jpg`.
Human-readable names live in TAGS and the app UI, not filenames (immutable-path ruling).

Per curated bounce (one file per bounce — never both twins):
1. **Source choice:** deliberate mp3 twin exists AND ffprobe durations match wav within ±1.5 s →
   copy the artist's own mp3 bit-for-bit, then retag the COPY. Twin duration mismatch → do NOT
   pair: `twin_mismatch` review item + transcode the wav fresh (adversarial: stale-twin finding).
   wav/aif-only → `ffmpeg -codec:a libmp3lame -b:a 320k` (CBR 320 — kills wavesurfer VBR-seek bug).
   Never lossy→lossy re-encode an existing mp3.
2. **Atomic writes:** transcode to `<dest>.tmp.<pid>`; promote only after: ffmpeg exit 0, ffprobe
   duration within ±0.5 s of source, decode check (`ffmpeg -v error -i out -f null -`); then
   atomic rename. On startup, sweep orphan tmp files. Build is incomplete while any tmp remains.
3. **Tags via mediafile onto the mirror copy only** (ID3v2.4 UTF-8): title = `Title (vN, role)`
   qualifier form; album = song title; albumartist = "Hareesh"; artist = collaborators or albumartist;
   track = chronological index in version chain; date = bounce_date; genre = vibes (multi);
   bpm; initial_key = canonical; TXXX frames: CAMELOT, STATUS, ERA, INSTR, COLLAB, MIXROLE,
   ENERGY, SONGID (song ULID), BOUNCEID (bounce ULID). NO rating/keeper frames (churn ruling).
   `tag_hash` = sha256 of the canonical tag dict; tag-only changes restamp in place (no re-encode).
4. **Covers:** Pillow-generated per song — deterministic from (era color, song title hash):
   flat color-field gradient + large title typography, 1400×1400 jpg; embedded via
   mediafile.images AND written to `mirror/art/`. No network, no SVG.
5. **Loudness:** after build, `rsgain custom -a -s i` over changed tracks — ReplayGain 2.0 TXXX
   tags, metadata-only, idempotent, never re-encodes.
6. **Peaks:** `audiowaveform -i <mp3> -o <ulid>.json --pixels-per-second 10 -b 8` normalized;
   regenerate only when the mp3 content changes.
7. **Cascade guards (adversarial, all three):** REFUSE to build if catalog curated-bounce count
   < 90% of `build_state.last_good_count` (override flag `--force-shrink`); write
   `.crate_mirror_sentinel` at mirror root; after successful build store new last-good count.
   Prune mirror orphans only when the corresponding bounce row is `missing_since` > 30 days.

## `cr8 push` (jukebox sync — script now, activates when jukebox is provisioned)

`rsync -az --delete` mirror → jukebox, guarded (all mandatory): `set -euo pipefail`; sentinel file
must exist at source; source must be a real directory with count ≥ 90% of catalog expectation;
`--max-delete=50`; destination path colon-free both ends. Then POST the app's rescan hook (later).

## verify additions

- V5 activates: every non-missing curated bounce has a current `mirror_files` row whose
  src_sha256+tag_hash match; no orphan mirror files; no tmp files; peaks+art present.
- V9 (new): tmp-file sweep clean; build_state count sane.

## Acceptance

1. pytest green (new tests: ULID immutability across rescans; twin-mismatch rejection; atomic
   promote logic — simulate a killed transcode with a tmp file; incremental rebuild triggers on
   content change not mtime; cascade guard refuses on shrunken catalog; tag round-trip via
   mediafile read-back; cover determinism).
2. Real run: `cr8 import-mik` reports its match counts; `cr8 build` produces a complete
   mirror (~750 tracks) with zero tmp leftovers; `cr8 verify` V5 green; spot-check 5 mirror
   files in a player (tags visible, audio plays, duration matches).
3. Re-run `cr8 build` immediately → 0 rebuilt.
4. Corpus untouched (marker-file check as in core spec).
