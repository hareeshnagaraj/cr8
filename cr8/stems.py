"""Local, scratch-first archival stem separation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence, TypeVar

from .audio import (
    AudioFile,
    analysis_source,
    bounce_files,
    probe_duration,
    sha256_file,
)
from .automation import FileLock, LockBusy
from .config import Config
from .paths import is_drop, source_root
from .public_ids import new_ulid
from .tooling import find_tool, run_tool


DEFAULT_RECIPE = "default-v1"
DEFAULT_MODEL_A = "UVR-MDX-NET-Inst_HQ_5.onnx"
DEFAULT_MODEL_B = "htdemucs.yaml"
HQ_RECIPE = "hq-v1"
HQ_MODEL_A = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
STEM_KINDS = ("vocals", "instrumental", "drums", "bass", "other")
WORK_MAX_AGE = timedelta(hours=24)
JOB_LEASE_MINUTES = 45
PASS_A_CHECKPOINT = "pass-a.json"

Runner = Callable[
    ...,
    subprocess.CompletedProcess[str],
]
T = TypeVar("T")


@dataclass(frozen=True)
class ResolvedBounce:
    bounce_id: int
    bounce_ulid: str
    source: AudioFile


@dataclass(frozen=True)
class SeparationResult:
    bounce_ulid: str
    output_dir: Path
    manifest: dict[str, Any]
    created: bool
    pass_a_seconds: float
    pass_b_seconds: float


@dataclass(frozen=True)
class StemJob:
    id: int
    ulid: str
    target_id: int
    bounce_ulid: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


@dataclass(frozen=True)
class WorkerSummary:
    claimed: int
    completed: int
    retried: int
    failed: int
    paused: bool


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro",
        uri=True,
        timeout=10,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def resolve_bounce_source(
    db_path: Path,
    config: Config,
    bounce_ulid: str,
) -> ResolvedBounce:
    """Resolve one public bounce ID and close SQLite before inference starts."""
    connection = _read_only_connection(db_path)
    try:
        row = connection.execute(
            "SELECT id, public_id FROM bounces WHERE public_id=?",
            (bounce_ulid,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown bounce ULID: {bounce_ulid}")
        bounce_id = int(row["id"])
        source = analysis_source(bounce_files(connection, config, bounce_id))
    finally:
        connection.close()
    if source is None:
        raise ValueError(f"bounce has no readable original: {bounce_ulid}")
    if is_drop(source.relpath):
        raise ValueError("bounce source escapes the configured corpus")
    corpus_root = source_root(config, source.relpath).resolve(strict=True)
    source_path = source.path.resolve(strict=True)
    if not source_path.is_relative_to(corpus_root):
        raise ValueError("bounce source escapes the configured corpus")
    return ResolvedBounce(
        bounce_id=bounce_id,
        bounce_ulid=str(row["public_id"]),
        source=source,
    )


def _separator_environment() -> dict[str, str]:
    home = str(Path.home())
    return {
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "HOME": home,
        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "OMP_NUM_THREADS": "6",
    }


def _required_paths(config: Config) -> tuple[Path, Path]:
    separator = config.state_dir / ".venv-stems" / "bin" / "audio-separator"
    models = config.state_dir / "models" / "uvr"
    missing = [
        path
        for path in (
            separator,
            models / DEFAULT_MODEL_A,
            models / DEFAULT_MODEL_B,
            models / "955717e8-8726e21a.th",
            models / "download_checks.json",
            models / "mdx_model_data" / "model_data.json",
        )
        if not path.is_file()
    ]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise ValueError(f"stem separation assets are missing: {names}")
    if not os.access(separator, os.X_OK):
        raise ValueError(f"audio-separator is not executable: {separator}")
    return separator, models


def _sweep_work(stems_root: Path, *, now: datetime | None = None) -> int:
    work_root = stems_root / ".work"
    work_root.mkdir(parents=True, exist_ok=True)
    cutoff = (now or datetime.now(UTC)).timestamp() - WORK_MAX_AGE.total_seconds()
    swept = 0
    for path in work_root.iterdir():
        try:
            stale = path.stat().st_mtime < cutoff
        except OSError:
            continue
        if not stale:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        swept += 1
    return swept


def _separator_version(
    separator: Path,
    *,
    runner: Runner,
    env: Mapping[str, str],
) -> str:
    result = runner(separator, ("--version",), timeout=30, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot read audio-separator version: {result.stderr.strip()}"
        )
    version = result.stdout.strip()
    if not version:
        raise RuntimeError("audio-separator returned an empty version")
    return version


def _pass_args(
    source_path: Path,
    models: Path,
    work_dir: Path,
    *,
    model: str,
) -> tuple[str | Path, ...]:
    common: list[str | Path] = [
        source_path,
        "--model_file_dir",
        models,
        "--model_filename",
        model,
        "--output_dir",
        work_dir,
        "--output_format",
        "FLAC",
        "--use_soundfile",
        "--normalization",
        "1.0",
    ]
    if model in {DEFAULT_MODEL_A, HQ_MODEL_A}:
        common.extend(
            (
                "--custom_output_names",
                json.dumps(
                    {"Vocals": "vocals", "Instrumental": "instrumental"},
                    separators=(",", ":"),
                ),
            )
        )
        if model == DEFAULT_MODEL_A:
            common.extend(
                (
                    "--mdx_segment_size",
                    "256",
                    "--mdx_overlap",
                    "0.25",
                    "--mdx_batch_size",
                    "1",
                )
            )
    else:
        common.extend(
            (
                "--custom_output_names",
                json.dumps(
                    {
                        "Drums": "drums",
                        "Bass": "bass",
                        "Other": "other",
                        "Vocals": "_demucs_vocals",
                    },
                    separators=(",", ":"),
                ),
                "--demucs_shifts",
                "1",
                "--demucs_overlap",
                "0.25",
                "--demucs_segment_size",
                "Default",
            )
        )
    common.extend(("--log_level", "info"))
    return tuple(common)


def _run_pass(
    separator: Path,
    args: Sequence[str | Path],
    *,
    timeout: float,
    runner: Runner,
    env: Mapping[str, str],
    log_path: Path,
) -> float:
    started = time.monotonic()
    result = runner(separator, args, timeout=timeout, env=env)
    elapsed = time.monotonic() - started
    _write_pass_log(log_path, result)
    if result.returncode != 0:
        model = args[args.index("--model_filename") + 1]
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(
            f"audio-separator failed for {model} "
            f"(exit {result.returncode}): {detail}"
        )
    return elapsed


def _write_pass_log(
    path: Path,
    result: subprocess.CompletedProcess[str],
) -> None:
    path.write_text(
        f"argv: {json.dumps([str(value) for value in result.args])}\n"
        f"returncode: {result.returncode}\n"
        "\n[stdout]\n"
        f"{result.stdout}"
        "\n[stderr]\n"
        f"{result.stderr}",
        encoding="utf-8",
    )


def _separator_input(
    source_path: Path,
    work_dir: Path,
    *,
    ffmpeg: Path,
    ffprobe: Path,
    runner: Runner,
    timeout: float,
) -> tuple[Path, str | None]:
    """Adapt libsndfile-incompatible WAV subtypes without using a lossy mirror."""
    probed = runner(
        ffprobe,
        (
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            source_path,
        ),
        timeout=60,
    )
    codec = probed.stdout.strip().casefold() if probed.returncode == 0 else ""
    if not (codec.startswith("pcm_f") or codec.startswith("pcm_s32")):
        return source_path, None
    converted = work_dir / "_separator_input.wav"
    result = runner(
        ffmpeg,
        (
            "-v",
            "error",
            "-y",
            "-i",
            source_path,
            "-map_metadata",
            "-1",
            "-vn",
            "-codec:a",
            "pcm_s24le",
            converted,
        ),
        timeout=timeout,
    )
    if result.returncode != 0 or not converted.is_file():
        raise RuntimeError(
            "cannot prepare 24-bit separator input: "
            f"{result.stderr.strip()}"
        )
    return converted, f"{codec} to pcm_s24le working copy"


def _verify_stem(
    path: Path,
    *,
    source_duration: float,
    ffmpeg: Path,
    state_dir: Path,
    runner: Runner,
) -> float:
    if not path.is_file():
        raise RuntimeError(f"separator did not create {path.name}")
    duration = probe_duration(path, state_dir=state_dir)
    if duration is None:
        raise RuntimeError(f"cannot probe stem duration: {path}")
    if abs(source_duration - duration) > 0.5:
        raise RuntimeError(
            f"duration mismatch for {path.name}: "
            f"source={source_duration:.3f}s stem={duration:.3f}s"
        )
    decoded = runner(
        ffmpeg,
        ("-v", "error", "-i", path, "-f", "null", "-"),
        timeout=120 + source_duration * 4.0,
    )
    if decoded.returncode != 0:
        raise RuntimeError(
            f"decode check failed for {path.name}: {decoded.stderr.strip()}"
        )
    return duration


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read stem manifest {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("manifest_version") != 1:
        raise ValueError(f"unsupported stem manifest: {path}")
    files = value.get("files")
    if not isinstance(files, dict) or set(files) != set(STEM_KINDS):
        raise ValueError(f"stem manifest has incomplete files: {path}")
    return value


def _verify_existing(
    final_dir: Path,
    *,
    bounce: ResolvedBounce,
    recipe: str,
    source_sha256: str,
) -> dict[str, Any]:
    manifest = load_manifest(final_dir / "manifest.json")
    if manifest.get("bounce_ulid") != bounce.bounce_ulid:
        raise ValueError("existing stem manifest belongs to another bounce")
    if manifest.get("recipe") != recipe:
        raise ValueError("existing stem manifest uses a different recipe")
    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("sha256") != source_sha256:
        raise ValueError("existing stems are stale for the current source")
    for kind, metadata in manifest["files"].items():
        if not isinstance(metadata, dict):
            raise ValueError(f"invalid manifest entry for {kind}")
        path = final_dir / str(metadata.get("filename", ""))
        if path.name != f"{kind}.flac" or not path.is_file():
            raise ValueError(f"existing stem is missing: {kind}")
        if sha256_file(path) != metadata.get("sha256"):
            raise ValueError(f"existing stem hash mismatch: {kind}")
    return manifest


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _ensure_hq_model(
    separator: Path,
    models: Path,
    *,
    runner: Runner,
    env: Mapping[str, str],
) -> None:
    target = models / HQ_MODEL_A
    if target.is_file():
        return
    result = runner(
        separator,
        (
            "--download_model_only",
            "--model_file_dir",
            models,
            "--model_filename",
            HQ_MODEL_A,
        ),
        timeout=900,
        env=env,
    )
    if result.returncode != 0 or not target.is_file():
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(f"cannot download high-quality stem model: {detail}")


def _write_pass_a_checkpoint(
    work_dir: Path,
    *,
    source_sha256: str,
    source_duration: float,
    model: str,
    elapsed: float,
    ffmpeg: Path,
    state_dir: Path,
    runner: Runner,
) -> None:
    files: dict[str, dict[str, Any]] = {}
    for kind in ("vocals", "instrumental"):
        path = work_dir / f"{kind}.flac"
        duration = _verify_stem(
            path,
            source_duration=source_duration,
            ffmpeg=ffmpeg,
            state_dir=state_dir,
            runner=runner,
        )
        files[kind] = {
            "sha256": sha256_file(path),
            "duration_s": duration,
        }
    _write_manifest(
        work_dir / PASS_A_CHECKPOINT,
        {
            "checkpoint_version": 1,
            "source_sha256": source_sha256,
            "model": model,
            "elapsed_s": round(elapsed, 3),
            "files": files,
        },
    )


def _resume_pass_a(
    work_dir: Path,
    *,
    source_sha256: str,
    source_duration: float,
    model: str,
    ffmpeg: Path,
    state_dir: Path,
    runner: Runner,
) -> float | None:
    checkpoint_path = work_dir / PASS_A_CHECKPOINT
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (
            checkpoint.get("checkpoint_version") != 1
            or checkpoint.get("source_sha256") != source_sha256
            or checkpoint.get("model") != model
        ):
            return None
        files = checkpoint["files"]
        for kind in ("vocals", "instrumental"):
            path = work_dir / f"{kind}.flac"
            if sha256_file(path) != files[kind]["sha256"]:
                return None
            _verify_stem(
                path,
                source_duration=source_duration,
                ffmpeg=ffmpeg,
                state_dir=state_dir,
                runner=runner,
            )
        return float(checkpoint.get("elapsed_s", 0.0))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _existing_result(
    final_dir: Path,
    *,
    bounce: ResolvedBounce,
    recipe: str,
    source_sha256: str,
) -> SeparationResult | None:
    try:
        manifest = _verify_existing(
            final_dir,
            bounce=bounce,
            recipe=recipe,
            source_sha256=source_sha256,
        )
    except ValueError:
        manifest = load_manifest(final_dir / "manifest.json")
        source = manifest.get("source")
        if (
            manifest.get("bounce_ulid") == bounce.bounce_ulid
            and manifest.get("recipe") == recipe
            and isinstance(source, dict)
            and source.get("sha256") != source_sha256
        ):
            return None
        raise
    timings = manifest.get("timings_s", {})
    return SeparationResult(
        bounce_ulid=bounce.bounce_ulid,
        output_dir=final_dir,
        manifest=manifest,
        created=False,
        pass_a_seconds=float(timings.get("pass_a", 0.0)),
        pass_b_seconds=float(timings.get("pass_b", 0.0)),
    )


def separate_resolved_bounce(
    config: Config,
    bounce: ResolvedBounce,
    *,
    recipe: str = DEFAULT_RECIPE,
    job_ulid: str | None = None,
    runner: Runner | None = None,
) -> SeparationResult:
    """Run both default passes with no database connection in scope."""
    if recipe not in {DEFAULT_RECIPE, HQ_RECIPE}:
        raise ValueError(f"unsupported synchronous recipe: {recipe}")
    run = runner or run_tool
    separator, models = _required_paths(config)
    ffmpeg = find_tool("ffmpeg", state_dir=config.state_dir)
    ffprobe = find_tool("ffprobe", state_dir=config.state_dir)
    if ffmpeg is None or ffprobe is None:
        missing = [
            name
            for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe))
            if path is None
        ]
        raise ValueError(f"stem verification requires: {', '.join(missing)}")
    source_duration = bounce.source.duration_s or probe_duration(
        bounce.source.path,
        state_dir=config.state_dir,
    )
    if source_duration is None:
        raise ValueError(f"cannot determine source duration: {bounce.source.relpath}")
    source_sha256 = sha256_file(bounce.source.path)
    stems_root = config.state_dir / "stems"
    stems_root.mkdir(parents=True, exist_ok=True)
    _sweep_work(stems_root)
    if recipe == HQ_RECIPE and not (
        stems_root / bounce.bounce_ulid / "manifest.json"
    ).is_file():
        raise ValueError("hq-v1 requires a completed default-v1 archive first")
    primary_dir = (
        stems_root / bounce.bounce_ulid
        if recipe == DEFAULT_RECIPE
        else stems_root / bounce.bounce_ulid / recipe
    )
    final_dir = primary_dir
    if primary_dir.exists():
        existing = _existing_result(
            primary_dir,
            bounce=bounce,
            recipe=recipe,
            source_sha256=source_sha256,
        )
        if existing is not None:
            return existing
        final_dir = (
            stems_root
            / bounce.bounce_ulid
            / "reruns"
            / f"{recipe}-{source_sha256[:12]}"
        )
        if final_dir.exists():
            existing = _existing_result(
                final_dir,
                bounce=bounce,
                recipe=recipe,
                source_sha256=source_sha256,
            )
            if existing is not None:
                return existing
    env = _separator_environment()
    model_a = DEFAULT_MODEL_A if recipe == DEFAULT_RECIPE else HQ_MODEL_A
    if recipe == HQ_RECIPE:
        _ensure_hq_model(separator, models, runner=run, env=env)
    work_dir = stems_root / ".work" / (job_ulid or new_ulid())
    if work_dir.exists():
        pass_a = _resume_pass_a(
            work_dir,
            source_sha256=source_sha256,
            source_duration=float(source_duration),
            model=model_a,
            ffmpeg=ffmpeg,
            state_dir=config.state_dir,
            runner=run,
        )
        if pass_a is None:
            shutil.rmtree(work_dir)
            work_dir.mkdir(parents=False)
        else:
            for name in (
                "drums.flac",
                "bass.flac",
                "other.flac",
                "_demucs_vocals.flac",
                "demucs_vocals.flac",
                "pass-b.log",
            ):
                (work_dir / name).unlink(missing_ok=True)
    else:
        work_dir.mkdir(parents=False)
        pass_a = None
    separator_version = _separator_version(separator, runner=run, env=env)
    timeout = 120 + source_duration * 4.0
    separator_input, input_conversion = _separator_input(
        bounce.source.path,
        work_dir,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        runner=run,
        timeout=timeout,
    )
    if pass_a is None:
        pass_a = _run_pass(
            separator,
            _pass_args(
                separator_input,
                models,
                work_dir,
                model=model_a,
            ),
            timeout=timeout,
            runner=run,
            env=env,
            log_path=work_dir / "pass-a.log",
        )
        _write_pass_a_checkpoint(
            work_dir,
            source_sha256=source_sha256,
            source_duration=float(source_duration),
            model=model_a,
            elapsed=pass_a,
            ffmpeg=ffmpeg,
            state_dir=config.state_dir,
            runner=run,
        )
    pass_b = _run_pass(
        separator,
        _pass_args(
            separator_input,
            models,
            work_dir,
            model=DEFAULT_MODEL_B,
        ),
        timeout=timeout,
        runner=run,
        env=env,
        log_path=work_dir / "pass-b.log",
    )
    for discarded_vocal in (
        "_demucs_vocals.flac",
        "demucs_vocals.flac",
    ):
        (work_dir / discarded_vocal).unlink(missing_ok=True)
    (work_dir / "_separator_input.wav").unlink(missing_ok=True)
    files: dict[str, dict[str, Any]] = {}
    for kind in STEM_KINDS:
        path = work_dir / f"{kind}.flac"
        duration = _verify_stem(
            path,
            source_duration=float(source_duration),
            ffmpeg=ffmpeg,
            state_dir=config.state_dir,
            runner=run,
        )
        files[kind] = {
            "filename": path.name,
            "sha256": sha256_file(path),
            "duration_s": duration,
        }
    unexpected = sorted(
        path.name
        for path in work_dir.glob("*.flac")
        if path.stem not in STEM_KINDS
    )
    if unexpected:
        raise RuntimeError(
            f"separator created unexpected FLAC output: {', '.join(unexpected)}"
        )
    manifest: dict[str, Any] = {
        "manifest_version": 1,
        "bounce_ulid": bounce.bounce_ulid,
        "recipe": recipe,
        "models": {"pass_a": model_a, "pass_b": DEFAULT_MODEL_B},
        "separator_version": separator_version,
        "source": {
            "relpath": bounce.source.relpath,
            "sha256": source_sha256,
            "duration_s": float(source_duration),
            "input_conversion": input_conversion,
        },
        "timings_s": {
            "pass_a": round(pass_a, 3),
            "pass_b": round(pass_b, 3),
        },
        "files": files,
    }
    for log_name in ("pass-a.log", "pass-b.log"):
        (work_dir / log_name).unlink(missing_ok=True)
    (work_dir / PASS_A_CHECKPOINT).unlink(missing_ok=True)
    _write_manifest(work_dir / "manifest.json", manifest)
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(work_dir, final_dir)
    return SeparationResult(
        bounce_ulid=bounce.bounce_ulid,
        output_dir=final_dir,
        manifest=manifest,
        created=True,
        pass_a_seconds=pass_a,
        pass_b_seconds=pass_b,
    )


def separate_bounce(
    db_path: Path,
    config: Config,
    bounce_ulid: str,
    *,
    recipe: str = DEFAULT_RECIPE,
    job_ulid: str | None = None,
    runner: Runner | None = None,
) -> SeparationResult:
    bounce = resolve_bounce_source(db_path, config, bounce_ulid)
    result = separate_resolved_bounce(
        config,
        bounce,
        recipe=recipe,
        job_ulid=job_ulid,
        runner=runner,
    )
    catalog_separation(db_path, config, bounce, result)
    return result


def catalog_separation(
    db_path: Path,
    config: Config,
    bounce: ResolvedBounce,
    result: SeparationResult,
) -> None:
    """Catalog one verified archive after inference has released SQLite."""
    final_dir = result.output_dir.resolve(strict=True)
    stems_root = (config.state_dir / "stems").resolve(strict=True)
    recipe = str(result.manifest["recipe"])
    primary_dir = (
        stems_root / bounce.bounce_ulid
        if recipe == DEFAULT_RECIPE
        else stems_root / bounce.bounce_ulid / recipe
    )
    reruns_dir = stems_root / bounce.bounce_ulid / "reruns"
    valid_rerun = (
        final_dir.parent == reruns_dir
        and final_dir.name.startswith(f"{recipe}-")
    )
    if final_dir != primary_dir and not valid_rerun:
        raise ValueError("stem archive is outside the expected bounce directory")
    manifest = _verify_existing(
        final_dir,
        bounce=bounce,
        recipe=recipe,
        source_sha256=str(result.manifest["source"]["sha256"]),
    )
    source = manifest["source"]
    models = manifest["models"]
    now = datetime.now(UTC).replace(microsecond=0).isoformat()

    connection = sqlite3.connect(
        db_path,
        timeout=10,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id FROM bounces WHERE id=? AND public_id=?",
            (bounce.bounce_id, bounce.bounce_ulid),
        ).fetchone()
        if row is None:
            raise ValueError(f"bounce changed while separating: {bounce.bounce_ulid}")
        connection.execute(
            """
            UPDATE files SET sha256=?
            WHERE bounce_id=? AND relpath=?
            """,
            (source["sha256"], bounce.bounce_id, source["relpath"]),
        )
        connection.execute(
            """
            INSERT INTO stem_runs(
              bounce_id, recipe, model_a, model_b, pass_a_done, pass_b_done,
              src_relpath, src_sha256, separator_version,
              started_at, finished_at, ok
            ) VALUES(?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(bounce_id, recipe) DO UPDATE SET
              model_a=excluded.model_a,
              model_b=excluded.model_b,
              pass_a_done=1,
              pass_b_done=1,
              src_relpath=excluded.src_relpath,
              src_sha256=excluded.src_sha256,
              separator_version=excluded.separator_version,
              finished_at=excluded.finished_at,
              ok=1
            """,
            (
                bounce.bounce_id,
                manifest["recipe"],
                models["pass_a"],
                models.get("pass_b"),
                source["relpath"],
                source["sha256"],
                manifest["separator_version"],
                now,
                now,
            ),
        )
        run_id = int(
            connection.execute(
                "SELECT id FROM stem_runs WHERE bounce_id=? AND recipe=?",
                (bounce.bounce_id, manifest["recipe"]),
            ).fetchone()["id"]
        )
        for kind in STEM_KINDS:
            metadata = manifest["files"][kind]
            archive = final_dir / str(metadata["filename"])
            # Relative to the stems root, then re-prefixed - not relative to
            # the app directory. Those are the same string only when stems/ is
            # a real directory inside the app, which is true on the laptop and
            # false on the server, where it is a symlink onto the data volume.
            # Resolving the symlink puts the finished file outside the app
            # directory entirely, so relative_to raised after the separation
            # had already run, and every job failed two minutes in with its
            # output sitting on disk.
            #
            # The stored form is unchanged: "stems/<bounce>/<kind>.flac", which
            # is what mirror.py and downloads.py already expect.
            archive_relpath = (
                Path("stems") / archive.relative_to(stems_root)
            ).as_posix()
            existing = connection.execute(
                "SELECT public_id FROM stems WHERE run_id=? AND kind=?",
                (run_id, kind),
            ).fetchone()
            public_id = (
                str(existing["public_id"]) if existing is not None else new_ulid()
            )
            connection.execute(
                """
                INSERT INTO stems(
                  public_id, run_id, bounce_id, kind, archive_relpath,
                  archive_sha256, mirror_relpath, duration_s, built_at
                ) VALUES(?, ?, ?, ?, ?, ?, NULL, ?, NULL)
                ON CONFLICT(run_id, kind) DO UPDATE SET
                  bounce_id=excluded.bounce_id,
                  archive_relpath=excluded.archive_relpath,
                  archive_sha256=excluded.archive_sha256,
                  duration_s=excluded.duration_s,
                  mirror_relpath=CASE
                    WHEN stems.archive_sha256=excluded.archive_sha256
                     AND stems.archive_relpath=excluded.archive_relpath
                    THEN stems.mirror_relpath ELSE NULL END,
                  built_at=CASE
                    WHEN stems.archive_sha256=excluded.archive_sha256
                     AND stems.archive_relpath=excluded.archive_relpath
                    THEN stems.built_at ELSE NULL END
                """,
                (
                    public_id,
                    run_id,
                    bounce.bounce_id,
                    kind,
                    archive_relpath,
                    metadata["sha256"],
                    float(metadata["duration_s"]),
                ),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _job_mutation(
    db_path: Path,
    operation: Callable[[sqlite3.Connection], T],
    *,
    attempts: int = 3,
) -> T:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(attempts):
        connection = sqlite3.connect(
            db_path,
            timeout=10,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            value = operation(connection)
            connection.commit()
            return value
        except sqlite3.OperationalError as exc:
            connection.rollback()
            if "locked" not in str(exc).casefold():
                raise
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.05 * (attempt + 1))
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
    assert last_error is not None, "busy retry exhausted without a lock error"
    raise RuntimeError("catalog remained busy; job mutation was not committed") from last_error


def enqueue_stem_job(
    db_path: Path,
    bounce_ulid: str,
    *,
    recipe: str = DEFAULT_RECIPE,
    priority: int = 100,
    requested_by: str = "owner",
    max_attempts: int = 3,
) -> str:
    if recipe not in {DEFAULT_RECIPE, "hq-v1"}:
        raise ValueError(f"unsupported stem recipe: {recipe}")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    payload = json.dumps({"recipe": recipe}, sort_keys=True, separators=(",", ":"))
    now = datetime.now(UTC).replace(microsecond=0).isoformat()

    def insert(connection: sqlite3.Connection) -> str:
        bounce = connection.execute(
            "SELECT id FROM bounces WHERE public_id=?",
            (bounce_ulid,),
        ).fetchone()
        if bounce is None:
            raise ValueError(f"unknown bounce ULID: {bounce_ulid}")
        target_id = int(bounce["id"])
        existing = connection.execute(
            """
            SELECT ulid FROM jobs
            WHERE kind='stems' AND target_id=?
              AND state IN ('queued','running')
            """,
            (target_id,),
        ).fetchone()
        if existing is not None:
            return str(existing["ulid"])
        job_ulid = new_ulid()
        connection.execute(
            """
            INSERT INTO jobs(
              ulid, kind, target_id, payload, state, priority,
              max_attempts, requested_by, created_at, updated_at
            ) VALUES(?, 'stems', ?, ?, 'queued', ?, ?, ?, ?, ?)
            """,
            (
                job_ulid,
                target_id,
                payload,
                priority,
                max_attempts,
                requested_by,
                now,
                now,
            ),
        )
        return job_ulid

    return _job_mutation(db_path, insert)


def enqueue_stem_jobs_for_songs(
    db_path: Path,
    song_ulids: Sequence[str],
    *,
    requested_by: str = "owner",
) -> int:
    unique_ulids = tuple(dict.fromkeys(song_ulids))
    if not unique_ulids:
        raise ValueError("select at least one song")
    now = datetime.now(UTC).replace(microsecond=0).isoformat()

    def insert(connection: sqlite3.Connection) -> int:
        placeholders = ",".join("?" for _ in unique_ulids)
        rows = connection.execute(
            f"""
            SELECT s.public_id AS song_ulid,
                   (
                     SELECT b.id FROM bounces AS b
                     JOIN mirror_files AS mf ON mf.bounce_id=b.id
                     WHERE b.song_id=s.id
                     ORDER BY COALESCE(b.bounce_date,'') DESC,
                              COALESCE(b.version,0) DESC, b.id DESC
                     LIMIT 1
                   ) AS bounce_id
            FROM songs AS s
            WHERE s.public_id IN ({placeholders})
            """,
            unique_ulids,
        ).fetchall()
        if len(rows) != len(unique_ulids) or any(
            row["bounce_id"] is None for row in rows
        ):
            raise ValueError("unknown song or song without a playable bounce")
        queued = 0
        payload = json.dumps(
            {"recipe": DEFAULT_RECIPE},
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in rows:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO jobs(
                  ulid, kind, target_id, payload, state, priority,
                  max_attempts, requested_by, created_at, updated_at
                ) VALUES(?, 'stems', ?, ?, 'queued', 0, 3, ?, ?, ?)
                """,
                (
                    new_ulid(),
                    int(row["bounce_id"]),
                    payload,
                    requested_by,
                    now,
                    now,
                ),
            )
            queued += int(connection.total_changes > before)
        return queued

    return _job_mutation(db_path, insert)


