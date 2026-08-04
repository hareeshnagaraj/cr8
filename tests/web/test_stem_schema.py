from pathlib import Path
import sqlite3

from cr8.db import connect
from cr8.web.common import database
from cr8.web.common.database import migrate, reading


def test_base_schema_includes_presence_for_non_web_databases(tmp_path: Path):
    path = tmp_path / "catalog.db"
    connection = connect(path)
    try:
        presence = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='presence'"
        ).fetchone()
    finally:
        connection.close()

    assert presence is not None


def test_web_migrate_returns_early_at_current_schema_version(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "catalog.db"
    connect(path).close()
    migrate(path)

    def unexpected_mutation(*_args, **_kwargs):
        raise AssertionError("current schema must not run migration writes")

    monkeypatch.setattr(database, "mutate", unexpected_mutation)

    migrate(path)


def test_web_migrate_adds_stem_schema_to_existing_v4_database(tmp_path: Path):
    path = tmp_path / "catalog.db"
    connection = connect(path)
    connection.execute("ALTER TABLE shares DROP COLUMN include_stems")
    connection.execute("DROP TABLE jobs")
    connection.execute("DROP TABLE stems")
    connection.execute("DROP TABLE stem_runs")
    connection.close()

    migrate(path)

    with reading(path) as migrated:
        tables = {
            str(row["name"])
            for row in migrated.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        shares = {
            str(row["name"])
            for row in migrated.execute("PRAGMA table_info(shares)")
        }
    assert {"stem_runs", "stems", "jobs"} <= tables
    assert "include_stems" in shares


def test_web_migrate_adds_optional_song_to_existing_invites(tmp_path: Path):
    path = tmp_path / "catalog.db"
    connection = connect(path)
    connection.execute("ALTER TABLE invites DROP COLUMN bounce_ulid")
    connection.close()

    migrate(path)

    with reading(path) as migrated:
        invite_columns = {
            str(row["name"])
            for row in migrated.execute("PRAGMA table_info(invites)")
        }
    assert "bounce_ulid" in invite_columns


def test_web_migrate_rebuilds_legacy_song_status_constraint(tmp_path: Path):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE eras (
          id INTEGER PRIMARY KEY,
          name TEXT UNIQUE,
          date_start TEXT,
          date_end TEXT,
          color TEXT
        );
        CREATE TABLE songs (
          id INTEGER PRIMARY KEY,
          slug TEXT NOT NULL,
          disambig TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'demo'
            CHECK(status IN ('idea','jam','demo','mixed','finished')),
          keeper INTEGER NOT NULL DEFAULT 0,
          key_canon TEXT,
          key_camelot TEXT,
          key_source TEXT,
          bpm REAL,
          bpm_source TEXT,
          energy INTEGER,
          era_id INTEGER REFERENCES eras(id),
          first_date TEXT,
          last_date TEXT,
          notes TEXT,
          public_id TEXT UNIQUE,
          human_touched INTEGER NOT NULL DEFAULT 0,
          UNIQUE(slug, disambig)
        );
        CREATE TABLE bounces (
          id INTEGER PRIMARY KEY,
          public_id TEXT UNIQUE,
          song_id INTEGER NOT NULL REFERENCES songs(id),
          source_stem TEXT NOT NULL,
          bounce_date TEXT,
          date_source TEXT,
          date_suspect INTEGER NOT NULL DEFAULT 0,
          version INTEGER,
          mixrole TEXT NOT NULL DEFAULT 'main',
          collab_raw TEXT,
          UNIQUE(song_id, source_stem)
        );
        CREATE TABLE mirror_files (
          bounce_id INTEGER PRIMARY KEY REFERENCES bounces(id),
          mirror_relpath TEXT UNIQUE,
          src_sha256 TEXT,
          encoder_settings TEXT,
          tag_hash TEXT,
          built_at TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO songs(
          id, slug, title, public_id
        ) VALUES(
          1, 'legacy-song', 'Legacy Song',
          '01ARZ3NDEKTSV4RRFFQ69G5FAV'
        );
        INSERT INTO bounces(
          id, public_id, song_id, source_stem
        ) VALUES(
          1, '01ARZ3NDEKTSV4RRFFQ69G5FAW', 1, 'legacy-song'
        );
        INSERT INTO mirror_files(bounce_id, mirror_relpath)
        VALUES(1, 'tracks/legacy.mp3');
        """
    )
    legacy.close()

    migrate(path)

    with reading(path) as migrated:
        song_sql = str(
            migrated.execute(
                """
                SELECT sql
                FROM sqlite_schema
                WHERE type='table' AND name='songs'
                """
            ).fetchone()[0]
        )
        song_columns = {
            str(row["name"])
            for row in migrated.execute("PRAGMA table_info(songs)")
        }
        migrated.execute(
            """
            UPDATE songs
            SET status='released', released_url='https://example.test/release'
            WHERE id=1
            """
        )
        row = migrated.execute(
            """
            SELECT title, status, released_url
            FROM songs
            WHERE id=1
            """
        ).fetchone()
        indexed = migrated.execute(
            """
            SELECT title
            FROM songs_fts
            WHERE songs_fts MATCH '"Legacy Song"'
            """
        ).fetchone()
        violations = list(migrated.execute("PRAGMA foreign_key_check"))

    assert "released_url" in song_columns
    assert "'released'" in song_sql
    assert tuple(row[key] for key in ("title", "status", "released_url")) == (
        "Legacy Song",
        "released",
        "https://example.test/release",
    )
    assert indexed[0] == "Legacy Song"
    assert violations == []
