from pathlib import Path

from cr8.db import connect, transaction
from cr8.resolve import levenshtein, resolve_catalog, slugify


def _insert_file(connection, relpath, duration, mtime, ext=None):
    connection.execute(
        """
        INSERT INTO files(
          relpath, layer, ext, size, mtime, md5, duration_s, first_seen, last_seen
        ) VALUES(?, 'curated', ?, 10, ?, 'abc', ?, 'scan', 'scan')
        """,
        (relpath, ext or Path(relpath).suffix, mtime, duration),
    )


def test_twin_collapse_conflicts_and_project_link(fixture_config, tmp_path):
    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        with transaction(connection):
            _insert_file(connection, "a/1-1-24-drownme-bm.wav", 10.0, 1704067200)
            _insert_file(connection, "a/1-1-24-drownme-bm.mp3", 10.0, 1704067201)
            _insert_file(connection, "b/9-1-25-drownme-cm.wav", 12.0, 1756684800)
            connection.execute(
                """
                INSERT INTO projects(
                  relpath, name_slug, name_date, als_count, backup_als_count, total_bytes
                ) VALUES('drownme Project', 'drownme', NULL, 1, 0, 100)
                """
            )
        summary = resolve_catalog(connection, config)
        assert summary.songs == 1
        assert summary.bounces == 2
        twin_id_count = connection.execute(
            """
            SELECT COUNT(DISTINCT bounce_id) FROM files
            WHERE relpath IN ('a/1-1-24-drownme-bm.wav','a/1-1-24-drownme-bm.mp3')
            """
        ).fetchone()[0]
        assert twin_id_count == 1
        kinds = {
            row["kind"]
            for row in connection.execute(
                "SELECT kind FROM review_queue WHERE status='open'"
            )
        }
        assert {"key_conflict", "possible_distinct"} <= kinds
        assert connection.execute(
            "SELECT method FROM song_projects"
        ).fetchone()[0] == "slug_exact"
    finally:
        connection.close()


def test_cross_directory_duration_mismatch_stays_separate(fixture_config, tmp_path):
    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        with transaction(connection):
            _insert_file(connection, "a/1-1-24-song.wav", 10.0, 1704067200)
            _insert_file(connection, "b/1-1-24-song.mp3", 11.0, 1704067200)
        resolve_catalog(connection, config)
        assert connection.execute("SELECT COUNT(*) FROM bounces").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM review_queue WHERE kind='twin_mismatch'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_project_internal_fast_path_does_not_mark_curated_audio_na(
    fixture_config, tmp_path
):
    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        with transaction(connection):
            _insert_file(
                connection,
                "Freeze handrum2 [2024-03-02 132745].wav",
                10.0,
                1709386065,
            )
            connection.execute(
                """
                INSERT INTO files(
                  relpath, layer, ext, size, mtime, md5, first_seen, last_seen
                ) VALUES(
                  'A Project/Samples/Imported/kick.wav', 'project', '.wav',
                  10, 1709386065, 'abc', 'scan', 'scan'
                )
                """
            )
        resolve_catalog(connection, config)
        statuses = {
            row["relpath"]: row["parse_status"]
            for row in connection.execute("SELECT relpath, parse_status FROM files")
        }
        assert statuses["Freeze handrum2 [2024-03-02 132745].wav"] == "parsed"
        assert statuses["A Project/Samples/Imported/kick.wav"] == "na"
    finally:
        connection.close()


def test_machine_rollups_preserve_human_fields_but_refresh_dates(
    fixture_config, tmp_path
):
    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        with transaction(connection):
            _insert_file(connection, "1-1-24-song-bm.wav", 10.0, 1704067200)
        resolve_catalog(connection, config)
        song_id = connection.execute("SELECT id FROM songs").fetchone()[0]
        with transaction(connection):
            connection.execute(
                """
                UPDATE songs SET title='Human Title', status='finished',
                  key_canon='C major', key_camelot='8B', key_source='human',
                  human_touched=1 WHERE id=?
                """,
                (song_id,),
            )
            _insert_file(connection, "2-2-24-song-f#m.wav", 10.0, 1706832000)
        resolve_catalog(connection, config)
        song = connection.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        assert song["title"] == "Human Title"
        assert song["status"] == "finished"
        assert (song["key_canon"], song["key_source"]) == ("C major", "human")
        assert (song["first_date"], song["last_date"]) == (
            "2024-01-01",
            "2024-02-02",
        )
    finally:
        connection.close()


def test_filename_rollup_leaves_lower_precedence_analysis_as_fallback(
    fixture_config, tmp_path
):
    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        with transaction(connection):
            _insert_file(connection, "1-1-24-song.wav", 10.0, 1704067200)
        resolve_catalog(connection, config)
        song_id = connection.execute("SELECT id FROM songs").fetchone()[0]
        with transaction(connection):
            connection.execute(
                """
                UPDATE songs SET key_canon='D minor', key_camelot='7A',
                  key_source='mik', bpm=128, bpm_source='detected' WHERE id=?
                """,
                (song_id,),
            )
        resolve_catalog(connection, config)
        song = connection.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        assert (song["key_canon"], song["key_source"]) == ("D minor", "mik")
        assert (song["bpm"], song["bpm_source"]) == (128, "detected")
    finally:
        connection.close()


def test_slug_and_levenshtein_helpers():
    assert slugify(["Drown", "Me!"]) == "drownme"
    assert levenshtein("drownme", "drowme") == 1
