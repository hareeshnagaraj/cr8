from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import tone_wav
from cr8.db import connect
from cr8.paths import archive_relpath
from cr8.stems import (
    DEFAULT_MODEL_A,
    DEFAULT_MODEL_B,
    HQ_MODEL_A,
    HQ_RECIPE,
    STEM_KINDS,
    _sweep_work,
    load_manifest,
    resolve_bounce_source,
    separate_bounce,
)


BOUNCE_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
JOB_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"


def test_crate_environment_does_not_import_separator_torch():
    assert importlib.util.find_spec("torch") is None


def _catalog(config, root: Path) -> tuple[Path, Path]:
    db_path = config.state_dir / "catalog.db"
    wav = tone_wav(root / "curated" / "1-1-24-song.wav", duration_s=0.25)
    mp3 = root / "curated" / "1-1-24-song.mp3"
    mp3.write_bytes(b"lossy twin")
    connection = connect(db_path)
    try:
        connection.execute(
            "INSERT INTO songs(slug, title, public_id) VALUES('song','Song','01ARZ3NDEKTSV4RRFFQ69G5FAV')"
        )
        song_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO bounces(public_id, song_id, source_stem)
            VALUES(?, ?, 'song')
            """,
            (BOUNCE_ULID, song_id),
        )
        bounce_id = int(
            connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        connection.executemany(
            """
            INSERT INTO files(
              relpath, layer, ext, duration_s, bounce_id, parse_status
            ) VALUES(?, 'curated', ?, 0.25, ?, 'parsed')
            """,
            (
                ("curated/1-1-24-song.wav", ".wav", bounce_id),
                ("curated/1-1-24-song.mp3", ".mp3", bounce_id),
            ),
        )
    finally:
        connection.close()
    return db_path, wav


def test_archive_source_passes_stem_containment(fixture_config):
    config, root = fixture_config
    archive = config.state_dir / "2021-New-Projects"
    archive.mkdir()
    config = replace(
        config,
        corpus=replace(config.corpus, archive_roots=(archive,)),
    )
    db_path, wav = _catalog(config, root)
    archived = archive / wav.name
    wav.replace(archived)
    relpath = archive_relpath(archive, archived)
    connection = connect(db_path)
    try:
        connection.execute(
            "UPDATE files SET relpath=? WHERE ext='.wav'",
            (relpath,),
        )
    finally:
        connection.close()

    resolved = resolve_bounce_source(db_path, config, BOUNCE_ULID)
    assert resolved.source.relpath == relpath
    assert resolved.source.path == archived


def _seed_tools(config) -> None:
    separator = config.state_dir / ".venv-stems" / "bin" / "audio-separator"
    separator.parent.mkdir(parents=True)
    separator.write_text("#!/bin/sh\n", encoding="utf-8")
    separator.chmod(0o755)
    models = config.state_dir / "models" / "uvr"
    (models / "mdx_model_data").mkdir(parents=True)
    for name in (
        DEFAULT_MODEL_A,
        DEFAULT_MODEL_B,
        HQ_MODEL_A,
        "955717e8-8726e21a.th",
        "download_checks.json",
        "mdx_model_data/model_data.json",
    ):
        (models / name).write_bytes(b"model")


class FakeRunner:
    def __init__(
        self,
        *,
        fail_model: str | None = None,
        source_codec: str = "",
    ) -> None:
        self.calls: list[
            tuple[Path, tuple[object, ...], float | None, dict[str, str] | None]
        ] = []
        self.fail_model = fail_model
        self.source_codec = source_codec

    def __call__(
        self,
        executable,
        args,
        *,
        timeout=None,
        env=None,
    ):
        executable = Path(executable)
        values = tuple(args)
        self.calls.append(
            (
                executable,
                values,
                timeout,
                dict(env) if env is not None else None,
            )
        )
        if executable.name == "audio-separator" and values == ("--version",):
            return subprocess.CompletedProcess(
                [str(executable), *values],
                0,
                stdout="audio-separator 0.44.5\n",
                stderr="",
            )
        if executable.name == "audio-separator" and "--download_model_only" in values:
            models = Path(values[values.index("--model_file_dir") + 1])
            model = str(values[values.index("--model_filename") + 1])
            (models / model).write_bytes(b"downloaded model")
            return subprocess.CompletedProcess(
                [str(executable), *map(str, values)],
                0,
                stdout="downloaded\n",
                stderr="",
            )
        if executable.name == "audio-separator":
            model = str(values[values.index("--model_filename") + 1])
            if model == self.fail_model:
                return subprocess.CompletedProcess(
                    [str(executable), *map(str, values)],
                    1,
                    stdout="",
                    stderr="simulated interruption",
                )
            source = Path(values[0])
            output_dir = Path(values[values.index("--output_dir") + 1])
            names = json.loads(
                str(values[values.index("--custom_output_names") + 1])
            )
            for name in names.values():
                sanitized = name.strip("_. ")
                shutil.copyfile(source, output_dir / f"{sanitized}.flac")
        elif executable.name == "ffprobe" and "stream=codec_name" in values:
            return subprocess.CompletedProcess(
                [str(executable), *map(str, values)],
                0,
                stdout=f"{self.source_codec}\n",
                stderr="",
            )
        elif executable.name == "ffmpeg" and "pcm_s24le" in values:
            shutil.copyfile(Path(values[values.index("-i") + 1]), Path(values[-1]))
        return subprocess.CompletedProcess(
            [str(executable), *map(str, values)],
            0,
            stdout="",
            stderr="",
        )


def test_separate_is_atomic_deterministic_and_manifest_round_trips(
    fixture_config,
):
    config, root = fixture_config
    db_path, wav = _catalog(config, root)
    _seed_tools(config)
    runner = FakeRunner()

    result = separate_bounce(
        db_path,
        config,
        BOUNCE_ULID,
        job_ulid=JOB_ULID,
        runner=runner,
    )

    assert result.created
    assert result.output_dir == config.state_dir / "stems" / BOUNCE_ULID
    assert not (config.state_dir / "stems" / ".work" / JOB_ULID).exists()
    assert sorted(path.name for path in result.output_dir.iterdir()) == [
        "bass.flac",
        "drums.flac",
        "instrumental.flac",
        "manifest.json",
        "other.flac",
        "vocals.flac",
    ]
    manifest = load_manifest(result.output_dir / "manifest.json")
    assert manifest == result.manifest
    assert manifest["source"]["relpath"].endswith(".wav")
    assert manifest["source"]["sha256"]
    assert manifest["source"]["input_conversion"] is None
    assert manifest["models"] == {
        "pass_a": DEFAULT_MODEL_A,
        "pass_b": DEFAULT_MODEL_B,
    }
    assert set(manifest["files"]) == set(STEM_KINDS)
    assert all(
        entry["duration_s"] == pytest.approx(0.25, abs=0.01)
        for entry in manifest["files"].values()
    )
    connection = connect(db_path)
    try:
        run = connection.execute("SELECT * FROM stem_runs").fetchone()
        assert run["bounce_id"] == 1
        assert run["recipe"] == "default-v1"
        assert run["pass_a_done"] == run["pass_b_done"] == run["ok"] == 1
        stems = connection.execute(
            "SELECT * FROM stems ORDER BY kind"
        ).fetchall()
        assert {row["kind"] for row in stems} == set(STEM_KINDS)
        assert all(str(row["public_id"]).startswith("01") for row in stems)
        assert all(
            str(row["archive_relpath"]).startswith(f"stems/{BOUNCE_ULID}/")
            for row in stems
        )
        public_ids = {row["kind"]: row["public_id"] for row in stems}
    finally:
        connection.close()
    assert wav.is_file()

    separator_calls = [
        call for call in runner.calls if call[0].name == "audio-separator"
    ]
    assert len(separator_calls) == 3
    pass_a = separator_calls[1]
    pass_b = separator_calls[2]
    for call in (pass_a, pass_b):
        _, args, timeout, env = call
        assert args[0] == wav
        assert timeout == pytest.approx(121.0)
        assert env is not None
        assert env["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"
        assert env["OMP_NUM_THREADS"] == "6"
        custom = args[args.index("--custom_output_names") + 1]
        assert isinstance(custom, str)
    assert pass_a[1][pass_a[1].index("--model_filename") + 1] == DEFAULT_MODEL_A
    assert json.loads(
        pass_a[1][pass_a[1].index("--custom_output_names") + 1]
    ) == {"Vocals": "vocals", "Instrumental": "instrumental"}
    assert pass_b[1][pass_b[1].index("--model_filename") + 1] == DEFAULT_MODEL_B
    assert json.loads(
        pass_b[1][pass_b[1].index("--custom_output_names") + 1]
    ) == {
        "Drums": "drums",
        "Bass": "bass",
        "Other": "other",
        "Vocals": "_demucs_vocals",
    }
    assert "--demucs_shifts" in pass_b[1]
    assert pass_b[1][pass_b[1].index("--demucs_shifts") + 1] == "1"

    no_op_runner = FakeRunner(fail_model=DEFAULT_MODEL_A)
    repeated = separate_bounce(
        db_path,
        config,
        BOUNCE_ULID,
        runner=no_op_runner,
    )
    assert not repeated.created
    assert not no_op_runner.calls
    connection = connect(db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM stem_runs"
        ).fetchone()[0] == 1
        assert {
            row["kind"]: row["public_id"]
            for row in connection.execute("SELECT kind, public_id FROM stems")
        } == public_ids
    finally:
        connection.close()


def test_interruption_leaves_only_work_output(fixture_config):
    config, root = fixture_config
    db_path, _ = _catalog(config, root)
    _seed_tools(config)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        separate_bounce(
            db_path,
            config,
            BOUNCE_ULID,
            job_ulid=JOB_ULID,
            runner=FakeRunner(fail_model=DEFAULT_MODEL_B),
        )

    assert not (config.state_dir / "stems" / BOUNCE_ULID).exists()
    work = config.state_dir / "stems" / ".work" / JOB_ULID
    assert work.is_dir()
    assert (work / "vocals.flac").is_file()
    assert (work / "instrumental.flac").is_file()
    assert (work / "pass-a.json").is_file()
    assert not (work / "manifest.json").exists()

    runner = FakeRunner()
    resumed = separate_bounce(
        db_path,
        config,
        BOUNCE_ULID,
        job_ulid=JOB_ULID,
        runner=runner,
    )
    assert resumed.created
    separator_models = [
        call[1][call[1].index("--model_filename") + 1]
        for call in runner.calls
        if call[0].name == "audio-separator" and "--model_filename" in call[1]
    ]
    assert separator_models == [DEFAULT_MODEL_B]


def test_sweep_removes_only_work_older_than_24_hours(tmp_path):
    stems = tmp_path / "stems"
    stale = stems / ".work" / "stale"
    current = stems / ".work" / "current"
    stale.mkdir(parents=True)
    current.mkdir()
    (stale / "partial.flac").write_bytes(b"partial")
    old = datetime.now(UTC) - timedelta(hours=25)
    timestamp = old.timestamp()
    for path in (stale / "partial.flac", stale):
        path.chmod(0o700 if path.is_dir() else 0o600)
        os.utime(path, (timestamp, timestamp))

    swept = _sweep_work(stems)

    assert swept == 1
    assert not stale.exists()
    assert current.is_dir()


def test_float_source_uses_temporary_pcm24_input(fixture_config):
    config, root = fixture_config
    db_path, wav = _catalog(config, root)
    _seed_tools(config)
    runner = FakeRunner(source_codec="pcm_f32le")

    result = separate_bounce(
        db_path,
        config,
        BOUNCE_ULID,
        job_ulid=JOB_ULID,
        runner=runner,
    )

    assert result.manifest["source"]["input_conversion"] == (
        "pcm_f32le to pcm_s24le working copy"
    )
    assert not (result.output_dir / "_separator_input.wav").exists()
    separator_inputs = [
        call[1][0]
        for call in runner.calls
        if call[0].name == "audio-separator" and call[1] != ("--version",)
    ]
    assert separator_inputs == [
        config.state_dir / "stems" / ".work" / JOB_ULID / "_separator_input.wav",
        config.state_dir / "stems" / ".work" / JOB_ULID / "_separator_input.wav",
    ]
    assert wav.is_file()


def test_hq_recipe_coexists_and_uses_roformer_for_pass_a(fixture_config):
    config, root = fixture_config
    db_path, _ = _catalog(config, root)
    _seed_tools(config)
    separate_bounce(
        db_path,
        config,
        BOUNCE_ULID,
        job_ulid=JOB_ULID,
        runner=FakeRunner(),
    )
    (config.state_dir / "models" / "uvr" / HQ_MODEL_A).unlink()
    runner = FakeRunner()

    result = separate_bounce(
        db_path,
        config,
        BOUNCE_ULID,
        recipe=HQ_RECIPE,
        runner=runner,
    )

    assert result.output_dir == (
        config.state_dir / "stems" / BOUNCE_ULID / HQ_RECIPE
    )
    assert result.manifest["models"]["pass_a"] == HQ_MODEL_A
    assert any("--download_model_only" in call[1] for call in runner.calls)
    pass_a = [
        call
        for call in runner.calls
        if call[0].name == "audio-separator"
        and "--model_filename" in call[1]
        and call[1][call[1].index("--model_filename") + 1] == HQ_MODEL_A
    ][0]
    assert "--mdx_segment_size" not in pass_a[1]
    connection = connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM stem_runs").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM stems").fetchone()[0] == 10
        assert {
            row["recipe"] for row in connection.execute("SELECT recipe FROM stem_runs")
        } == {"default-v1", HQ_RECIPE}
    finally:
        connection.close()


def test_changed_source_rerun_preserves_prior_archive(fixture_config):
    config, root = fixture_config
    db_path, wav = _catalog(config, root)
    _seed_tools(config)
    first = separate_bounce(
        db_path,
        config,
        BOUNCE_ULID,
        runner=FakeRunner(),
    )
    first_sha = first.manifest["source"]["sha256"]
    connection = connect(db_path)
    try:
        public_ids = {
            row["kind"]: row["public_id"]
            for row in connection.execute("SELECT kind, public_id FROM stems")
        }
    finally:
        connection.close()
    tone_wav(wav, duration_s=0.25, frequency=660.0)

    rerun = separate_bounce(
        db_path,
        config,
        BOUNCE_ULID,
        runner=FakeRunner(),
    )

    assert rerun.created
    assert rerun.output_dir.parent.name == "reruns"
    assert rerun.output_dir.name.startswith("default-v1-")
    assert load_manifest(first.output_dir / "manifest.json")["source"]["sha256"] == (
        first_sha
    )
    assert rerun.manifest["source"]["sha256"] != first_sha
    connection = connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM stem_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM stems").fetchone()[0] == 5
        assert {
            row["kind"]: row["public_id"]
            for row in connection.execute("SELECT kind, public_id FROM stems")
        } == public_ids
        assert all(
            "/reruns/default-v1-" in row["archive_relpath"]
            for row in connection.execute("SELECT archive_relpath FROM stems")
        )
    finally:
        connection.close()


def test_archive_relpath_is_computed_against_the_stems_root(tmp_path):
    """stems/ is a symlink on the server and a real directory on the laptop.

    Deriving the stored path from the app directory only works in the second
    case. On the server, resolving the symlink puts the finished stem outside
    the app directory, so relative_to raised - after separation had already
    run for two minutes and written its output to disk.
    """
    from pathlib import Path

    app = tmp_path / "Catalog"
    app.mkdir()
    data = tmp_path / "data" / "stems"
    data.mkdir(parents=True)
    (app / "stems").symlink_to(data)

    stems_root = (app / "stems").resolve(strict=True)
    archive = stems_root / "01BOUNCE" / "vocals.flac"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"")

    # What the code used to do.
    try:
        archive.relative_to(app.resolve())
        raise AssertionError("expected the old derivation to fail")
    except ValueError:
        pass

    # What it does now, and the exact string both readers expect.
    relpath = (Path("stems") / archive.relative_to(stems_root)).as_posix()
    assert relpath == "stems/01BOUNCE/vocals.flac"
    assert (app.resolve() / relpath).resolve(strict=True) == archive
