from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
import plistlib
import sqlite3

import pytest

from cr8.enrichment import DetectSummary, FingerprintSummary
from cr8.automation import (
    BUILD_LOCK_NAME,
    DoctorCheck,
    FileLock,
    LockBusy,
    _acquire_build_lock,
    install_launchd,
    run_ingest_tick,
    run_nightly,
    snapshot_database,
    verify_database_copy,
)
from cr8.cli import main
from cr8.db import connect
from conftest import old_audio


def test_flock_prevents_concurrent_nightly(fixture_config, tmp_path):
    config, _root = fixture_config
    lock_path = config.state_dir / ".cr8-nightly.lock"

    with FileLock(lock_path):
        report = run_nightly(tmp_path / "catalog.db", config)

    assert report.already_running
    assert report.stages == []


def test_ingest_tick_defers_while_the_build_lock_is_held(fixture_config, tmp_path):
    config, _root = fixture_config

    with FileLock(config.state_dir / BUILD_LOCK_NAME):
        with pytest.raises(LockBusy):
            run_ingest_tick(
                tmp_path / "catalog.db", config, stability_wait_seconds=0
            )


def test_nightly_waits_out_the_build_lock_instead_of_skipping(fixture_config):
    config, _root = fixture_config
    holder = FileLock(config.state_dir / BUILD_LOCK_NAME)
    holder.__enter__()
    waits: list[float] = []

    def release_on_first_wait(seconds: float) -> None:
        waits.append(seconds)
        holder.__exit__(None, None, None)

    lock = _acquire_build_lock(config.state_dir, sleep=release_on_first_wait)
    lock.__exit__(None, None, None)

    assert waits == [15.0]


def test_failed_nightly_stage_means_no_healthchecks_ping(
    fixture_config, tmp_path, monkeypatch
):
    config, _root = fixture_config
    monkeypatch.setenv("CRATE_HEALTHCHECK_URL", "https://example.invalid/heartbeat")
    pinged: list[str] = []

    def fail_detect() -> str:
        raise RuntimeError("simulated detector failure")

    names = (
        "scan",
        "verify",
        "import-mik",
        "fingerprint",
        "detect",
        "build",
        "scrub",
        "database-backup",
        "restic-local",
        "restic-b2",
        "final-verify",
    )
    overrides = {name: (lambda: "ok") for name in names}
    overrides["detect"] = fail_detect

    report = run_nightly(
        tmp_path / "catalog.db",
        config,
        now=datetime(2026, 7, 30, tzinfo=UTC),
        ping=pinged.append,
        notify=lambda _config, _message, _title: True,
        stage_overrides=overrides,
    )

    assert not report.ok
    assert any(stage.name == "detect" and not stage.ok for stage in report.stages)
    assert pinged == []
    health = next(stage for stage in report.stages if stage.name == "healthcheck")
    assert health.skipped
    assert "stage failed" in health.detail


def test_integrity_check_aborts_on_corrupt_database_copy(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database")

    with pytest.raises(RuntimeError, match="integrity_check"):
        verify_database_copy(corrupt)


def test_snapshot_database_uses_vacuum_into_and_verifies_the_copy(tmp_path):
    source = tmp_path / "catalog.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE proof(value TEXT)")
        connection.execute("INSERT INTO proof VALUES('safe')")

    snapshot, expired = snapshot_database(
        source,
        tmp_path / "backups",
        today=date(2026, 7, 30),
    )

    assert snapshot.name == "catalog-2026-07-30.db"
    assert expired == 0
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT value FROM proof").fetchone()[0] == "safe"


def test_nightly_has_an_explicit_logged_wal_checkpoint_stage(
    fixture_config, tmp_path
):
    config, _root = fixture_config
    overridden = {
        name: (lambda: "ok")
        for name in (
            "scan",
            "verify",
            "import-mik",
            "fingerprint",
            "detect",
            "build",
            "scrub",
            "database-backup",
            "restic-local",
            "restic-b2",
            "final-verify",
        )
    }

    report = run_nightly(
        tmp_path / "catalog.db",
        config,
        now=datetime(2026, 7, 30, tzinfo=UTC),
        ping=lambda _url: None,
        notify=lambda _config, _message, _title: True,
        stage_overrides=overridden,
    )

    names = [stage.name for stage in report.stages]
    assert names.index("checkpoint") < names.index("database-backup")
    checkpoint = next(stage for stage in report.stages if stage.name == "checkpoint")
    assert checkpoint.ok
    assert checkpoint.detail.startswith("wal_checkpoint(TRUNCATE): ")


