# Stem separation for cr8 — research + integration recommendation

**Date:** 2026-07-29
**Status:** research only, no code written, no files outside this report touched.
**Host measured:** MacBook Pro, **Apple M1 Max**, 10 cores (8P/2E), **64 GB RAM**, 1.9 TB free on
the Music volume. ffmpeg 8.1.2 + ffprobe at `/opt/homebrew/bin`.

---

## 0. Verdict, up front

1. **Embed [`audio-separator`](https://github.com/nomadkaraoke/python-audio-separator) (MIT,
   v0.44.5, released 2026-07-20).** It is the headless CLI that speaks all four UVR model
   formats. It is the only mature, maintained, MIT-licensed option that runs the same models the
   owner already trusts.
2. **Install it in its own venv** (`.venv-stems`), never in cr8's. cr8 is `starlette==1.3.1` /
   `fastapi==0.141.1` on Python 3.14 with a `pip-audit`-gated BOM; `audio-separator` drags in
   torch, onnxruntime, librosa, pydub, `onnx-weekly` and `onnx2torch-py313`. Driving it as a
   subprocess through the existing `cr8/tooling.py` `find_tool` / `run_tool` argv pattern keeps
   that entire dependency mass outside cr8's audited surface.
3. **Yes — reuse the UVR models already on disk. All seven are recognised by exact filename.**
   That saves the two downloads that matter (the 84 MB htdemucs bag and a 59 MB MDX model) and
   means the default recipe needs **zero model downloads**.
4. **UVR itself has no headless entrypoint. Confirmed by direct inspection, not by reading docs**
   — see §1. Do not try to drive the GUI.
5. **Default recipe = two passes, both from on-disk models:** `UVR-MDX-NET-Inst_HQ_5.onnx` for
   vocals + instrumental (the good acapella), `htdemucs.yaml` for drums + bass + other. That is
   exactly the five stems asked for, and it is the fastest route to all five.
6. **Stems do NOT belong in the mirror.** The mirror is disposable and self-prunes; stems cost
   ~20–35 hours of GPU time for the catalog. Argument and layout in §7.
7. **Build the worker local-first but talk to the queue over loopback HTTP**, so moving it to
   `hnclawbot` later is a config change. Recommendation is to stay local for now — §10.

---

## 1. What is actually on disk (UVR audit)

`/Applications/Ultimate Vocal Remover.app` — built 2023-10-17, PyInstaller *onedir* bundle,
Python 3.11, **torch 2.1.0** frozen inside, Mach-O arm64.

### Is there a headless / CLI entrypoint? **No.**

I parsed the PyInstaller CArchive cookie and table of contents out of
`Contents/MacOS/UVR` directly (read-only). The archive contains **14 entries**, of which exactly
**one** is application code:

```
m  struct                     344
m  pyimod01_archive          6226
m  pyimod02_importers       24802
m  pyimod03_ctypes           7035
s  pyiboot01_bootstrap       1838
s  pyi_rth_inspect           1679
s  pyi_rth_pkgres            8569
s  pyi_rth_multiprocessing   4669
s  pyi_rth_pkgutil           2465
s  pyi_rth_setuptools        1182
s  pyi_rth__tkinter          1137
s  UVR                     596903   <-- the entire app: the Tk GUI
```

There is no `separate` script, no `__main__` with argparse, no secondary entrypoint. `UVR.py`'s
code object is marshalled Python 3.11 bytecode inside the PYZ; the only way to reach it would be
`pyinstxtractor` + `decompyle`, and what you'd get is a Tkinter app whose separation logic is
wired to widget state and a `torch 2.1.0` that predates every relevant MPS fix. The `pyi_rth__tkinter`
runtime hook confirms it boots a GUI. **Dead end — and the right one, because the models are the
valuable part, not the wrapper.**

### The models (516 MB total) — every one is in audio-separator's catalog

| File | Arch | Size | Date | In audio-separator catalog? |
|---|---|---|---|---|
| `MDX_Net_Models/UVR-MDX-NET-Inst_HQ_3.onnx` | MDX-Net | 66.8 MB | 2023-10-17 | yes |
| `MDX_Net_Models/UVR-MDX-NET-Inst_HQ_4.onnx` | MDX-Net | 59.1 MB | 2024-05-20 | yes |
| `MDX_Net_Models/UVR-MDX-NET-Inst_HQ_5.onnx` | MDX-Net | 59.1 MB | 2025-12-16 | yes |
| `VR_Models/1_HP-UVR.pth` | VR Arch | 126.8 MB | 2023-10-17 | yes |
| `VR_Models/UVR-DeNoise.pth` | VR Arch | 127.1 MB | 2024-08-17 | yes |
| `VR_Models/UVR-DeNoise-Lite.pth` | VR Arch | 17.9 MB | 2023-10-17 | yes |
| `Demucs_Models/v3_v4_repo/955717e8-8726e21a.th` + `htdemucs.yaml` | Demucs v4 | 84.1 MB | 2023-10-17 | yes — this is exactly `Demucs v4: htdemucs` |

Verified by pulling
`https://raw.githubusercontent.com/TRvlvr/application_data/main/filelists/download_checks.json`
(the catalog audio-separator itself uses) and matching filenames. All seven present.

Also on disk and reusable: `MDX_Net_Models/model_data/mdx_c_configs/*.yaml` (17 MDXC configs) and
`VR_Models/model_data/model_data.json` — but audio-separator fetches its own copies of those, so
they're not needed.

**Two notes.** The Demucs pair is the *base* `htdemucs`, not `htdemucs_ft`; the fine-tuned bag is
four more `.th` files (~320 MB) not on disk. And `UVR-MDX-NET-Inst_HQ_5.onnx` is dated 2025-12-16,
so the owner has been keeping models current — HQ_5 is the newest MDX-Net instrumental model and
is the right pass-A default.

---

## 2. Tool landscape as of July 2026

### `audio-separator` — **the pick**

