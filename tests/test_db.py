import sqlite3
from pathlib import Path

import pytest

from cr8.db import SCHEMA_VERSION, connect


def test_schema_and_pragmas(tmp_path: Path):
    connection = connect(tmp_path / "catalog.db")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "files", "songs", "song_aliases", "bounces", "song_tags", "eras",
            "analysis", "mik_tracks", "projects", "song_projects", "review_queue",
            "mirror_files", "playlists", "samply_uploads", "feedback",
            "rating_sync", "runs", "meta", "build_state",
            "users", "shares", "sessions", "reactions", "listen_progress",
            "playback_events", "app_alerts", "stem_runs", "stems", "jobs",
            "songs_fts",
        } <= tables
        assert connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0] == str(SCHEMA_VERSION)
        song_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(songs)")
        }
        assert {"human_touched", "public_id", "released_url"} <= song_columns
        connection.execute(
            """
            INSERT INTO songs(slug, title, status, released_url)
            VALUES('released-song', 'Released Song', 'released',
                   'https://example.test/track')
            """
        )
        assert tuple(
            connection.execute(
                """
                SELECT status, released_url
                FROM songs
                WHERE slug='released-song'
                """
            ).fetchone()
        ) == ("released", "https://example.test/track")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO songs(slug, title, status)
                VALUES('invalid-song', 'Invalid Song', 'invalid')
                """
            )
        assert {"public_id"} <= {
            row["name"] for row in connection.execute("PRAGMA table_info(bounces)")
        }
        assert {"sha256", "fingerprint", "fp_at"} <= {
            row["name"] for row in connection.execute("PRAGMA table_info(files)")
        }
        assert {
            "bounce_id",
            "mirror_relpath",
            "src_sha256",
            "encoder_settings",
            "tag_hash",
            "built_at",
        } == {
            row["name"]
            for row in connection.execute("PRAGMA table_info(mirror_files)")
        }
        assert {"include_stems"} <= {
            row["name"] for row in connection.execute("PRAGMA table_info(shares)")
        }
        assert {
            "bounce_id", "recipe", "model_a", "model_b", "pass_a_done",
            "pass_b_done", "src_relpath", "src_sha256", "separator_version",
            "started_at", "finished_at", "ok",
        } <= {
            row["name"]
            for row in connection.execute("PRAGMA table_info(stem_runs)")
        }
        assert {
            "public_id", "run_id", "bounce_id", "kind", "archive_relpath",
            "archive_sha256", "mirror_relpath", "duration_s", "built_at",
        } <= {
            row["name"] for row in connection.execute("PRAGMA table_info(stems)")
        }
        assert {
            "ulid", "kind", "target_id", "payload", "state", "priority",
            "attempts", "max_attempts", "lease_owner", "lease_until",
            "progress", "error", "requested_by", "created_at", "updated_at",
        } <= {
            row["name"] for row in connection.execute("PRAGMA table_info(jobs)")
        }
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {"idx_jobs_active", "idx_jobs_claim", "idx_stems_bounce"} <= indexes
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name='v_song_bounces'"
        ).fetchone()
    finally:
        connection.close()


def test_v1_migration_backfills_immutable_ulids_and_rebuilds_mirror_table(
    tmp_path: Path,
):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE songs (
          id INTEGER PRIMARY KEY, slug TEXT NOT NULL, disambig TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'demo', keeper INTEGER DEFAULT 0,
          key_canon TEXT, key_camelot TEXT, key_source TEXT, bpm REAL, bpm_source TEXT,
          energy INTEGER, era_id INTEGER, first_date TEXT, last_date TEXT, notes TEXT,
          human_touched INTEGER NOT NULL DEFAULT 0, UNIQUE(slug, disambig)
        );
        CREATE TABLE bounces (
          id INTEGER PRIMARY KEY, song_id INTEGER NOT NULL, source_stem TEXT NOT NULL,
          bounce_date TEXT, date_source TEXT, date_suspect INTEGER NOT NULL DEFAULT 0,
          version INTEGER, mixrole TEXT NOT NULL DEFAULT 'main', collab_raw TEXT,
          UNIQUE(song_id, source_stem)
        );
        CREATE TABLE files (
          id INTEGER PRIMARY KEY, relpath TEXT UNIQUE NOT NULL, layer TEXT NOT NULL,
          ext TEXT, size INTEGER, mtime REAL, md5 TEXT, duration_s REAL, bounce_id INTEGER,
          parse_status TEXT NOT NULL DEFAULT 'unparsed', first_seen TEXT, last_seen TEXT,
          missing_since TEXT
        );
        CREATE TABLE mirror_files (
          bounce_id INTEGER PRIMARY KEY, mirror_relpath TEXT UNIQUE,
          src_md5 TEXT, tag_hash TEXT, built_at TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO songs(id, slug, title) VALUES(1, 'song', 'Song');
        INSERT INTO bounces(id, song_id, source_stem) VALUES(1, 1, 'song');
        INSERT INTO mirror_files VALUES(1, 'tracks/old.mp3', 'md5', 'tags', 'then');
        INSERT INTO meta VALUES('schema_version', '1');
        """
    )
    legacy.close()

    connection = connect(path)
    song_ulid = connection.execute("SELECT public_id FROM songs WHERE id=1").fetchone()[0]
    bounce_ulid = connection.execute("SELECT public_id FROM bounces WHERE id=1").fetchone()[0]
    mirror = connection.execute("SELECT * FROM mirror_files").fetchone()
    song_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(songs)")
    }
    assert len(song_ulid) == len(bounce_ulid) == 26
    assert "released_url" in song_columns
    connection.execute(
        """
        UPDATE songs
        SET status='released', released_url='https://example.test/track'
        WHERE id=1
        """
    )
    assert tuple(
        connection.execute(
            "SELECT status, released_url FROM songs WHERE id=1"
        ).fetchone()
    ) == ("released", "https://example.test/track")
    assert mirror["mirror_relpath"] == "tracks/old.mp3"
    assert mirror["src_sha256"] is None
    assert mirror["encoder_settings"] is None
    assert mirror["tag_hash"] == "tags"
    connection.close()

    reopened = connect(path)
    try:
        assert reopened.execute("SELECT public_id FROM songs WHERE id=1").fetchone()[0] == song_ulid
        assert reopened.execute("SELECT public_id FROM bounces WHERE id=1").fetchone()[0] == bounce_ulid
        assert reopened.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0] == str(SCHEMA_VERSION)
    finally:
        reopened.close()