def test_ingest_tick_debounce_skips_fresh_files(fixture_config, tmp_path):
    config, root = fixture_config
    fresh = root / "curated" / "7-30-26-still-exporting.wav"
    fresh.parent.mkdir()
    fresh.write_bytes(b"DAW is still writing")

    result = run_ingest_tick(
        tmp_path / "catalog.db",
        config,
        stability_wait_seconds=0,
    )

    assert result.scan.skipped_debounce == 1
    assert result.scan.new == 0
    assert result.scan.touched_relpaths == ()
    assert result.build is None
    connection = connect(tmp_path / "catalog.db")
    try:
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    finally:
        connection.close()


def test_ingest_tick_notifies_only_for_a_new_unparseable_name(
    fixture_config, tmp_path, monkeypatch
):
    config, root = fixture_config
    old_audio(root / "curated" / "---.wav")
    notifications: list[str] = []
    monkeypatch.setattr(
        "cr8.automation._run_osascript",
        lambda _config, message, _title="cr8": notifications.append(message) or True,
    )

    result = run_ingest_tick(
        tmp_path / "catalog.db",
        config,
        stability_wait_seconds=0,
    )

    assert result.unparsed == ("curated/---.wav",)
    assert len(notifications) == 1
    assert "---.wav" in notifications[0]


def test_doctor_exits_nonzero_when_service_is_down(
    fixture_config, tmp_path, monkeypatch, capsys
):
    config, _root = fixture_config
    db_path = tmp_path / "catalog.db"
    connection = connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO runs(kind, started, finished, ok, notes)
            VALUES('scan', ?, ?, 1, '{}')
            """,
            (
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )
    finally:
        connection.close()

    def service_response(url, *, accepted, timeout=5):
        del accepted, timeout
        if "8080" in url:
            return False, "unreachable"
        return True, "HTTP 401"

    monkeypatch.setattr("cr8.automation._service_response", service_response)
    monkeypatch.setattr(
        "cr8.automation._restic_snapshot_check",
        lambda _config, _now: DoctorCheck("restic snapshot", True, "1.0 hours old"),
    )

    code = main(
        [
            "--config",
            str(config.path),
            "--db",
            str(db_path),
            "doctor",
        ]
    )

    assert code == 1
    assert "[PROBLEM] owner service: unreachable" in capsys.readouterr().out


def test_launchd_plists_include_all_watches_and_monthly_drill(
    fixture_config, tmp_path
):
    config, root = fixture_config
    destination = tmp_path / "LaunchAgents"
    written = install_launchd(
        config,
        tmp_path / "catalog.db",
        destination=destination,
    )

    assert {path.name for path in written} == {
        "com.crate.ingest.plist",
        "com.crate.nightly.plist",
        "com.crate.monthly.plist",
    }
    payloads = {
        path.stem: plistlib.loads(path.read_bytes())
        for path in written
    }
    ingest = payloads["com.crate.ingest"]
    assert ingest["ThrottleInterval"] == 300
    assert str(root) in ingest["WatchPaths"]
    assert str(root / "curated") in ingest["WatchPaths"]
    assert ingest["ProgramArguments"][-1] == "ingest-tick"
    assert payloads["com.crate.nightly"]["StartCalendarInterval"] == {
        "Hour": 3,
        "Minute": 30,
    }
    assert payloads["com.crate.monthly"]["ProgramArguments"][-1] == "monthly"


def test_analysis_failures_do_not_block_the_mirror_build(fixture_config, tmp_path, monkeypatch):
    """A file the analyser cannot read must not stop the build behind it.

    keyfinder-cli exits non-zero on any source it cannot resample to 16-bit
    PCM. Because the detect candidate set is exactly the songs still missing a
    key or tempo - the ones that have already failed - raising there could
    never clear on its own. It skipped the mirror build every night, silently,
    which is how hundreds of tracks ended up with stale tags.
    """
    from cr8 import automation

    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        monkeypatch.setattr(
            automation,
            "detect",
            lambda *a, **k: DetectSummary(
                candidates=9, keys_analyzed=0, bpms_analyzed=0,
                failed=9, skipped_tools=(), undetermined=0,
            ),
        )
        detail = automation._detect_stage(connection, config)
        assert "could not read" in detail

        monkeypatch.setattr(
            automation,
            "fingerprint",
            lambda *a, **k: FingerprintSummary(
                candidates=3, analyzed=0, skipped=0, failed=3,
                edges=0, missing_tool=None,
            ),
        )
        detail = automation._fingerprint_stage(connection, config)
        assert "could not read" in detail
    finally:
        connection.close()