def _alert_for_job(
    connection: sqlite3.Connection,
    *,
    job_ulid: str,
    message: str,
    terminal: bool,
) -> None:
    connection.execute(
        """
        INSERT INTO app_alerts(severity, kind, message, created_at)
        VALUES(?, 'stem_job_failed', ?, ?)
        """,
        (
            "critical" if terminal else "warning",
            f"Stem job {job_ulid}: {message}"[:500],
            datetime.now(UTC).replace(microsecond=0).isoformat(),
        ),
    )


def claim_stem_job(db_path: Path, worker_id: str) -> StemJob | None:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()

    def claim(connection: sqlite3.Connection) -> StemJob | None:
        exhausted = connection.execute(
            """
            SELECT id, ulid FROM jobs
            WHERE kind='stems' AND attempts >= max_attempts
              AND (
                state='queued'
                OR (state='running' AND lease_until < datetime('now'))
              )
            ORDER BY id
            """
        ).fetchall()
        for row in exhausted:
            message = "maximum attempts exhausted"
            connection.execute(
                """
                UPDATE jobs
                SET state='failed', lease_owner=NULL, lease_until=NULL,
                    progress='failed', error=?, updated_at=?
                WHERE id=?
                """,
                (message, now, int(row["id"])),
            )
            _alert_for_job(
                connection,
                job_ulid=str(row["ulid"]),
                message=message,
                terminal=True,
            )
        row = connection.execute(
            f"""
            UPDATE jobs
            SET state='running', lease_owner=?,
                lease_until=datetime('now','+{JOB_LEASE_MINUTES} minutes'),
                attempts=attempts+1, progress='separating', error=NULL,
                updated_at=?
            WHERE id = (
              SELECT id FROM jobs
              WHERE kind='stems'
                AND attempts < max_attempts
                AND (
                  state='queued'
                  OR (state='running' AND lease_until < datetime('now'))
                )
              ORDER BY priority DESC, id
              LIMIT 1
            )
            RETURNING id, ulid, target_id, payload, attempts, max_attempts
            """,
            (worker_id, now),
        ).fetchone()
        if row is None:
            return None
        bounce = connection.execute(
            "SELECT public_id FROM bounces WHERE id=?",
            (int(row["target_id"]),),
        ).fetchone()
        if bounce is None:
            raise RuntimeError(f"stem job {row['ulid']} has no target bounce")
        try:
            payload = json.loads(str(row["payload"]))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"stem job {row['ulid']} has invalid payload") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"stem job {row['ulid']} has invalid payload")
        return StemJob(
            id=int(row["id"]),
            ulid=str(row["ulid"]),
            target_id=int(row["target_id"]),
            bounce_ulid=str(bounce["public_id"]),
            payload=payload,
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
        )

    return _job_mutation(db_path, claim)