- MIT. v0.44.5, 2026-07-20. Actively released (0.43, 0.44 in the last month).
- Python `>=3.10`, wheels/classifiers through **3.14**.
- Speaks **MDX-Net (.onnx), VR Arch (.pth), Demucs (.yaml + .th), MDXC / MDX23C / RoFormer /
  SCNet / Bandit (.ckpt + .yaml)** — one CLI, one output convention, every UVR model.
- Device selection is real, and it's the code path we care about (`separator.py:377-437`):
  CUDA → **MPS** → DirectML → CPU. On arm64 it sets `torch.device("mps")` *and* requests
  `CoreMLExecutionProvider` for ONNX Runtime. Verify at install time with `--env_info`, which
  prints `ONNXruntime has CoreMLExecutionProvider available, enabling acceleration`.
- `--model_file_dir` is a **flat** directory; `download_file_if_not_exists()` skips any file
  already present. That is what makes model reuse a copy-and-go operation.
- Demucs resolution is `get_demucs_model(name=<yaml basename>, repo=Path(<model_file_dir>))`
  (`demucs_separator.py:111`) — i.e. it expects the `.yaml` and the `.th` **side by side in one
  directory**, which is exactly UVR's `v3_v4_repo/` layout.

**One real Apple Silicon hazard, and it is fixable in one line.** `pyproject.toml` pins
`samplerate = "0.1.0"`, whose *only* PyPI artifact is
`samplerate-0.1.0-py2.py3-none-any.whl` bundling a prebuilt **x86_64** `libsamplerate.dylib`.
On arm64 importing it dies with `mach-o file, but is an incompatible architecture`
([issue #293](https://github.com/nomadkaraoke/python-audio-separator/issues/293), opened
2026-07-17, **still open**, no maintainer response). Mitigations, both verified:
- `samplerate` **0.2.4** (2026-03-22) ships `macosx_*_universal2` wheels for cp39–cp314. Force it
  after install; pip will warn about the pin and work fine.
- I grepped the whole source: **audio-separator never actually imports `samplerate`.** Every hit
  in the repo is a `model.samplerate` attribute or a local variable. So the pin is vestigial and
  the failure is latent rather than certain — but pin over it anyway, it costs nothing.

### `mlx-audio-separator` — the accelerator, not the spine

MIT, v0.1.5 (2026-06-14), by `ssmall256`. MLX-native, **no PyTorch at inference**. CLI
deliberately mirrors audio-separator's (`-m htdemucs_ft.yaml`, `--list_models`, `--output_format`).
Measured on an M4 mini against MUSDB18-HQ:

| model | speedup vs audio-separator |
|---|---|
| `htdemucs_ft.yaml` | 1.40× |
| `model_bs_roformer_ep_317_sdr_12.9755.ckpt` | 2.16× |
| `mel_band_roformer_instrumental_instv7n_gabox.ckpt` | 2.50× |
| `UVR-MDX-NET-Inst_HQ_3.onnx` | 1.53× |
| **median (4-model set)** | **1.847×** |

Numerical parity claimed at `rel L2 <= 5e-2` on 4/4 models. Requires converted MLX weights, but
ships first-run conversion from `.ckpt` / `.onnx` / Demucs checkpoints via the `[convert]` extra.

**Why it's not the default:** 10 stars, 59 commits, 6 releases, one maintainer, 0.1.x, five months
old. A ~1.85× speedup is not worth betting a multi-hour pipeline on a project that could go quiet.
**But** because its CLI shape matches, the right move is to make the model+argv a config value so
swapping engines is a config change. Revisit in six months; if it's still alive, the swap is an
afternoon and cuts a 30-hour catalog run to ~16.

`ssmall256/demucs-mlx` (MIT, v1.4.4) is the narrower sibling — htdemucs / htdemucs_ft /
htdemucs_6s / hdemucs_mmi / mdx / mdx_extra, `~73× realtime`, and its own README benchmarks
**2.7 s vs PyTorch-MPS 6.9 s** on a 3:15 track (M4 Max). Note what that benchmark implies:
PyTorch MPS demucs *does* run — see the MPS caveat in §5.

### Demucs upstream

`facebookresearch/demucs` was **archived 2025-01-01, read-only**. The live fork is
`adefossez/demucs`, maintained by the original author, who has stated it is bug-fixes-only with
slow replies and no new features. `htdemucs_ft` is the quality pick (vocals SDR **11.2685** per
audio-separator's own scores table), `htdemucs` the speed pick, `htdemucs_6s` adds piano + guitar
(experimental, and the piano stem is widely considered poor). **Do not add a direct `demucs`
dependency** — audio-separator vendors a Demucs implementation already (`uvr_lib_v5/demucs/`), so
taking both means two copies of the same model code.

### 2025–2026 SOTA worth knowing

- **BS-RoFormer** (ByteDance, SDX'23 winner) remains the reference. `model_bs_roformer_ep_317_sdr_12.9755.ckpt`
  is the model the audio-separator maintainer calls "my go-to for a clean, full-spectrum
  separation." Implementation lineage is `lucidrains/BS-RoFormer` (MIT); ZFTurbo's training configs
  are MIT.
- **Mel-Band RoFormer** ([arXiv:2310.01809](https://arxiv.org/abs/2310.01809)) swaps empirical band
  splits for mel-scale projection. audio-separator's bundled `models.json` carries **78 RoFormer
  entries**, including the whole Gabox / Unwa / becruily family that top UVR users actually run.
  Licensing is per-checkpoint and messy — e.g. Kim Vocal 2 was GPL-3.0 from 2025-06-17 and
  **relicensed MIT on 2026-04-22**. For a private, self-hosted band app none of this bites, but
  if stems ever ship publicly, check the specific checkpoint.
- **SCNet** — four variants in the catalog (`SCNet-XL` at `model_scnet_ep_54_sdr_9.8051.ckpt`).
  4-stem, competitive, slower. Not worth it over htdemucs here.
- **MDX23C** — `MDX23C-8KFFT-InstVoc_HQ.ckpt`, instrumental SDR 16.3035. Excellent and the
  slowest of the practical options (2:37 on an M3 Max).
- **Apollo** (`JusperLee/Apollo`, [arXiv:2409.08514](https://arxiv.org/abs/2409.08514)) — repairs
  codec damage in lossy audio, **not** a separator. **CC BY-SA 4.0**, i.e. share-alike. It would
  only help on the 11 `.m4a` and the mp3-only bounces, and it adds a copyleft license to the tree.
  **Skip.**

### macOS-native wrappers

**StemRoller** is the obvious name — Electron over Demucs — but I could not confirm its 2026
maintenance status or that it has an Apple Silicon build, and it has no scriptable interface
worth targeting. Every other "local Mac stem app" in this space is either a GUI over Demucs or
closed-source. **Nothing here beats a CLI you drive from argv.** Flagged as the weakest-verified
part of this report; it also doesn't change the verdict.

---

## 3. Install — exact commands, macOS Apple Silicon

```bash
cd ./

# Separate venv. Pin 3.13, not 3.14: audio-separator's onnx-weekly / onnx2torch-py313
# dependency chain is best-trodden there. cr8's own 3.14 venv is untouched.
python3.13 -m venv .venv-stems
.venv-stems/bin/pip install --upgrade pip
.venv-stems/bin/pip install 'audio-separator[cpu]==0.44.5'

# Required on arm64: step over the x86_64-only samplerate pin (issue #293).
.venv-stems/bin/pip install --upgrade 'samplerate==0.2.4'

# Must print: "ONNXruntime has CoreMLExecutionProvider available, enabling acceleration"
.venv-stems/bin/audio-separator --env_info
```

`[cpu]` is correct on Apple Silicon — it pulls plain `onnxruntime`, which carries the CoreML
execution provider on macOS. There is no `[silicon]` extra; acceleration is auto-detected.

### Seed the model directory from UVR (zero downloads for the default recipe)

**Copy, don't symlink** — 516 MB is nothing against 1.9 TB free, and copying decouples the worker
from the GUI app staying installed, makes `models/uvr` a self-contained backed-up asset, and
avoids `os.path.isfile` following a link into `/Applications` at 3 a.m.

```bash
UVR="/Applications/Ultimate Vocal Remover.app/Contents/Resources/models"
DEST=".//models/uvr"
mkdir -p "$DEST"

# Flat layout is required — audio-separator's model_file_dir is not nested.
cp "$UVR/MDX_Net_Models/UVR-MDX-NET-Inst_HQ_3.onnx" "$DEST/"
cp "$UVR/MDX_Net_Models/UVR-MDX-NET-Inst_HQ_4.onnx" "$DEST/"
cp "$UVR/MDX_Net_Models/UVR-MDX-NET-Inst_HQ_5.onnx" "$DEST/"
cp "$UVR/VR_Models/1_HP-UVR.pth"                    "$DEST/"
cp "$UVR/VR_Models/UVR-DeNoise.pth"                 "$DEST/"
cp "$UVR/VR_Models/UVR-DeNoise-Lite.pth"            "$DEST/"
# htdemucs: the .yaml and the .th bag member must land in the SAME directory.
cp "$UVR/Demucs_Models/v3_v4_repo/955717e8-8726e21a.th" "$DEST/"
cp "$UVR/Demucs_Models/v3_v4_repo/htdemucs.yaml"        "$DEST/"

# Pre-seed metadata so the worker never needs the network mid-job.
curl -sL https://raw.githubusercontent.com/TRvlvr/application_data/main/filelists/download_checks.json \
  -o "$DEST/download_checks.json"
curl -sL https://raw.githubusercontent.com/TRvlvr/application_data/main/vr_model_data/model_data_new.json \
  -o "$DEST/vr_model_data.json"
curl -sL https://raw.githubusercontent.com/TRvlvr/application_data/main/mdx_model_data/model_data_new.json \
  -o "$DEST/mdx_model_data.json"
```

Those three JSON filenames are exact — from `separator.py:610`, `:805`, `:809`. With them
pre-seeded and the models present, the default recipe runs **fully offline**. Add
`models/` to `.gitignore` and to the restic backup set.

### Optional, if the owner wants the best acapella (§6)

```bash
# BS-RoFormer 1297: ~150 MB, one-time.
.venv-stems/bin/audio-separator --download_model_only \
  --model_file_dir .//models/uvr \
  --model_filename model_bs_roformer_ep_317_sdr_12.9755.ckpt
```

---

## 4. Exact CLI invocation shape

argv lists, no `shell=True`, matching `cr8/tooling.py:run_tool`. `--custom_output_names` is
`type=json.loads`, so it takes a single JSON string as one argv element — no shell quoting
anywhere. Output path is `{output_dir}/{custom_name}.{format}` (`common_separator.py:490-507` for
the name, `:316-319` and `:404-407` for the `output_dir` join).

### Pass A — vocals + instrumental

```python
SEP = Path(".//.venv-stems/bin/audio-separator")
MODELS = Path(".//models/uvr")

argv_pass_a = [
    str(SEP),
    str(source_path),                       # the ORIGINAL bounce (.wav), not the mirror mp3
    "--model_file_dir", str(MODELS),
    "--model_filename", "UVR-MDX-NET-Inst_HQ_5.onnx",
    "--output_dir", str(work_dir),          # stems/.work/<job-ulid>/
    "--output_format", "FLAC",
    "--use_soundfile",                      # bypass pydub's forced int16 downconvert
    "--normalization", "1.0",               # do not re-gain an archival stem
    "--custom_output_names",
    '{"Vocals": "vocals", "Instrumental": "instrumental"}',
    "--mdx_segment_size", "256",
    "--mdx_overlap", "0.25",
    "--mdx_batch_size", "1",
    "--log_level", "info",
]
```

Produces `work_dir/vocals.flac` and `work_dir/instrumental.flac`.

### Pass B — drums + bass + other

```python
argv_pass_b = [
    str(SEP),
    str(source_path),
    "--model_file_dir", str(MODELS),
    "--model_filename", "htdemucs.yaml",    # the .yaml, not the .th
    "--output_dir", str(work_dir),
    "--output_format", "FLAC",
    "--use_soundfile",
    "--normalization", "1.0",
    "--custom_output_names",
    '{"Drums": "drums", "Bass": "bass", "Other": "other", "Vocals": "_demucs_vocals"}',
    "--demucs_shifts", "1",                 # NOT the default 2 — that doubles wall clock
    "--demucs_overlap", "0.25",
    "--demucs_segment_size", "Default",
    "--log_level", "info",
]
```

Demucs always emits all four sources; there is no `--single_stem` for a 3-of-4 selection. Write
`_demucs_vocals.flac` and delete it in the promote step (it is strictly worse than pass A's
vocals — SDR 11.27 for htdemucs_ft vs 12.98 for the roformer class, and worse still for base
htdemucs).

Stem-name keys are the `CommonSeparator` constants and are matched case-insensitively:
`Vocals`, `Instrumental`, `Drums`, `Bass`, `Other`, `Guitar`, `Piano`.

### Subprocess environment

Pass an explicit, minimal env:

```python
env = {
    "PATH": "/opt/homebrew/bin:/usr/bin:/bin",   # ffmpeg/ffprobe if pydub is ever hit
    "HOME": str(Path.home()),
    "PYTORCH_ENABLE_MPS_FALLBACK": "1",          # unsupported op -> CPU kernel, not a crash
    "OMP_NUM_THREADS": "6",                      # leave 2P+2E for the DAW; see §10
}
```

`PYTORCH_ENABLE_MPS_FALLBACK=1` is cheap insurance and belongs there permanently — see §5.

### Timeout

`run_tool(..., timeout=...)` should scale with duration, not be a constant. The longest bounce in
the catalog is **654 s (10:54)** against a mean of **172 s (2:52)**. Use
`timeout = 120 + duration_s * 4.0` per pass, which gives ~13 min for a typical track and ~46 min
for the longest, and a `124` exit code that the job layer already understands.

---

## 5. Does MPS actually work, or does it fall back?

**ONNX / MDX-Net path — yes, with CoreML.** `configure_mps()` sets
`onnx_execution_provider = ["CoreMLExecutionProvider"]` when ORT reports it, and logs a loud
warning when it doesn't. The maintainer's own M3 Max timing of 36 s for a 3-minute track on
`UVR-MDX-NET-Inst_HQ_4.onnx` is only achievable with acceleration on. **Verify locally with
`--env_info` before trusting any estimate in §6.**

**Torch / RoFormer path — yes.** Same maintainer, same machine, `model_bs_roformer_ep_317` at
1:49 for 3 minutes. That is MPS working.

**Torch / Demucs path — conflicting evidence, and this is the one thing to measure first.**
- Against: a widely-cited Medium post claims "Demucs doesn't work with [MPS] — complex tensors,
  custom ops, and various incompatibilities get in the way." That post exists to promote an MLX
  port, so treat it as interested.
- For: `demucs-mlx`'s own README benchmarks itself *against PyTorch MPS demucs* at 6.9 s on a
  3:15 track. You cannot benchmark against something that doesn't run.
- The historical failures were real but predate torch 2.x complex/`stft` support on MPS
  (macOS 14+). audio-separator requires `torch >= 2.3` and the host is on Darwin 25.4.

**Resolution:** set `PYTORCH_ENABLE_MPS_FALLBACK=1` — any unsupported op silently uses a CPU
kernel instead of raising — and **make pass B on one real bounce the first thing the implementer
runs.** If htdemucs is quietly falling back to CPU for the hybrid branch, pass B will be several
times slower than the estimate below, and the fix is to swap pass B to `demucs-mlx` (which needs
no PyTorch at all and keeps the same argv-list integration shape).

Separately: audio-separator's Demucs is UVR's **older fork** (`uvr_lib_v5/demucs/`), not upstream.
Its Demucs performance is not representative of upstream demucs, in either direction.

---

## 6. Speed, quality, and which model to default to

### Measured baseline

The audio-separator maintainer, **2023 MacBook Pro M3 Max, "the same 3 minute pop song"**
([discussion #133](https://github.com/nomadkaraoke/python-audio-separator/discussions/133)):

| model | arch | wall clock |
|---|---|---|
| `2_HP-UVR.pth` | VR | **0:19** |
| `UVR-MDX-NET-Inst_HQ_4.onnx` | MDX | **0:36** |
| `model_bs_roformer_ep_317_sdr_12.9755.ckpt` | RoFormer | **1:49** |
| `MDX23C-8KFFT-InstVoc_HQ_2.ckpt` | MDXC | **2:37** |

The catalog's mean bounce is **172 s**, so "3-minute track" maps almost exactly onto this corpus.

### Extrapolated to this M1 Max — **estimates, must be measured**

M1 Max is roughly **1.3–1.8× slower** than M3 Max on this class of work. Taking 1.5×:

| pass | model | est. per track (M1 Max) | × 652 bounces |
|---|---|---|---|
| A | `UVR-MDX-NET-Inst_HQ_5.onnx` | ~55 s | **~10 h** |
| A (hi-q) | `model_bs_roformer_ep_317` | ~2:45 | ~30 h |
| B | `htdemucs.yaml`, shifts=1 | **unmeasured**, est. 60–150 s | 11–27 h |

**Default recipe (MDX + htdemucs): ~21–37 hours for the whole catalog**, one time, at background
priority. A weekend. Per-song on demand it is **under three minutes** — which is what "one click"
actually needs to feel like, and it does not.

RAM is a non-issue: 64 GB against a peak working set in the low single-digit GB.

### Which model to default to

**Pass A default: `UVR-MDX-NET-Inst_HQ_5.onnx`.** Already on disk, newest MDX-Net instrumental
model (2025-12), ~3× faster than the RoFormer. Over 652 bounces that difference is **20 hours**.

**Offer `model_bs_roformer_ep_317_sdr_12.9755.ckpt` as an explicit per-song "high quality" re-run.**
The maintainer's judgement — *"the better quality separation offered by the Roformer and MDX23C
models are worth the extra inference time"* — is right for the handful of songs that matter, and
wrong as a batch default. Model this as `recipe='default-v1'` vs `recipe='hq-v1'` on the run row,
so both can coexist for one bounce and the UI can offer "redo this one properly."

### Realistic quality expectations — say this out loud in the UI

These are **unreleased demo bounces**, not commercial masters. Separation quality tracks source
quality hard, and every published SDR number comes from MUSDB18-HQ, which is professionally
mixed. Expect:

- **Instrumental: very good.** This is the easiest target and the one MDX-Net Inst models are
  specifically trained for.
- **Vocals: good, occasionally excellent, sometimes ugly.** Reverb tails smear into the vocal
  stem; heavy bus compression on a rough mix makes the separator fight itself; sibilance and
  breaths are where artifacts live. Usable as an idea tool and a reference, usually not as a
  deliverable without cleanup.
- **Drums and bass: reliably decent.** htdemucs is strong here even on rough sources.
- **"Other": a garbage bin by construction** — everything htdemucs couldn't assign. Expect
  cymbal bleed and smeared guitars. Label it honestly in the UI; don't dress it up as a stem.
- **Mono-ish or clipped bounces will separate badly.** No model fixes a clipped master.

Frame it in the app's voice: *an idea tool, not a stem delivery format.*

**And one thing the catalog already knows that no model can beat:** 12 songs have **real**
`vox` / `novox` / `inst` / `acap` / `bass` / `gtar` bounces (5 `vox`, 6 `novox`, 2 `bass`, 1 each
`inst`, `gtar`, `acap`). Where a genuine stem sibling exists, it is better than anything
separation will produce. **Surface the real bounce first and mark separated stems visually
distinct from real ones.** The remaining 460 songs are where this feature actually earns its keep.

### Separate from the WAV, not the mirror

623 of 1,280 curated files are `.wav`. The mirror is mp3-320 — lossy, and separating lossy audio
compounds codec artifacts into every stem. Feed the separator the **original bounce**, chosen with
the existing `cr8/audio.py:choose_mirror_source` (`cr8/audio.py:105`). The corpus is
READ-ONLY, which is fine: we only read it, and the worker's write paths never point at it.

---

## 7. Where stems live — and why not the mirror

### The argument

`mirror/` is derived and **actively self-destructing by design**:
`cr8/mirror.py:_prune_expired` (`:438`) deletes tracks, peaks, and art 30 days after their
source files go missing; `_sweep_temporary_files` (`:422`) reaps stray temporaries; the whole tree
is rebuildable from the corpus in an afternoon of ffmpeg. That is exactly the right contract for
an mp3 transcode.

It is exactly the **wrong** contract for an artifact that costs 20–35 hours of GPU time and cannot
be reproduced from the corpus without spending that time again. One prune bug, one
`--force-shrink`, one "let me just rebuild the mirror" and a weekend of compute is gone silently.

The corpus is READ-ONLY, so stems cannot go there either.

### Proposed layout — two tiers

```
~/Music/Catalog/
  stems/                                  # NEW. First-class, durable, backed up.
    <bounce-ulid>/
      vocals.flac                         # archival, 24-bit via --use_soundfile
      instrumental.flac
      drums.flac
      bass.flac
      other.flac
      manifest.json                       # recipe, models, versions, sha256s, source sha256
    .work/<job-ulid>/                     # scratch; swept on worker start
  mirror/
    tracks/<stem-ulid>.mp3                # derived mp3-320 rendition — disposable, as today
    peaks/<stem-ulid>.json                # audiowaveform, same pipeline as bounces
  models/uvr/                             # NEW. The copied UVR models + metadata JSONs.
```

**Expensive artifact durable; cheap artifact disposable.** `stems/` joins `catalog.db` in the
restic backup set (task #3). `mirror/` keeps its existing "derived, prunable" contract untouched,
and because the mp3 renditions live under `mirror/tracks/` with their own ULIDs, **the existing
`/m/<ulid>` media route, Range handling, containment checks, and peaks pipeline all work with no
changes.** A mirror rebuild re-transcodes stem mp3s from `stems/` in seconds.

### Sizing

At 2:52 mean, 5 stems: FLAC archival ≈ 50–80 MB/bounce → **~42 GB**; mp3-320 renditions ≈ 34 MB/bounce
→ **~22 GB**. Total **~64 GB** against 1.9 TB free. Fine, and worth stating in the spec so it isn't
a surprise.

---

## 8. Catalog model

### Do NOT reuse `bounces` + `mixrole='stems'`

The `mixrole` enum does already carry `'stems'` and `'acap'` — tempting, and wrong. `bounces` rows
are `UNIQUE(song_id, source_stem)` and exist to describe **files discovered in the corpus**;
`files.bounce_id` joins them to real corpus paths. A separated stem has no corpus file. Inventing
one means either fake `files` rows (which `cr8 scan` will mark `missing_since` on the next run,
and `cr8 verify` will flag as coverage holes) or `bounces` rows with no files (which breaks
`v_song_bounces`'s `MAX(f.mtime)` and `choose_mirror_source`). Either way you corrupt the
scan/verify invariants that make the catalog trustworthy. `mixrole='stems'` should stay what it is:
a label for a *real bounce* that happens to be a stem export.

### New tables

```sql
CREATE TABLE IF NOT EXISTS stem_runs (
  id INTEGER PRIMARY KEY,
  bounce_id INTEGER NOT NULL REFERENCES bounces(id),
  recipe TEXT NOT NULL,                    -- 'default-v1' | 'hq-v1'
  model_a TEXT NOT NULL,                   -- 'UVR-MDX-NET-Inst_HQ_5.onnx'
  model_b TEXT,                            -- 'htdemucs.yaml'
  pass_a_done INTEGER NOT NULL DEFAULT 0,  -- resumability, see §9
  pass_b_done INTEGER NOT NULL DEFAULT 0,
  src_relpath TEXT NOT NULL,               -- which original was separated
  src_sha256 TEXT NOT NULL,                -- staleness detection
  separator_version TEXT NOT NULL,         -- 'audio-separator 0.44.5'
  started_at TEXT, finished_at TEXT,
  ok INTEGER NOT NULL DEFAULT 0,
  UNIQUE(bounce_id, recipe));

CREATE TABLE IF NOT EXISTS stems (
  id INTEGER PRIMARY KEY,
  public_id TEXT UNIQUE NOT NULL,          -- own ULID; this is what /m/<ulid> serves
  run_id INTEGER NOT NULL REFERENCES stem_runs(id),
  bounce_id INTEGER NOT NULL REFERENCES bounces(id),
  kind TEXT NOT NULL
    CHECK(kind IN ('vocals','instrumental','drums','bass','other')),
  archive_relpath TEXT UNIQUE NOT NULL,    -- 'stems/<bounce-ulid>/vocals.flac'
  archive_sha256 TEXT NOT NULL,
  mirror_relpath TEXT,                     -- 'tracks/<stem-ulid>.mp3', NULL until built
  duration_s REAL, built_at TEXT,
  UNIQUE(run_id, kind));

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  ulid TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('stems')),
  target_id INTEGER NOT NULL,              -- bounce_id
  payload TEXT NOT NULL,                   -- JSON: {"recipe": "default-v1"}
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK(state IN ('queued','running','done','failed','cancelled')),
  priority INTEGER NOT NULL DEFAULT 0,     -- user click = 100, batch backfill = 0
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  lease_owner TEXT, lease_until TEXT,
  progress TEXT,                           -- 'pass B (drums/bass/other)'
  error TEXT,
  requested_by TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

-- One active job per bounce. Makes double-clicking the button a no-op at the DB level.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active
  ON jobs(kind, target_id) WHERE state IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_jobs_claim
  ON jobs(state, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_stems_bounce ON stems(bounce_id, kind);
```

Bump `WEB_SCHEMA_VERSION` to 4. `jobs` is deliberately generic on `kind` so the next long task
(fingerprinting, re-encode, `cr8 detect` backfill) reuses the machinery.

### Media route

`cr8/web/common/media.py:artifact_path` currently resolves a bounce ULID against
`mirror_files.mirror_relpath`. Extend `track_by_ulid` to fall through to `stems.public_id` →
`stems.mirror_relpath`. Same root, same `resolve(strict=True)` + `is_relative_to(mirror)`
containment, same FileResponse. Nothing about the security posture changes because stem mp3s live
inside the same mirror tree.

---

## 9. The job queue and the worker

### Why a queue at all

Jobs are 1–5 minutes. Doing this in a request handler blocks a uvicorn worker for minutes and
breaks the app under any concurrency. The house pattern already exists in shape (launchd +
`cr8 build`), so: **a launchd-triggered worker draining the `jobs` table.**

### Claiming, without breaking the single-writer ruling

SPEC-band-app.md's write discipline says all mutations flow through HTTP handlers, with an explicit
carve-out: *"The CLI keeps its own write paths (scan/build) but never runs concurrently with long
app transactions — app write transactions are milliseconds."* The worker is CLI-shaped and its
transactions are **also** milliseconds: claim, heartbeat, complete. The long part is a subprocess
holding **no** database connection. So it sits inside the existing carve-out, provided:

- `BEGIN IMMEDIATE`, `busy_timeout=10000`, bounded retry — same as the app.
- **The DB connection is closed for the entire duration of the subprocess.** Open, claim, close,
  separate for four minutes, open, commit, close.
- A swallowed `SQLITE_BUSY` is a bug — assert, log, write an `app_alerts` row.

Lease-based claim (crash-safe, no lock held across the work):

```sql
BEGIN IMMEDIATE;
UPDATE jobs SET state='running', lease_owner=:worker_id,
       lease_until=datetime('now','+45 minutes'),
       attempts=attempts+1, updated_at=:now
 WHERE id = (SELECT id FROM jobs
              WHERE state='queued'
                 OR (state='running' AND lease_until < datetime('now'))
              ORDER BY priority DESC, id LIMIT 1)
RETURNING id, target_id, payload;
COMMIT;
```

An expired lease is reclaimed automatically, so a kill -9 or a reboot costs one attempt, not a
stuck row. `attempts >= max_attempts` → `state='failed'` with `error` set, plus an `app_alerts`
row so it surfaces instead of vanishing.

### Freshness — "stale, never wrong"

The worker owns job state in SQLite. For live UI it does **one** extra thing: a loopback
`POST http://127.0.0.1:8080/internal/jobs/poke` with a worker token, which fires the existing
SSE "poke then re-GET fragment". If that POST fails, **nothing breaks** — the page's htmx poll
(5 s while any job is active for the visible song, off otherwise) catches up. Stale for a few
seconds, never wrong. The guest app is not involved at all.

### launchd

A third plist beside `com.cr8.cr8-owner` / `-guest`:

```
Label            com.cr8.cr8-stems-worker
ProgramArguments .venv/bin/crate stems worker --drain
StartInterval    120            (or WatchPaths on a queue sentinel for instant pickup)
ProcessType      Background     (macOS deprioritises CPU + I/O automatically)
Nice             10
LowPriorityIO    true
```

`ProcessType Background` is the important one — it is what stops a 30-hour backfill from making
Ableton unusable. `--drain` processes until the queue is empty, then exits; combined with
`StartInterval` that gives a simple, restartable loop with no long-lived daemon. Add a
`stems/.paused` sentinel file the worker checks between jobs, and a UI toggle for it, so the owner
can reclaim the machine for a session with one tap.

### `cr8 stems` CLI

```
cr8 stems <song|bounce-ulid>       # enqueue (priority 100); --wait to run inline
cr8 stems --all [--missing]        # backfill the catalog (priority 0)
cr8 stems --recipe hq-v1 <target>  # BS-RoFormer re-run
cr8 stems status                   # queue depth, running job, failures, ETA
cr8 stems worker --drain           # what launchd calls
cr8 stems worker --once            # one job, for tests
cr8 stems clean                    # sweep stems/.work/*, reclaim expired leases
```

Consistent with the existing verb set (`scan`, `build`, `push`, `detect`, `scrub`).

### Surviving interruption — the concrete rules

The mirror's idiom is already right; reuse it verbatim.

1. **Scratch, then promote.** All separator output goes to `stems/.work/<job-ulid>/`. Nothing
   under `stems/<bounce-ulid>/` is touched until a pass fully succeeds.
2. **Atomic per file.** `os.replace(work/vocals.flac, stems/<ulid>/vocals.flac)` — same-filesystem
   rename, atomic. This is exactly `cr8/mirror.py:592-605` and `:644-666`.
3. **Manifest last.** `manifest.json` is written after all five stems land. Its presence is the
   on-disk "this run is complete" marker; a directory without one is a partial and gets redone.
4. **DB flip last of all,** in one transaction: insert `stems` rows, set `stem_runs.ok=1`,
   `jobs.state='done'`.
5. **Pass-level resumability.** `stem_runs.pass_a_done` / `pass_b_done` mean a job that dies during
   pass B does not redo the ten minutes of pass A. On resume, verify the promoted pass-A files
   against `archive_sha256` before trusting the flag.
6. **Sweep on start.** Delete `stems/.work/*` older than 24 h — mirrors `_sweep_temporary_files`.
7. **Staleness, not deletion.** `stem_runs.src_sha256` is the source digest at compute time. If the
   bounce's source changes, the stems are *stale*, and the UI says so. Never auto-delete
   twenty minutes of compute because a file's mtime moved.
8. **Never partially visible.** A stem is playable only once its row exists, and rows only exist
   after promotion. There is no window where the UI offers a half-written FLAC.
9. **mp3 rendition is a separate, cheap step** (existing ffmpeg + audiowaveform path). If it fails,
   the archival stem still exists and the rendition retries next `cr8 build` — a stem with
   `mirror_relpath IS NULL` renders as "processing", not as a broken player.

---

## 10. Local vs `hnclawbot` over Tailscale

**Data movement is not the deciding factor.** A 3-minute WAV is ~30 MB out and five FLACs are
~65 MB back — seconds on a tailnet. The interesting costs are elsewhere.

**Local (this M1 Max, 64 GB) — recommended for v1.**
- Zero new moving parts. Corpus, `stems/`, `mirror/`, `catalog.db` are all already here.
- The real cost: this is a **studio machine**. A 30-hour backfill on 8 P-cores and the GPU will be
  felt in Ableton. `ProcessType Background` + `Nice 10` + `LowPriorityIO` + `OMP_NUM_THREADS=6` +
  a pause sentinel makes it tolerable; it does not make it invisible. Per-song on-demand runs
  (< 3 min, one at a time) are a non-issue — it is only the backfill that hurts.

**`hnclawbot` over Tailscale — the upgrade, later.**
- The win is real and it is exactly the one that matters: separation stops competing with music
  production.
- Against it: (a) a spare MacBook is very likely **slower than an M1 Max** — if it is a fanless
  Air, sustained thermal throttling over a 30-hour queue could make it net slower, so the "offload"
  buys quiet, not speed; (b) it has to stay awake — `caffeinate -dimsu`, clamshell on power, and a
  `pmset` review; (c) two venvs, two model dirs, two failure modes, and a second machine that has
  to be up for the button to do anything; (d) **the queue lives in `catalog.db` on this Mac.** A
  remote worker must NOT reach it over a network filesystem — SQLite over SMB/NFS is a
  well-known corruption class, and this database is the catalog.
- (d) is the design constraint worth respecting **now**: it forces the worker to talk to the queue
  over **HTTP**, not over the filesystem.

**Recommendation: build local-first, but make the worker's queue access an HTTP client against
`http://127.0.0.1:8080/internal/jobs/*` with a worker token** — claim, heartbeat, complete, and
upload results. Locally that is a loopback call costing microseconds. Moving to `hnclawbot` then
becomes: install the venv + models there, point `CRATE_QUEUE_URL` at
`http://studio-mac:8080`, and let the worker fetch source audio and POST stems back over the same
authenticated channel. **A config change, not a rewrite.** Do it only when (1) the DAW disruption
is actually observed, and (2) `hnclawbot`'s chip is M2 Pro or better. Cost is $0 either way — the
currency being spent is this Mac's responsiveness, not dollars.

---

## 11. UI surfacing

**Song detail page**, below the version rail (per the ratified B-vault-pole reference):

- Section header microlabel `STEMS`, uppercase at +0.055em, IBM Plex Mono — per the ratification.
- Five rows, each a real `<button>` with a 44 px hit area (never a bare div — SPEC-dig §3), each
  entering the **same `playQueue`** as any bounce. Label in mono: `VOCALS` / `INSTRUMENTAL` /
  `DRUMS` / `BASS` / `OTHER`, with duration.
- Real stem bounces (the 12 songs with `mixrole` in `vox`/`novox`/`inst`/`acap`/`bass`/`gtar`)
  render **above** the separated ones and are visually distinguished — a mono `SOURCE` tag vs a
  mono `SEPARATED` tag. Never let a model's guess masquerade as the owner's own export.
- Empty state teaches the loop, in the app's voice: *"no stems yet — separate this and you get
  vocals, instrumental, drums, bass, other. About three minutes."*
- The action is one owner-only button: `SEPARATE`. POST, commits in milliseconds, returns the lit
  `QUEUED` chip — the existing commit-ack discipline, unchanged.
- States on the button: `SEPARATE` → `QUEUED` → `SEPARATING · PASS 2 OF 2` → the stem rows appear.
  htmx polls every 5 s **only while a job is active for this song**, SSE poke when available.
- `STALE` badge when `stem_runs.src_sha256` no longer matches the source, with a re-run action.

**Banlist check:** a per-job progress indicator is a *job status*, not a guilt dashboard. The
ratification bans **global percent-complete**. So: never render "38% of your catalog has stems"
anywhere. A queue-depth line in `cr8 stems status` on the CLI is fine; on the web surface, show
only the job in front of you.

**Shares.** Add `include_stems INTEGER NOT NULL DEFAULT 0` to `shares`. Guest access to a stem
requires **both** its parent bounce ULID present in the share's snapshotted `scope_json` **and**
`include_stems=1` — checked before every byte, in the guest app's own scope path, exactly as
today. This keeps `scope_json` a snapshotted bounce-ULID list (so no re-minting, no scope drift)
while letting stems ride along. Default off: a share is about songs; stems are opt-in per share.
Shuffle and DIG operate over bounces only — **stems are never queue-eligible on the guest origin
unless explicitly in scope**, which preserves SPEC-dig's "shuffle and dig NEVER escape scope"
acceptance test unchanged.

---

## 12. Acceptance criteria worth writing into the spec

1. `--env_info` reports `CoreMLExecutionProvider available` on this host (else the timing model is
   wrong and everything downstream should be re-estimated).
2. One real bounce through pass A and pass B, timed, with `PYTORCH_ENABLE_MPS_FALLBACK=1` — this
   is the measurement that turns §6's estimates into facts, and the gate on whether pass B stays
   on audio-separator's Demucs.
3. `kill -9` the worker mid-pass-B → next drain resumes without redoing pass A; no partial file
   under `stems/<ulid>/`; no `stems` row for the incomplete run.
4. Double-POST the separate button → exactly one `jobs` row (the partial unique index).
5. Guest with a share where `include_stems=0` gets 403/404 on a stem ULID whose parent bounce **is**
   in scope.
6. `cr8 build --force-shrink` does not delete anything under `stems/`.
7. Import audit still passes: `crate.web.guest` imports zero owner routes; the worker module is
   imported by neither web app.
8. `pip-audit` on cr8's venv is unchanged — the stems venv is out of scope by construction, and
   a test should assert `torch` is not importable from `.venv`.

---

## 13. Flagged — could not verify

- **htdemucs on MPS through audio-separator's vendored UVR fork.** Conflicting public claims
  (§5). Not resolvable from documentation; needs one local run. This is the single largest
  uncertainty in the wall-clock estimate, and the only one that could change a design decision
  (pass B engine).
- **M1 Max scaling factor.** The 1.3–1.8× figure is a general-purpose extrapolation from M3 Max,
  not a measurement of these models. Every "× 652" number in §6 inherits that error bar.
- **StemRoller's 2026 maintenance status and Apple Silicon build.** Search returned only
  aggregator pages. Does not affect the verdict.
- **`mlx-audio-separator` weight-cache location and RAM figures** are undocumented in its README.
  Irrelevant unless it is adopted.
- **audio-separator issue #293 severity.** I established that the pinned `samplerate==0.1.0` has
  only an x86_64 dylib, and separately that audio-separator's source never imports it. I could not
  determine what the reporter's traceback path actually was (likely a transitive lazy import).
  The one-line pin-override in §3 makes the question moot.
- **`--normalization 1.0` semantics.** Read as "peak ceiling", so 1.0 should be a no-op, but I did
  not trace `normalize()` end to end. Worth a null-test: separate a file, sum the stems, compare
  peak against the source.
- **`hnclawbot`'s actual chip, RAM, and tailnet status** — the machine was not reachable from here
  (`tailscale status` produced no output on this host). §10's recommendation is conditional on it.

---

## Sources

- [nomadkaraoke/python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) · [PyPI](https://pypi.org/project/audio-separator/) · [README](https://github.com/nomadkaraoke/python-audio-separator/blob/main/README.md) · [model recommendations + M3 Max timings (discussion #133)](https://github.com/nomadkaraoke/python-audio-separator/discussions/133) · [issue #293, samplerate arm64](https://github.com/nomadkaraoke/python-audio-separator/issues/293)
- [UVR model catalog `download_checks.json`](https://raw.githubusercontent.com/TRvlvr/application_data/main/filelists/download_checks.json) · [Anjok07/ultimatevocalremovergui](https://github.com/Anjok07/ultimatevocalremovergui)
- [ssmall256/mlx-audio-separator](https://github.com/ssmall256/mlx-audio-separator) · [PyPI](https://pypi.org/project/mlx-audio-separator/) · [ssmall256/demucs-mlx](https://github.com/ssmall256/demucs-mlx/)
- [facebookresearch/demucs (archived)](https://github.com/facebookresearch/demucs) · [adefossez/demucs (maintained fork)](https://github.com/adefossez/demucs)
- [Mel-Band RoFormer, arXiv:2310.01809](https://arxiv.org/abs/2310.01809) · [BS-RoPE Transformer, arXiv:2309.02612](https://arxiv.org/pdf/2309.02612) · [lucidrains/BS-RoFormer](https://github.com/lucidrains/BS-RoFormer) · [ZFTurbo/Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training)
- [JusperLee/Apollo](https://github.com/JusperLee/Apollo) · [arXiv:2409.08514](https://arxiv.org/abs/2409.08514)
- [samplerate on PyPI](https://pypi.org/project/samplerate/) · [torch on PyPI](https://pypi.org/project/torch/)
- Local inspection: `/Applications/Ultimate Vocal Remover.app` PyInstaller CArchive TOC; `.//{cr8/,specs/,canon/,config.toml}`; read-only copy of `catalog.db`.
