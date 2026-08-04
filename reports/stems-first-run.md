# Stems — first real run (measured, not estimated)

Date: 2026-07-29 · Host: M1 Max, 64 GB · audio-separator 0.44.5 · torch 2.13.0 · ORT 1.28.0

## Acceleration confirmed active
- Torch device: **MPS** ("Apple Silicon MPS/CoreML is available… setting Torch device to MPS")
- ONNX Runtime: **CoreMLExecutionProvider enabled**

## Pass A — MDX-Net (vocals + instrumental)
- Model: `UVR-MDX-NET-Inst_HQ_5.onnx` (vendored to `models/uvr/`, from the installed UVR app)
- Source: Stayhere, mirror mp3-320, 185.8 s
- **Separation: 50 s** (wall clock incl. process start: 61.9 s) → ~0.27× realtime
- Outputs: `(Vocals)` + `(Instrumental)` FLAC, both **184.006531 s** — duration match to source
- Peak CPU ~190%, RAM non-issue

### Versus the research estimate
Report projected ~55 s/track for pass A on this machine. **Measured 50 s.** Estimate holds.

Catalog-wide pass A extrapolation: 652 bounces × 50 s ≈ **9 hours** at background priority.

## Still not measured
- `hq-v1` recipe (BS-RoFormer) — downloads on first use.

## Note on output naming
audio-separator names outputs `<input-stem>_(Vocals)_<model>.flac`. The implementer should pass
`--custom_output_names` (JSON, one argv element) to get deterministic `vocals.flac` /
`instrumental.flac` inside `stems/<bounce-ulid>/`, per SPEC-stems.md.

## Pass B — htdemucs (drums + bass + other)

- Command: `cr8 stems separate 01KYQK63W7ENA7J4XRHAPYK1Z6`
- Source: Stayhere v2 original WAV, **168.0 s** (`7-29-26-stayhere-cm-v2.wav`), not the mirror.
- Source format: 32-bit float WAV. Because FLAC cannot store FLOAT samples and
  audio-separator 0.44.5 otherwise logs an export error while exiting 0, the command made a
  scratch-only 24-bit PCM WAV for inference. The manifest retains the original path and SHA-256.
- Model: `htdemucs.yaml`, `--demucs_shifts 1`, `--demucs_overlap 0.25`.
- **Pass-B wall clock: 15.8 s**; audio-separator's own separation log: **13 s**.
- Outputs: `drums.flac`, `bass.flac`, and `other.flac`, all **168.000000 s**. The redundant
  Demucs vocal was discarded; pass A's vocal is retained.
- Acceleration log during this exact pass: **"Apple Silicon MPS/CoreML is available… setting
  Torch device to MPS."**

### CPU-fallback conclusion

**Pass B is not silently on CPU.** At 15.8 s for 168 s of audio (~0.094× realtime), it is far
faster than the report's 60–150 s estimate, not far slower than 150 s. The original CPU-fallback
concern is resolved on this M1 Max / torch 2.13.0 / audio-separator 0.44.5 combination.

Catalog-wide pass-B extrapolation at the measured wall time:
652 bounces × 15.8 s ≈ **2.9 hours** before background-priority overhead.

## Complete synchronous command result

- Pass A wall clock on the same original WAV: **37.3 s**.
- Pass B wall clock: **15.8 s**.
- Five deterministic 24-bit FLACs were duration-checked to ±0.5 s, fully decoded with ffmpeg,
  SHA-256 hashed, recorded in `manifest.json`, and atomically promoted to
  `stems/01KYQK63W7ENA7J4XRHAPYK1Z6/`.
- audio-separator sanitizes a requested `_demucs_vocals` custom name to
  `demucs_vocals.flac`; the command handles both spellings when discarding that output.
