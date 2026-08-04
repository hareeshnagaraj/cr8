"""SQLite connection, schema, and transaction helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .public_ids import new_ulid
from .web.common.schema import WEB_SCHEMA_SQL, WEB_SCHEMA_VERSION


SCHEMA_VERSION = WEB_SCHEMA_VERSION

SONGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS songs (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL, disambig TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'demo'
    CHECK(status IN ('idea','jam','demo','mixed','finished','released')),
  keeper INTEGER NOT NULL DEFAULT 0 CHECK(keeper BETWEEN 0 AND 5),
  key_canon TEXT, key_camelot TEXT, key_source TEXT,
  bpm REAL, bpm_source TEXT, energy INTEGER,
  era_id INTEGER REFERENCES eras(id),
  first_date TEXT, last_date TEXT, notes TEXT, released_url TEXT,
  public_id TEXT UNIQUE,
  human_touched INTEGER NOT NULL DEFAULT 0 CHECK(human_touched IN (0,1)),
  UNIQUE(slug, disambig));
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  relpath TEXT UNIQUE NOT NULL,
  layer TEXT NOT NULL CHECK(layer IN ('curated','project','other')),
  ext TEXT, size INTEGER, mtime REAL, md5 TEXT, duration_s REAL,
  sha256 TEXT, fingerprint TEXT, fp_at TEXT,
  bounce_id INTEGER REFERENCES bounces(id),
  parse_status TEXT NOT NULL DEFAULT 'unparsed'
    CHECK(parse_status IN ('unparsed','parsed','residue','assigned','na')),
  first_seen TEXT, last_seen TEXT, missing_since TEXT);
""" + SONGS_TABLE_SQL + """
CREATE TABLE IF NOT EXISTS song_aliases (alias_slug TEXT PRIMARY KEY,
  song_id INTEGER NOT NULL REFERENCES songs(id));
CREATE TABLE IF NOT EXISTS bounces (
  id INTEGER PRIMARY KEY,
  public_id TEXT UNIQUE,
  song_id INTEGER NOT NULL REFERENCES songs(id),
  source_stem TEXT NOT NULL,
  bounce_date TEXT, date_source TEXT CHECK(date_source IN ('filename','mtime','human')),
  date_suspect INTEGER NOT NULL DEFAULT 0,
  version INTEGER,
  mixrole TEXT NOT NULL DEFAULT 'main'
    CHECK(mixrole IN ('main','vox','novox','inst','bass','gtar','stems','acap')),
  collab_raw TEXT,
  UNIQUE(song_id, source_stem));
CREATE TABLE IF NOT EXISTS song_tags (
  song_id INTEGER NOT NULL REFERENCES songs(id),
  dim TEXT NOT NULL
    CHECK(dim IN ('vibe','instr','collab','use','problem')),
  value TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'human',
  author TEXT,
  created_at TEXT,
  PRIMARY KEY(song_id, dim, value));
CREATE TABLE IF NOT EXISTS eras (id INTEGER PRIMARY KEY, name TEXT UNIQUE,
  date_start TEXT, date_end TEXT, color TEXT);
CREATE TABLE IF NOT EXISTS analysis (
  file_id INTEGER REFERENCES files(id), kind TEXT CHECK(kind IN ('key','bpm','energy','cues')),
  value TEXT, confidence REAL, source TEXT, analyzed_at TEXT);
CREATE TABLE IF NOT EXISTS mik_tracks (
  id INTEGER PRIMARY KEY, src_path TEXT, name TEXT, duration_s REAL,
  camelot TEXT, key_std TEXT, bpm REAL, energy INTEGER, cues_json TEXT,
  matched_file_id INTEGER REFERENCES files(id), imported_at TEXT);
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, relpath TEXT UNIQUE,
  name_slug TEXT, name_date TEXT, als_count INTEGER, backup_als_count INTEGER, total_bytes INTEGER);
CREATE TABLE IF NOT EXISTS song_projects (song_id INTEGER REFERENCES songs(id),
  project_id INTEGER REFERENCES projects(id),
  method TEXT CHECK(method IN ('slug_exact','human')), PRIMARY KEY(song_id, project_id));
CREATE TABLE IF NOT EXISTS review_queue (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('unparsed_name','merge_suggestion','possible_distinct',
    'twin_mismatch','key_conflict','bpm_conflict','date_suspect','project_link','stray_location')),
  file_id INTEGER, song_id INTEGER, payload TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','ignored')),
  created_at TEXT, resolved_at TEXT, resolution TEXT);
CREATE TABLE IF NOT EXISTS mirror_files (bounce_id INTEGER PRIMARY KEY REFERENCES bounces(id),
  mirror_relpath TEXT UNIQUE, src_sha256 TEXT, encoder_settings TEXT,
  tag_hash TEXT, built_at TEXT);
CREATE TABLE IF NOT EXISTS build_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS playlists (id INTEGER PRIMARY KEY, name TEXT UNIQUE,
  query TEXT NOT NULL, samply_sync INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS samply_uploads (bounce_id INTEGER REFERENCES bounces(id), box_id TEXT, playlist TEXT,
  uploaded_md5 TEXT, url TEXT, uploaded_at TEXT, PRIMARY KEY(bounce_id, box_id));
CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY, song_id INTEGER REFERENCES songs(id),
  source TEXT CHECK(source IN ('samply','navidrome','cr8','manual')),
  author TEXT, timecode_s REAL, body TEXT, ext_id TEXT UNIQUE,
  created_at TEXT, pulled_at TEXT, acked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS rating_sync (song_id INTEGER REFERENCES songs(id), nd_user TEXT, stars INTEGER,
  loved INTEGER, updated_at TEXT, PRIMARY KEY (song_id, nd_user));
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, kind TEXT, started TEXT, finished TEXT,
  ok INTEGER, notes TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX IF NOT EXISTS idx_files_layer_status ON files(layer, parse_status);
CREATE INDEX IF NOT EXISTS idx_files_bounce ON files(bounce_id);
CREATE INDEX IF NOT EXISTS idx_files_last_seen ON files(last_seen);
CREATE INDEX IF NOT EXISTS idx_bounces_song_date ON bounces(song_id, bounce_date, version);
CREATE INDEX IF NOT EXISTS idx_review_open_kind ON review_queue(status, kind, created_at);
CREATE INDEX IF NOT EXISTS idx_song_tags_dim_value ON song_tags(dim, value);
CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(name_slug);

DROP VIEW IF EXISTS v_song_bounces;
CREATE VIEW v_song_bounces AS
WITH bounce_files AS (
  SELECT b.id, b.song_id, b.source_stem, b.bounce_date, b.date_source,
         b.date_suspect, b.version, b.mixrole, b.collab_raw,
         MAX(f.mtime) AS mtime
  FROM bounces AS b
  LEFT JOIN files AS f ON f.bounce_id = b.id
  GROUP BY b.id
)
SELECT bounce_files.*,
       ROW_NUMBER() OVER (
         PARTITION BY song_id
         ORDER BY COALESCE(bounce_date, ''), COALESCE(version, 0), COALESCE(mtime, 0), id
       ) AS chain_position
FROM bounce_files;
""" + WEB_SCHEMA_SQL


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: str | Path, *, initialize: bool = True) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    # Never checkpoint-delete the WAL on close. Two SQLite libraries share
    # this database (stdlib here, APSW in the web layer); within one process
    # POSIX locks cannot mediate between them, so a close-time checkpoint
    # here yanks the sidecar files out from under the web layer's held read
    # connections - "database disk image is malformed" at a distance.
    # Checkpointing belongs to wal_autocheckpoint and the nightly TRUNCATE.
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    if initialize:
        initialize_schema(connection)
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    song_columns = _columns(connection, "songs")
    if "human_touched" not in song_columns:
        connection.execute(
            "ALTER TABLE songs ADD COLUMN human_touched INTEGER NOT NULL DEFAULT 0 "
            "CHECK(human_touched IN (0,1))"
        )
    if "public_id" not in song_columns:
        connection.execute("ALTER TABLE songs ADD COLUMN public_id TEXT")
    if songs_need_released_migration(connection):
        _migrate_released_songs(connection)

    bounce_columns = _columns(connection, "bounces")
    if "public_id" not in bounce_columns:
        connection.execute("ALTER TABLE bounces ADD COLUMN public_id TEXT")

    file_columns = _columns(connection, "files")
    for name in ("sha256", "fingerprint", "fp_at"):
        if name not in file_columns:
            connection.execute(f"ALTER TABLE files ADD COLUMN {name} TEXT")

    mirror_columns = _columns(connection, "mirror_files")
    if "src_md5" in mirror_columns or "encoder_settings" not in mirror_columns:
        _migrate_mirror_files(connection)

    share_columns = _columns(connection, "shares")
    if "include_stems" not in share_columns:
        connection.execute(
            "ALTER TABLE shares ADD COLUMN include_stems INTEGER NOT NULL "
            "DEFAULT 0 CHECK(include_stems IN (0,1))"
        )
    if "landing_collection_id" not in share_columns:
        connection.execute(
            "ALTER TABLE shares ADD COLUMN landing_collection_id INTEGER "
            "REFERENCES collections(id)"
        )
    reaction_columns = _columns(connection, "reactions")
    if "timecode_s" not in reaction_columns:
        connection.execute(
            "ALTER TABLE reactions ADD COLUMN timecode_s REAL"
        )

    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_songs_public_id ON songs(public_id)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bounces_public_id ON bounces(public_id)"
    )
    ensure_public_ids(connection)
    indexed = int(connection.execute("SELECT COUNT(*) FROM songs_fts").fetchone()[0])
    songs = int(connection.execute("SELECT COUNT(*) FROM songs").fetchone()[0])
    if indexed != songs:
        connection.execute("INSERT INTO songs_fts(songs_fts) VALUES('rebuild')")
    connection.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def songs_need_released_migration(connection: Any) -> bool:
    row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name='songs'"
    ).fetchone()
    if row is None:
        return False
    columns = {
        str(column[1]) for column in connection.execute("PRAGMA table_info(songs)")
    }
    return "released_url" not in columns or "'released'" not in str(row[0])