def test_v4_migration_adds_share_stems_flag_and_enforces_job_constraints(
    tmp_path: Path,
):
    path = tmp_path / "v4.db"
    initial = connect(path)
    initial.execute("ALTER TABLE shares DROP COLUMN include_stems")
    initial.close()

    connection = connect(path)
    try:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(shares)")
        }
        assert "include_stems" in columns
        connection.execute(
            """
            INSERT INTO songs(slug, title) VALUES('song', 'Song')
            """
        )
        song_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO bounces(song_id, source_stem) VALUES(?, 'song')",
            (song_id,),
        )
        bounce_id = int(
            connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        values = (
            "01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "stems",
            bounce_id,
            '{"recipe":"default-v1"}',
            "owner",
            "2026-07-29T00:00:00+00:00",
            "2026-07-29T00:00:00+00:00",
        )
        connection.execute(
            """
            INSERT INTO jobs(
              ulid, kind, target_id, payload, requested_by, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO jobs(
                  ulid, kind, target_id, payload, requested_by,
                  created_at, updated_at
                ) VALUES('01ARZ3NDEKTSV4RRFFQ69G5FAX', 'stems', ?, '{}',
                         'owner', ?, ?)
                """,
                (bounce_id, values[-2], values[-1]),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO stems(
                  public_id, run_id, bounce_id, kind,
                  archive_relpath, archive_sha256
                ) VALUES('01ARZ3NDEKTSV4RRFFQ69G5FAY', 999, ?, 'guitar',
                         'stems/bad.flac', 'bad')
                """,
                (bounce_id,),
            )
    finally:
        connection.close()