def finish_stem_job(
    db_path: Path,
    job: StemJob,
    *,
    error: str | None = None,
) -> str:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()

    def finish(connection: sqlite3.Connection) -> str:
        owned = connection.execute(
            """
            SELECT state, lease_owner FROM jobs
            WHERE id=? AND ulid=?
            """,
            (job.id, job.ulid),
        ).fetchone()
        if owned is None or owned["state"] != "running":
            raise RuntimeError(f"stem job is no longer running: {job.ulid}")
        if error is None:
            recipe = str(job.payload.get("recipe", DEFAULT_RECIPE))
            complete = connection.execute(
                """
                SELECT sr.id,
                       (SELECT COUNT(*) FROM stems st WHERE st.run_id=sr.id) AS stems
                FROM stem_runs sr
                WHERE sr.bounce_id=? AND sr.recipe=? AND sr.ok=1
                """,
                (job.target_id, recipe),
            ).fetchone()
            if complete is None or int(complete["stems"]) != len(STEM_KINDS):
                raise RuntimeError(
                    f"separator exited without five cataloged stems: {job.ulid}"
                )
            connection.execute(
                """
                UPDATE jobs
                SET state='done', lease_owner=NULL, lease_until=NULL,
                    progress='done', error=NULL, updated_at=?
                WHERE id=?
                """,
                (now, job.id),
            )
            return "done"
        message = error.strip()[-2000:] or "stem separation failed"
        terminal = job.attempts >= job.max_attempts
        state = "failed" if terminal else "queued"
        connection.execute(
            """
            UPDATE jobs
            SET state=?, lease_owner=NULL, lease_until=NULL,
                progress=?, error=?, updated_at=?
            WHERE id=?
            """,
            (
                state,
                "failed" if terminal else "retry queued",
                message,
                now,
                job.id,
            ),
        )
        _alert_for_job(
            connection,
            job_ulid=job.ulid,
            message=message,
            terminal=terminal,
        )
        return state

    return _job_mutation(db_path, finish)


