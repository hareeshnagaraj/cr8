from pathlib import Path
import sqlite3
import subprocess

from conftest import tone_wav
from cr8.db import connect
from cr8.enrichment import (
    _bookmark_path,
    _detected_bpm,
    _detected_key,
    detect,
    import_mik,
)
from cr8.keys import load_keymap
from cr8.resolve import resolve_catalog


def _catalog_song(connection, config, root):
    path = tone_wav(root / "1-1-24-song.wav")
    connection.execute(
        """
        INSERT INTO files(
          relpath, layer, ext, size, mtime, md5, duration_s,
          first_seen, last_seen
        ) VALUES('1-1-24-song.wav', 'curated', '.wav', ?, ?, 'md5', 0.25, 's', 's')
        """,
        (path.stat().st_size, path.stat().st_mtime),
    )
    resolve_catalog(connection, config)
    return path


def _mik_db(path: Path, source_path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE ZSONG (
          Z_PK INTEGER PRIMARY KEY, ZBOOKMARKDATA BLOB, ZNAME TEXT, ZKEY TEXT,
          ZTAGKEY TEXT, ZTEMPO REAL, ZTAGTEMPO INTEGER, ZENERGY REAL,
          ZTAGENERGY INTEGER
        );
        CREATE TABLE ZCUEPOINT (
          ZSONG INTEGER, ZTIME REAL, ZNAME TEXT, ZENERGYLEVEL INTEGER
        );
        """
    )
    blob = b"book\x00file:///\x00" + str(source_path).encode() + b"\x00"
    connection.execute(
        "INSERT INTO ZSONG VALUES(1, ?, 'Song', '10A', 'Bm', 123.5, 0, 6, 6)",
        (blob,),
    )
    connection.execute("INSERT INTO ZCUEPOINT VALUES(1, 3.5, 'Drop', 7)")
    connection.commit()
    connection.close()
    return path


def test_bookmark_decode_and_mik_import_is_idempotent(fixture_config, tmp_path):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        source = _catalog_song(connection, config, root)
        assert _bookmark_path(b"prefix\x00" + str(source).encode()) == str(source)
        mik = _mik_db(tmp_path / "fixture.mikdb", source)
        first = import_mik(connection, config, source_path=mik)
        second = import_mik(connection, config, source_path=mik)
        assert (first.imported, first.matched, first.unmatched) == (1, 1, 0)
        assert second == first
        song = connection.execute("SELECT * FROM songs").fetchone()
        assert (song["key_canon"], song["key_source"]) == ("B minor", "mik")
        assert (round(song["bpm"], 1), song["bpm_source"], song["energy"]) == (
            123.5,
            "mik",
            6,
        )
        assert connection.execute("SELECT COUNT(*) FROM mik_tracks").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM analysis WHERE source='mik'"
        ).fetchone()[0] == 4
    finally:
        connection.close()


def test_detect_degrades_by_kind_when_tools_are_missing(fixture_config, tmp_path):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        _catalog_song(connection, config, root)
        summary = detect(
            connection,
            config,
            tool_paths={"keyfinder-cli": None, "aubio": None},
        )
        assert summary.candidates == 1
        assert summary.failed == 0
        assert summary.skipped_tools == ("keyfinder-cli", "aubio")
    finally:
        connection.close()


def test_detect_output_parsers(fixture_config):
    config, _ = fixture_config
    keymap = load_keymap(config.keymap_path)
    assert _detected_key("F#m\n", keymap) == ("F# minor", "11A")
    assert _detected_key("8B\n", keymap) == ("C major", "8B")
    assert _detected_bpm("0.0\n0.5\n1.0\n1.5\n") == 120.0
    assert _detected_bpm("BPM: 128.25") == 128.25
    assert _detected_bpm("124.33 bpm") == 124.33


def test_a_track_with_no_findable_tempo_is_not_a_failure(fixture_config, tmp_path, monkeypatch):
    """aubio printing "unknown bpm" means it worked, not that it broke.

    Counting that as a failure made the nightly detect stage raise every single
    night - and the mirror build runs behind detect as a prerequisite, so it
    was skipped every night too, leaving every track's ID3 tags stale. One
    ambient piece with no steady pulse was enough to do it.
    """
    from cr8 import enrichment

    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        _catalog_song(connection, config, root)

        calls: list[tuple] = []

        def fake_run_tool(executable, args, **kwargs):
            calls.append((executable, args))
            return subprocess.CompletedProcess(
                [str(executable)], 0, stdout="unknown bpm\n", stderr=""
            )

        monkeypatch.setattr(enrichment, "run_tool", fake_run_tool)
        monkeypatch.setattr(
            enrichment,
            "find_tool",
            lambda name, **kwargs: Path("/usr/bin/true") if name == "aubio" else None,
        )

        summary = enrichment.detect(connection, config)
        assert calls, "aubio should have been invoked"
        assert summary.failed == 0
        assert summary.undetermined >= 1
        assert summary.bpms_analyzed == 0
    finally:
        connection.close()


def test_a_tool_that_errors_is_still_a_failure(fixture_config, tmp_path, monkeypatch):
    """The distinction has to cut both ways, or the guard is just suppression."""
    from cr8 import enrichment

    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        _catalog_song(connection, config, root)
        monkeypatch.setattr(
            enrichment,
            "run_tool",
            lambda executable, args, **kwargs: subprocess.CompletedProcess(
                [str(executable)], 1, stdout="", stderr="boom"
            ),
        )
        monkeypatch.setattr(
            enrichment,
            "find_tool",
            lambda name, **kwargs: Path("/usr/bin/false") if name == "aubio" else None,
        )
        summary = enrichment.detect(connection, config)
        assert summary.failed >= 1
        assert summary.undetermined == 0
    finally:
        connection.close()
