"""Uploaded files travel the same road as everything else.

A drop is only useful if the ordinary pipeline picks it up: scanned, parsed,
resolved into a song and a bounce. The two things that make drops different are
that they live outside the read-only corpus, and that a filename nobody wrote
to our convention still has to become a track rather than disappearing into a
residue pile.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cr8.db import connect
from cr8.paths import (
    DROPS_PREFIX,
    archive_relpath,
    drop_relpath,
    is_archive,
    is_drop,
    source_path,
)
from cr8.scan import scan_catalog


@pytest.fixture
def world(fixture_config):
    """The real config loader, so drops_root is resolved the way it is live."""
    config, root = fixture_config
    (root / "curated").mkdir(exist_ok=True)
    drops = config.corpus.resolved_drops_root
    (drops / "henry").mkdir(parents=True, exist_ok=True)
    connection = connect(config.state_dir / "catalog.db")
    return config, connection, root, drops


def test_a_relpath_says_where_a_file_lives(world) -> None:
    config, _, root, drops = world
    assert source_path(config, "curated/take.wav") == root / "curated/take.wav"
    assert source_path(config, "_drops/henry/take.wav") == drops / "henry/take.wav"


def test_an_archive_relpath_round_trips_through_its_configured_root(world) -> None:
    config, _, _, _ = world
    archive = config.state_dir / "2021-New-Projects"
    config = replace(
        config,
        corpus=replace(config.corpus, archive_roots=(archive,)),
    )
    relpath = archive_relpath(archive, "song.wav")
    assert relpath == "_archive/2021-New-Projects/song.wav"
    assert is_archive(relpath)
    assert source_path(config, relpath) == archive / "song.wav"


def test_drop_relpath_round_trips() -> None:
    relpath = drop_relpath("henry", "bounce.wav")
    assert relpath == f"{DROPS_PREFIX}henry/bounce.wav"
    assert is_drop(relpath)
    assert not is_drop("curated/bounce.wav")


def test_a_dropped_file_is_scanned(world) -> None:
    config, connection, _, drops = world
    (drops / "henry" / "skyline 2026-01-02.wav").write_bytes(b"RIFF" + b"\0" * 1024)

    summary = scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
    assert summary.new == 1

    row = connection.execute(
        "SELECT relpath, layer FROM files"
    ).fetchone()
    assert row["relpath"] == "_drops/henry/skyline 2026-01-02.wav"
    assert row["layer"] == "curated"


def test_a_dropped_file_becomes_a_song(world) -> None:
    config, connection, _, drops = world
    (drops / "henry" / "skyline 2026-01-02.wav").write_bytes(b"RIFF" + b"\0" * 1024)
    scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)

    titles = [
        str(row["title"]) for row in connection.execute("SELECT title FROM songs")
    ]
    assert titles, "a dropped file should resolve into a song"


def test_a_filename_nobody_wrote_for_us_still_becomes_a_track(world) -> None:
    """The convention is ours, not the uploader's. Refusing their file because
    they did not know it would make the feature look broken."""
    config, connection, _, drops = world
    (drops / "henry" / "01-02-2026.wav").write_bytes(b"RIFF" + b"\0" * 1024)
    scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)

    row = connection.execute(
        "SELECT parse_status FROM files WHERE relpath LIKE '_drops/%'"
    ).fetchone()
    assert row["parse_status"] == "parsed", "a drop must not be left as residue"

    songs = [str(r["title"]) for r in connection.execute("SELECT title FROM songs")]
    assert any("01" in title for title in songs), songs


def test_an_unparseable_drop_is_still_flagged_for_a_human(world) -> None:
    config, connection, _, drops = world
    (drops / "henry" / "01-02-2026.wav").write_bytes(b"RIFF" + b"\0" * 1024)
    scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)

    kinds = [
        str(row["kind"]) for row in connection.execute("SELECT kind FROM review_queue")
    ]
    assert "unparsed_name" in kinds, "it should still land in front of someone"


def test_a_corpus_file_with_a_bad_name_is_still_residue(world) -> None:
    """The fallback is for drops only. Corpus files keep the old contract, so a
    typo in the archive still gets flagged rather than silently invented."""
    config, connection, root, _ = world
    (root / "curated" / "!!!.wav").write_bytes(b"RIFF" + b"\0" * 512)
    scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)

    row = connection.execute(
        "SELECT parse_status FROM files WHERE relpath LIKE 'curated/%'"
    ).fetchone()
    assert row["parse_status"] == "residue"


def test_a_full_scan_does_not_lose_drops(world) -> None:
    """The full scan decides what has gone missing. If it cannot see drops, it
    marks every uploaded file missing on the next nightly run."""
    config, connection, _, drops = world
    (drops / "henry" / "keeper 2026-01-02.wav").write_bytes(b"RIFF" + b"\0" * 1024)
    scan_catalog(connection, config, curated_only=True, debounce_seconds=0, stability_wait_seconds=0)
    scan_catalog(connection, config, curated_only=False, debounce_seconds=0, stability_wait_seconds=0)

    row = connection.execute(
        "SELECT missing_since FROM files WHERE relpath LIKE '_drops/%'"
    ).fetchone()
    assert row["missing_since"] is None


def test_deleting_a_drop_marks_it_missing(world) -> None:
    config, connection, _, drops = world
    target = drops / "henry" / "gone 2026-01-02.wav"
    target.write_bytes(b"RIFF" + b"\0" * 1024)
    scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
    target.unlink()
    scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)

    row = connection.execute(
        "SELECT missing_since FROM files WHERE relpath LIKE '_drops/%'"
    ).fetchone()
    assert row["missing_since"] is not None


def test_no_drops_root_is_not_an_error(fixture_config) -> None:
    """A machine that has never received an upload has no drops directory."""
    config, root = fixture_config
    (root / "curated").mkdir(exist_ok=True)
    assert not config.corpus.resolved_drops_root.exists()
    connection = connect(config.state_dir / "catalog.db")
    summary = scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
    assert summary.new == 0