def run_stem_worker(
    db_path: Path,
    config: Config,
    *,
    config_path: Path,
    drain: bool,
    worker_id: str | None = None,
    runner: Runner | None = None,
) -> WorkerSummary:
    worker = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    run = runner or run_tool
    claimed = completed = retried = failed = 0
    paused = False
    # One separation at a time on a machine. The job leases stop two workers
    # taking the same job, but nothing stopped them taking different ones -
    # and this runs torch across every core for minutes at a stretch on the
    # box that is also serving the app. On a timer, a drain that outlives its
    # own interval would stack a new worker on top of itself every tick.
    #
    # Non-blocking on purpose: a second worker finding the lock held should
    # report nothing to do and exit, not queue up behind the first.
    try:
        lock = FileLock(config.state_dir / ".cr8-stems.lock")
        lock.__enter__()
    except LockBusy:
        return WorkerSummary(
            claimed=0, completed=0, retried=0, failed=0, paused=False
        )
    try:
        return _worker_loop(
            db_path,
            config,
            config_path=config_path,
            drain=drain,
            worker=worker,
            run=run,
        )
    finally:
        lock.__exit__()


def _worker_loop(
    db_path: Path,
    config: Config,
    *,
    config_path: Path,
    drain: bool,
    worker: str,
    run: Runner,
) -> WorkerSummary:
    claimed = completed = retried = failed = 0
    paused = False
    while True:
        if (config.state_dir / "stems" / ".paused").is_file():
            paused = True
            break
        job = claim_stem_job(db_path, worker)
        if job is None:
            break
        claimed += 1
        recipe = str(job.payload.get("recipe", DEFAULT_RECIPE))
        try:
            result = run(
                Path(sys.executable),
                (
                    "-m",
                    "cr8.cli",
                    "--config",
                    config_path.resolve(),
                    "--db",
                    db_path.resolve(),
                    "stems",
                    "separate",
                job.bounce_ulid,
                "--recipe",
                recipe,
                "--job-ulid",
                job.ulid,
            ),
                timeout=7200,
            )
        except (OSError, TimeoutError) as exc:
            outcome = finish_stem_job(db_path, job, error=str(exc))
        else:
            if result.returncode == 0:
                try:
                    outcome = finish_stem_job(db_path, job)
                except RuntimeError as exc:
                    outcome = finish_stem_job(db_path, job, error=str(exc))
            else:
                detail = (result.stderr or result.stdout).strip()
                outcome = finish_stem_job(
                    db_path,
                    job,
                    error=detail or f"separator exited {result.returncode}",
                )
        if outcome == "done":
            completed += 1
        elif outcome == "queued":
            retried += 1
        else:
            failed += 1
        if not drain:
            break
    return WorkerSummary(
        claimed=claimed,
        completed=completed,
        retried=retried,
        failed=failed,
        paused=paused,
    )


