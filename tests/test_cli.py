import json
from datetime import datetime

from cr8.cli import main
from cr8.db import connect


def test_backfill_dates_uses_file_mtime_refreshes_rollups_and_is_idempotent(
    fixture_config, tmp_path, capsys
):
    config, _ = fixture_config
    db_path = tmp_path / "backfill.sqlite"
    connection = connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO songs(slug, title, first_date, last_date)
            VALUES('backfill-me', 'Backfill Me', '1999-01-01', '1999-01-01')
            """
        )
        song_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO bounces(song_id, source_stem, bounce_date, date_source)
            VALUES(?, 'already-dated', '2022-01-02', 'filename')
            """,
            (song_id,),
        )
        connection.execute(
            """
            INSERT INTO bounces(song_id, source_stem, bounce_date, date_source)
            VALUES(?, 'needs-backfill', NULL, 'mtime')
            """,
            (song_id,),
        )
        bounce_id = int(
            connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        mtime = datetime(2024, 8, 1, 12, 0).timestamp()
        connection.execute(
            """
            INSERT INTO files(
              relpath, layer, mtime, bounce_id, parse_status, first_seen
            ) VALUES(
              'needs-backfill.wav', 'curated', ?, ?, 'parsed', '1999-01-01'
            )
            """,
            (mtime, bounce_id),
        )
    finally:
        connection.close()

    command = [
        "--config",
        str(config.path),
        "--db",
        str(db_path),
        "backfill-dates",
    ]
    assert main([*command, "--dry-run"]) == 0
    dry_run_output = capsys.readouterr().out
    assert "would backfill 1 bounces, roll up 1 songs" in dry_run_output
    assert f"bounce {bounce_id} (needs-backfill) -> 2024-08-01" in dry_run_output
    connection = connect(db_path)
    try:
        assert connection.execute(
            "SELECT bounce_date FROM bounces WHERE id=?", (bounce_id,)
        ).fetchone()[0] is None
    finally:
        connection.close()

    assert main(command) == 0
    assert "1 bounces backfilled, 1 songs rolled up" in capsys.readouterr().out
    connection = connect(db_path)
    try:
        assert tuple(
            connection.execute(
                "SELECT bounce_date, date_source FROM bounces WHERE id=?",
                (bounce_id,),
            ).fetchone()
        ) == ("2024-08-01", "mtime")
        assert tuple(
            connection.execute(
                "SELECT first_date, last_date FROM songs WHERE id=?", (song_id,)
            ).fetchone()
        ) == ("2022-01-02", "2024-08-01")
    finally:
        connection.close()

    assert main(command) == 0
    assert "0 bounces backfilled, 0 songs rolled up" in capsys.readouterr().out


def test_status_smoke(fixture_config, tmp_path, capsys):
    config, _ = fixture_config
    code = main(["--config", str(config.path), "--db", str(tmp_path / "db.sqlite"), "status"])
    assert code == 0
    output = capsys.readouterr().out
    assert "cr8 status" in output
    assert "songs: 0" in output


def test_set_released_status_and_url(fixture_config, tmp_path, capsys):
    config, _ = fixture_config
    db_path = tmp_path / "db.sqlite"
    connection = connect(db_path)
    connection.execute(
        "INSERT INTO songs(slug, title) VALUES('release-me', 'Release Me')"
    )
    connection.close()

    release_url = "https://open.spotify.com/track/example"
    code = main(
        [
            "--config",
            str(config.path),
            "--db",
            str(db_path),
            "set",
            "release-me",
            "status=released",
            f"released_url={release_url}",
        ]
    )

    assert code == 0
    assert "updated song" in capsys.readouterr().out
    connection = connect(db_path)
    try:
        assert tuple(
            connection.execute(
                """
                SELECT status, released_url, human_touched
                FROM songs
                WHERE slug='release-me'
                """
            ).fetchone()
        ) == ("released", release_url, 1)
    finally:
        connection.close()


def test_portable_export_writes_csv_json_and_collection_m3u(
    fixture_config, tmp_path, capsys
):
    config, _ = fixture_config
    db_path = tmp_path / "portable.sqlite"
    connection = connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO songs(
              slug, title, public_id, key_canon, bpm
            ) VALUES('portable', 'Portable Song', '01PORTABLESONG000000000000', 'C minor', 119.1)
            """
        )
        song_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO song_tags(
              song_id, dim, value, source, author, created_at
            ) VALUES(?, 'vibe', 'portable-vibe', 'human', 'owner', '2026-01-01')
            """,
            (song_id,),
        )
        connection.execute(
            """
            INSERT INTO bounces(
              public_id, song_id, source_stem, version
            ) VALUES('01PORTABLEBOUNCE0000000000', ?, 'portable', 1)
            """,
            (song_id,),
        )
        bounce_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO files(
              relpath, layer, ext, bounce_id, parse_status
            ) VALUES('Portable.wav', 'curated', '.wav', ?, 'parsed')
            """,
            (bounce_id,),
        )
        connection.execute(
            """
            INSERT INTO collections(ulid, name, created_at)
            VALUES('01PORTABLECOLLECTION000000', 'Portable Set', '2026-01-01')
            """
        )
        collection_id = int(
            connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO collection_items(
              collection_id, bounce_ulid, position
            ) VALUES(?, '01PORTABLEBOUNCE0000000000', 0)
            """,
            (collection_id,),
        )
    finally:
        connection.close()

    destination = tmp_path / "export"
    code = main(
        [
            "--config",
            str(config.path),
            "--db",
            str(db_path),
            "export",
            str(destination),
        ]
    )
    assert code == 0
    assert (destination / "songs.csv").is_file()
    payload = json.loads(
        (destination / "songs.json").read_text(encoding="utf-8")
    )
    assert payload["format"] == "crate-portable-v1"
    assert payload["songs"][0]["tags"] == [
        {
            "dimension": "vibe",
            "value": "portable-vibe",
            "source": "human",
            "author": "owner",
        }
    ]
    playlist = next((destination / "collections").glob("*.m3u"))
    playlist_text = playlist.read_text(encoding="utf-8")
    assert "#EXTM3U" in playlist_text
    assert "#EXTINF:-1,Portable Song" in playlist_text
    assert "Portable.wav" in playlist_text
    assert "exported 1 song(s) and 1 collection(s)" in capsys.readouterr().out


def test_init_writes_config_dirs_secret_and_db(tmp_path, monkeypatch, capsys):
    import shutil
    from pathlib import Path

    from cr8.tooling import find_tool

    if find_tool("ffmpeg") is None or find_tool("ffprobe") is None:
        import pytest

        pytest.skip("ffmpeg/ffprobe required for init preflight")

    example = Path("config.example.toml")
    shutil.copy(example, tmp_path / "config.example.toml")
    corpus = tmp_path / "music"
    (corpus / "demos").mkdir(parents=True)
    (corpus / "projects").mkdir()

    answers = iter(
        [
            str(corpus),  # corpus root
            "y",  # seed curated_dirs
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = main(["--config", str(tmp_path / "config.toml"), "init"])
    assert code == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:3100/setup" in out
    config_text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert str(corpus) in config_text
    assert '"demos"' in config_text
    assert (tmp_path / "secrets" / "owner-session.key").is_file()
    assert (tmp_path / "mirror").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "catalog.db").is_file()