def rebuild_songs_for_released(connection: Any) -> None:
    source_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(songs)")
    }
    target_columns = (
        "id",
        "slug",
        "disambig",
        "title",
        "status",
        "keeper",
        "key_canon",
        "key_camelot",
        "key_source",
        "bpm",
        "bpm_source",
        "energy",
        "era_id",
        "first_date",
        "last_date",
        "notes",
        "released_url",
        "public_id",
        "human_touched",
    )
    copied = [column for column in target_columns if column in source_columns]
    connection.execute(
        SONGS_TABLE_SQL.replace(
            "CREATE TABLE IF NOT EXISTS songs",
            "CREATE TABLE songs_released",
            1,
        )
    )
    names = ", ".join(copied)
    connection.execute(
        f"INSERT INTO songs_released({names}) SELECT {names} FROM songs"
    )
    connection.execute("DROP TABLE songs")
    connection.execute("ALTER TABLE songs_released RENAME TO songs")
    if connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='songs_fts'"
    ).fetchone():
        connection.execute("INSERT INTO songs_fts(songs_fts) VALUES('rebuild')")


def _migrate_released_songs(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        with transaction(connection):
            rebuild_songs_for_released(connection)
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("released-song migration broke foreign keys")
    connection.executescript(WEB_SCHEMA_SQL)


def _migrate_mirror_files(connection: sqlite3.Connection) -> None:
    legacy = _columns(connection, "mirror_files")
    connection.execute(
        """
        CREATE TABLE mirror_files_v2 (
          bounce_id INTEGER PRIMARY KEY REFERENCES bounces(id),
          mirror_relpath TEXT UNIQUE,
          src_sha256 TEXT,
          encoder_settings TEXT,
          tag_hash TEXT,
          built_at TEXT
        )
        """
    )
    if legacy:
        connection.execute(
            """
            INSERT INTO mirror_files_v2(
              bounce_id, mirror_relpath, src_sha256, encoder_settings,
              tag_hash, built_at
            )
            SELECT bounce_id, mirror_relpath, NULL, NULL, tag_hash, built_at
            FROM mirror_files
            """
        )
        connection.execute("DROP TABLE mirror_files")
    connection.execute("ALTER TABLE mirror_files_v2 RENAME TO mirror_files")


def _backfill_public_ids(connection: sqlite3.Connection, table: str) -> None:
    rows = connection.execute(
        f"SELECT id FROM {table} WHERE public_id IS NULL ORDER BY id"
    ).fetchall()
    for index in range(0, len(rows), 500):
        with transaction(connection):
            for row in rows[index : index + 500]:
                while True:
                    public_id = new_ulid()
                    try:
                        connection.execute(
                            f"UPDATE {table} SET public_id=? "
                            "WHERE id=? AND public_id IS NULL",
                            (public_id, int(row["id"])),
                        )
                    except sqlite3.IntegrityError:
                        continue
                    break


def ensure_public_ids(connection: sqlite3.Connection) -> None:
    """Mint IDs for rows created by legacy or direct SQL call paths."""
    _backfill_public_ids(connection, "songs")
    _backfill_public_ids(connection, "bounces")


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