def clean_stem_jobs(db_path: Path, config: Config) -> tuple[int, int]:
    swept = _sweep_work(config.state_dir / "stems")
    now = datetime.now(UTC).replace(microsecond=0).isoformat()

    def reclaim(connection: sqlite3.Connection) -> int:
        rows = connection.execute(
            """
            SELECT id, ulid, attempts, max_attempts FROM jobs
            WHERE kind='stems' AND state='running'
              AND lease_until < datetime('now')
            """
        ).fetchall()
        for row in rows:
            terminal = int(row["attempts"]) >= int(row["max_attempts"])
            connection.execute(
                """
                UPDATE jobs
                SET state=?, lease_owner=NULL, lease_until=NULL,
                    progress=?, error=?, updated_at=?
                WHERE id=?
                """,
                (
                    "failed" if terminal else "queued",
                    "failed" if terminal else "lease reclaimed",
                    "maximum attempts exhausted" if terminal else None,
                    now,
                    int(row["id"]),
                ),
            )
            if terminal:
                _alert_for_job(
                    connection,
                    job_ulid=str(row["ulid"]),
                    message="maximum attempts exhausted",
                    terminal=True,
                )
        return len(rows)

    return swept, _job_mutation(db_path, reclaim)


def stem_job_status(db_path: Path) -> dict[str, Any]:
    connection = _read_only_connection(db_path)
    try:
        counts = {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM jobs WHERE kind='stems'
                GROUP BY state
                """
            )
        }
        running = connection.execute(
            """
            SELECT j.ulid, b.public_id AS bounce_ulid, j.progress,
                   j.attempts, j.max_attempts, j.lease_until
            FROM jobs AS j JOIN bounces AS b ON b.id=j.target_id
            WHERE j.kind='stems' AND j.state='running'
            ORDER BY j.priority DESC, j.id LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    return {
        "counts": counts,
        "running": dict(running) if running is not None else None,
    }
