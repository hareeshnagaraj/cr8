from datetime import date
from pathlib import Path

from conftest import old_audio, tone_wav
from cr8.db import connect
from cr8.scan import scan_catalog
from cr8.verify import coverage_snapshot, run_verify
from cr8.web.common.text import display_date, display_date_range


def test_verify_report_and_strict_exit(fixture_config, tmp_path):
    config, root = fixture_config
    old_audio(root / "1-1-24-song-bm.wav")
    connection = connect(tmp_path / "catalog.db")
    try:
        scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
        clean = run_verify(connection, config, today=date(2026, 7, 29))
        assert clean.exit_code == 0
        assert clean.report_path.is_file()
        assert "V5 mirror integrity: SKIP" in clean.output
        strict = run_verify(connection, config, strict=True, today=date(2026, 7, 29))
        assert strict.exit_code == 1
        assert any(item.startswith("V4:") for item in strict.findings)
    finally:
        connection.close()


def test_verify_finds_disk_drift(fixture_config, tmp_path):
    config, root = fixture_config
    old_audio(root / "1-1-24-song.wav")
    connection = connect(tmp_path / "catalog.db")
    try:
        scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
        old_audio(root / "2-2-24-new.wav")
        result = run_verify(connection, config, today=date(2026, 7, 29))
        assert result.exit_code == 1
        assert any("on disk but not cataloged" in item for item in result.findings)
    finally:
        connection.close()


def test_released_songs_are_archived_from_every_v4_gap(
    fixture_config, tmp_path
):
    config, _root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        connection.execute(
            "INSERT INTO songs(slug, title) VALUES('released', 'Released')"
        )
        song_id = int(
            connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE songs SET status='released' WHERE id=?", (song_id,)
        )
        coverage = coverage_snapshot(connection)
        assert coverage.released == {song_id}
        for dimension in ("status", "key", "vibe", "instr", "collab"):
            assert getattr(coverage, dimension) == {song_id}
        assert coverage.gaps == {}

        result = run_verify(connection, config, today=date(2026, 7, 29))
        assert "released    1 archived" in result.output
    finally:
        connection.close()


def test_detail_date_range_formats_current_and_cross_year_dates():
    today = date(2026, 7, 29)
    assert (
        display_date_range("2026-07-27", "2026-07-29", today=today)
        == "Jul 27 – Jul 29"
    )
    assert (
        display_date_range("2025-07-27", "2025-07-29", today=today)
        == "Jul 27 – Jul 29, 2025"
    )
    assert (
        display_date_range("2025-12-31", "2026-01-01", today=today)
        == "Dec 31, 2025 – Jan 1, 2026"
    )


def test_track_display_date_always_includes_the_year():
    assert display_date("2026-08-01") == "Aug 1, 2026"


def test_a_source_unreadable_mid_copy_is_not_an_orphan(fixture_config, tmp_path):
    # An rsync mid-swap makes a source file briefly unreadable at verify
    # time. mirror_expectations skips the bounce on the explicit theory the
    # file has not ARRIVED rather than gone; the verifier must honour the
    # same theory instead of calling the bounce's built art an orphan and
    # failing the nightly. Two nightlies went red on exactly this.
    import pytest as _pytest
    from cr8.mirror import build_mirror
    from cr8.tooling import find_tool

    if find_tool("ffmpeg") is None or find_tool("ffprobe") is None:
        _pytest.skip("ffmpeg required to build the mirror")
    config, root = fixture_config
    wav = root / "1-1-24-inflight.wav"
    tone_wav(wav, duration_s=0.25)
    connection = connect(tmp_path / "catalog.db")
    try:
        scan_catalog(
            connection, config, debounce_seconds=0, stability_wait_seconds=0
        )
        build_mirror(connection, config)
        clean = run_verify(connection, config, today=date(2026, 7, 29))
        assert not any(item.startswith("V5") for item in clean.findings)

        # The file vanishes from disk WITHOUT the scan marking it missing -
        # exactly what a partially-copied corpus looks like.
        hidden = wav.with_suffix(".mid-copy")
        wav.rename(hidden)
        try:
            first = run_verify(connection, config, today=date(2026, 7, 29))
            first_v5 = [
                item for item in first.findings if item.startswith("V5")
            ]
            assert first_v5 == [], first_v5
            assert "source unreadable (in flight)" in first.output

            second = run_verify(connection, config, today=date(2026, 7, 29))
            second_v5 = [
                item for item in second.findings if item.startswith("V5")
            ]
            assert second_v5 == [], second_v5

            third = run_verify(connection, config, today=date(2026, 7, 29))
            assert any(
                item.startswith("V5:")
                and "source unreadable for 3 consecutive runs" in item
                for item in third.findings
            )
        finally:
            hidden.rename(wav)

        run_verify(connection, config, today=date(2026, 7, 29))
        counters = connection.execute(
            "SELECT key FROM build_state WHERE key LIKE 'in_flight_runs:%'"
        ).fetchall()
        assert counters == []
    finally:
        connection.close()
