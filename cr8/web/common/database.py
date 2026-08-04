"""APSW-only web database access with short, retried write transactions."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, TypeVar

import apsw

from ...db import (
    rebuild_songs_for_released,
    songs_need_released_migration,
    utc_now,
)
from .derived import backfill_derived_tags, backfill_eras
from .schema import SHARES_TABLE_SQL, WEB_SCHEMA_SQL, WEB_SCHEMA_VERSION


LOGGER = logging.getLogger("cr8.web.database")
T = TypeVar("T")


class DatabaseBusy(RuntimeError):
    """Raised instead of swallowing an exhausted SQLITE_BUSY."""


class Row(dict[str, Any]):
    """A mapping row that also supports sqlite-style numeric indexing."""

    __slots__ = ("_values",)

    def __init__(self, names: Sequence[str], values: Sequence[Any]) -> None:
        super().__init__(zip(names, values, strict=True))
        self._values = tuple(values)

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _row_trace(cursor: apsw.Cursor, values: tuple[Any, ...]) -> Row:
    names = [str(column[0]) for column in cursor.get_description()]
    return Row(names, values)


def connect(
    path: str | Path, *, foreign_keys: bool = True
) -> apsw.Connection:
    connection = apsw.Connection(str(Path(path)))
    connection.set_busy_timeout(10_000)
    connection.set_row_trace(_row_trace)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}"
    )
    # NORMAL in WAL drops an fsync per commit; the named tradeoff (accepted by
    # the owner 2026-08-02) is losing the last few seconds of writes on power
    # loss - never corruption. Cache and mmap sizes dwarf the 17 MB database
    # on purpose; they only pay off because reading() now keeps connections.
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA cache_size=-65536")
    connection.execute("PRAGMA wal_autocheckpoint=4000")
    return connection


# One cached read connection per (thread, database path). A fresh APSW
# connection per call - the old reading() - made every request re-pay file
# open, WAL header read and pragma setup, and threw SQLite's page cache away
# every single query; /api/facets alone opened six. Thread-local is the load-
# bearing word: FastAPI runs sync handlers in an anyio threadpool and APSW
# connections must not cross threads. The cache is capped so the test suite's
# hundreds of throwaway tmp-path databases cannot exhaust file descriptors;
# production only ever has one path per thread.
_READ_CACHE_LIMIT = 8
_read_local = threading.local()


def _file_identity(path: str) -> tuple[int, int] | None:
    # A VACUUM INTO + os.replace (or a test fixture rebuild) swaps the main
    # file under a held descriptor. The stdlib connection's
    # SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE setting owns sidecar safety now, so the
    # database inode is the only identity this cache needs to observe.
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def _drop_cached(key: str, connection: apsw.Connection) -> None:
    cache = getattr(_read_local, "connections", None)
    if cache is not None:
        cache.pop(key, None)
    try:
        connection.close()
    except apsw.Error:
        pass


def _data_version(connection: apsw.Connection) -> int:
    return int(connection.execute("PRAGMA data_version").fetchone()[0])


def _cached_read_connection(path: str | Path) -> apsw.Connection:
    key = str(Path(path))
    cache: dict[
        str, tuple[apsw.Connection, tuple[int, int] | None, int]
    ] | None = getattr(_read_local, "connections", None)
    if cache is None:
        cache = {}
        _read_local.connections = cache
    cached = cache.get(key)
    if cached is not None:
        connection, identity, data_version = cached
        # A VACUUM INTO + os.replace (or a test fixture rebuild) swaps the
        # file under a held descriptor; queries then read the DELETED inode
        # and answer with stale rows. SQLite's data_version is the honest
        # signal for an external commit, including a foreign connection that
        # rotated its WAL. If either check cannot be read, reopen cleanly.
        try:
            fresh = (
                identity == _file_identity(key)
                and data_version == _data_version(connection)
            )
        except apsw.Error:
            fresh = False
        if fresh:
            return connection
        _drop_cached(key, connection)
    if len(cache) >= _READ_CACHE_LIMIT:
        evicted_key, (evicted, _, _) = next(iter(cache.items()))
        del cache[evicted_key]
        try:
            evicted.close()
        except apsw.Error:
            pass
    connection = connect(key)
    cache[key] = (connection, _file_identity(key), _data_version(connection))
    return connection


@contextmanager
def reading(path: str | Path) -> Iterator[apsw.Connection]:
    # Not re-entrant for the same thread and path: an inner apsw.Error drops
    # the shared cached connection, so an outer block would hold a closed one.
    # No caller nests this context; keep that boundary explicit.
    connection = _cached_read_connection(path)
    try:
        yield connection
    except apsw.Error:
        # An errored connection may hold broken state; drop it from the cache
        # so the next call starts clean rather than inheriting the wreck.
        _drop_cached(str(Path(path)), connection)
        raise


def fetch_one(
    connection: apsw.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> Row | None:
    return connection.execute(sql, parameters).fetchone()


def fetch_all(
    connection: apsw.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> list[Row]:
    return list(connection.execute(sql, parameters))


def mutate(
    path: str | Path,
    operation: Callable[[apsw.Connection], T],
    *,
    attempts: int = 3,
    foreign_keys: bool = True,
) -> T:
    last_error: apsw.BusyError | None = None
    for attempt in range(attempts):
        connection = connect(path, foreign_keys=foreign_keys)
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.execute("COMMIT")
            return result
        except apsw.BusyError as exc:
            last_error = exc
            try:
                connection.execute("ROLLBACK")
            except apsw.Error:
                pass
            if attempt + 1 < attempts:
                time.sleep(0.05 * (attempt + 1))
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except apsw.Error:
                pass
            raise
        finally:
            connection.close()
    LOGGER.critical("SQLITE_BUSY remained after %s bounded attempts", attempts)
    assert last_error is not None, "busy retry exhausted without a BusyError"
    raise DatabaseBusy("catalog is busy; mutation was not committed") from last_error


def _statements(script: str) -> Iterator[str]:
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if apsw.complete(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    if buffer.strip():
        raise RuntimeError("incomplete web schema statement")


# One row per song, holding every scrap of text worth digging through. Kept in
# sync by rebuilding a song's row rather than trying to patch fields in place:
# tags and aliases live in other tables, and "delete then insert this one song"
# is a rule that stays correct no matter which of them changed.
SEARCH_ROW_SQL = """
INSERT INTO songs_search(rowid, title, slug, aliases, tags, notes)
SELECT s.id, COALESCE(s.title, ''), COALESCE(s.slug, ''),
       COALESCE((SELECT GROUP_CONCAT(a.alias_slug, ' ')
                 FROM song_aliases AS a WHERE a.song_id=s.id), ''),
       COALESCE((SELECT GROUP_CONCAT(t.value, ' ')
                 FROM song_tags AS t WHERE t.song_id=s.id), ''),
       COALESCE(s.notes, '')
FROM songs AS s
WHERE s.id={song}
"""

SEARCH_TRIGGERS_SQL = (
    # songs: any edit to the text we index rebuilds that song's row.
    """
    CREATE TRIGGER IF NOT EXISTS songs_search_ai AFTER INSERT ON songs BEGIN
      INSERT INTO songs_search(rowid, title, slug, aliases, tags, notes)
      VALUES (new.id, COALESCE(new.title,''), COALESCE(new.slug,''), '', '',
              COALESCE(new.notes,''));
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS songs_search_ad AFTER DELETE ON songs BEGIN
      DELETE FROM songs_search WHERE rowid=old.id;
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS songs_search_au
    AFTER UPDATE OF title, slug, notes ON songs BEGIN
      DELETE FROM songs_search WHERE rowid=new.id;
      INSERT INTO songs_search(rowid, title, slug, aliases, tags, notes)
      SELECT new.id, COALESCE(new.title,''), COALESCE(new.slug,''),
             COALESCE((SELECT GROUP_CONCAT(a.alias_slug,' ') FROM song_aliases AS a
                       WHERE a.song_id=new.id), ''),
             COALESCE((SELECT GROUP_CONCAT(t.value,' ') FROM song_tags AS t
                       WHERE t.song_id=new.id), ''),
             COALESCE(new.notes,'');
    END;
    """,
    # song_tags: tagging is how this catalogue is actually navigated, so a new
    # tag has to be findable immediately.
    """
    CREATE TRIGGER IF NOT EXISTS song_tags_search_ai AFTER INSERT ON song_tags BEGIN
      DELETE FROM songs_search WHERE rowid=new.song_id;
      INSERT INTO songs_search(rowid, title, slug, aliases, tags, notes)
      SELECT s.id, COALESCE(s.title,''), COALESCE(s.slug,''),
             COALESCE((SELECT GROUP_CONCAT(a.alias_slug,' ') FROM song_aliases AS a
                       WHERE a.song_id=s.id), ''),
             COALESCE((SELECT GROUP_CONCAT(t.value,' ') FROM song_tags AS t
                       WHERE t.song_id=s.id), ''),
             COALESCE(s.notes,'')
      FROM songs AS s WHERE s.id=new.song_id;
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS song_tags_search_ad AFTER DELETE ON song_tags BEGIN
      DELETE FROM songs_search WHERE rowid=old.song_id;
      INSERT INTO songs_search(rowid, title, slug, aliases, tags, notes)
      SELECT s.id, COALESCE(s.title,''), COALESCE(s.slug,''),
             COALESCE((SELECT GROUP_CONCAT(a.alias_slug,' ') FROM song_aliases AS a
                       WHERE a.song_id=s.id), ''),
             COALESCE((SELECT GROUP_CONCAT(t.value,' ') FROM song_tags AS t
                       WHERE t.song_id=s.id), ''),
             COALESCE(s.notes,'')
      FROM songs AS s WHERE s.id=old.song_id;
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS song_aliases_search_ai
    AFTER INSERT ON song_aliases BEGIN
      DELETE FROM songs_search WHERE rowid=new.song_id;
      INSERT INTO songs_search(rowid, title, slug, aliases, tags, notes)
      SELECT s.id, COALESCE(s.title,''), COALESCE(s.slug,''),
             COALESCE((SELECT GROUP_CONCAT(a.alias_slug,' ') FROM song_aliases AS a
                       WHERE a.song_id=s.id), ''),
             COALESCE((SELECT GROUP_CONCAT(t.value,' ') FROM song_tags AS t
                       WHERE t.song_id=s.id), ''),
             COALESCE(s.notes,'')
      FROM songs AS s WHERE s.id=new.song_id;
    END;
    """,
)


def rebuild_search_index(
    connection: apsw.Connection, *, force: bool = False
) -> None:
    """Fill songs_search and install the triggers that keep it current."""
    # Older databases predate song_aliases. Indexing is not worth refusing to
    # boot over, so search without them rather than failing the migration.
    has_aliases = (
        fetch_one(
            connection,
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='song_aliases'",
        )
        is not None
    )
    for statement in SEARCH_TRIGGERS_SQL:
        if "song_aliases" in statement and not has_aliases:
            continue
        connection.execute(statement)
    aliases_sql = (
        "COALESCE((SELECT GROUP_CONCAT(a.alias_slug, ' ') "
        "FROM song_aliases AS a WHERE a.song_id=s.id), '')"
        if has_aliases
        else "''"
    )
    indexed = int(fetch_one(connection, "SELECT COUNT(*) FROM songs_search")[0])
    songs = int(fetch_one(connection, "SELECT COUNT(*) FROM songs")[0])
    if not force and indexed == songs:
        return
    connection.execute("DELETE FROM songs_search")
    connection.execute(
        f"""
        INSERT INTO songs_search(rowid, title, slug, aliases, tags, notes)
        SELECT s.id, COALESCE(s.title, ''), COALESCE(s.slug, ''),
               {aliases_sql},
               COALESCE((SELECT GROUP_CONCAT(t.value, ' ')
                         FROM song_tags AS t WHERE t.song_id=s.id), ''),
               COALESCE(s.notes, '')
        FROM songs AS s
        """
    )


def migrate(path: str | Path) -> None:
    try:
        with reading(path) as connection:
            current = fetch_one(
                connection,
                "SELECT value FROM meta WHERE key='web_schema_version'",
            )
    except apsw.Error:
        current = None
    if current is not None and str(current["value"]) == str(WEB_SCHEMA_VERSION):
        return

    def migrate_songs(connection: apsw.Connection) -> bool:
        if not songs_need_released_migration(connection):
            return False
        rebuild_songs_for_released(connection)
        return True

    songs_migrated = mutate(
        path,
        migrate_songs,
        foreign_keys=False,
    )

    def apply(connection: apsw.Connection) -> None:
        song_tags_row = fetch_one(
            connection,
            """
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name='song_tags'
            """,
        )
        song_tags_sql = (
            str(song_tags_row["sql"]) if song_tags_row is not None else ""
        )
        if not song_tags_sql:
            connection.execute(
                """
                CREATE TABLE song_tags (
                  song_id INTEGER NOT NULL REFERENCES songs(id),
                  dim TEXT NOT NULL
                    CHECK(dim IN (
                      'vibe','instr','collab','use','problem'
                    )),
                  value TEXT NOT NULL,
                  source TEXT NOT NULL DEFAULT 'human',
                  author TEXT,
                  created_at TEXT,
                  PRIMARY KEY(song_id, dim, value)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX idx_song_tags_dim_value
                ON song_tags(dim, value)
                """
            )
        elif "'use'" not in song_tags_sql or "'problem'" not in song_tags_sql:
            connection.execute(
                """
                CREATE TABLE song_tags_v9 (
                  song_id INTEGER NOT NULL REFERENCES songs(id),
                  dim TEXT NOT NULL
                    CHECK(dim IN (
                      'vibe','instr','collab','use','problem'
                    )),
                  value TEXT NOT NULL,
                  source TEXT NOT NULL DEFAULT 'human',
                  author TEXT,
                  created_at TEXT,
                  PRIMARY KEY(song_id, dim, value)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO song_tags_v9(
                  song_id, dim, value, source, author, created_at
                )
                SELECT song_id, dim, value, source, author, created_at
                FROM song_tags
                """
            )
            connection.execute("DROP TABLE song_tags")
            connection.execute(
                "ALTER TABLE song_tags_v9 RENAME TO song_tags"
            )
            connection.execute(
                """
                CREATE INDEX idx_song_tags_dim_value
                ON song_tags(dim, value)
                """
            )
        for statement in _statements(WEB_SCHEMA_SQL):
            connection.execute(statement)
        share_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(shares)")
        }
        if "kind" in share_columns:
            connection.execute(
                SHARES_TABLE_SQL.replace(
                    "IF NOT EXISTS shares", "shares_v5", 1
                )
            )
            connection.execute(
                """
                INSERT INTO shares_v5(
                  id, ulid, label, token_sha256, scope_mode, scope_json,
                  expires_at, max_uses, use_count, revoked_at, created_at,
                  include_stems, allow_downloads
                )
                SELECT id, ulid, label, token_sha256, 'live', '[]',
                       expires_at, max_uses, use_count, revoked_at, created_at,
                       COALESCE(include_stems, 0), 0
                FROM shares
                """
            )
            connection.execute("DROP TABLE shares")
            connection.execute("ALTER TABLE shares_v5 RENAME TO shares")
            share_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(shares)")
            }
        if "include_stems" not in share_columns:
            connection.execute(
                "ALTER TABLE shares ADD COLUMN include_stems INTEGER NOT NULL "
                "DEFAULT 0 CHECK(include_stems IN (0,1))"
            )
        if "scope_mode" not in share_columns:
            connection.execute(
                "ALTER TABLE shares ADD COLUMN scope_mode TEXT NOT NULL "
                "DEFAULT 'live' CHECK(scope_mode IN ('live','frozen'))"
            )
        if "allow_downloads" not in share_columns:
            connection.execute(
                "ALTER TABLE shares ADD COLUMN allow_downloads INTEGER NOT NULL "
                "DEFAULT 0 CHECK(allow_downloads IN (0,1))"
            )
        if "landing_collection_id" not in share_columns:
            connection.execute(
                "ALTER TABLE shares ADD COLUMN landing_collection_id INTEGER "
                "REFERENCES collections(id)"
            )
        if "note" not in share_columns:
            connection.execute("ALTER TABLE shares ADD COLUMN note TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS presence (
              username TEXT PRIMARY KEY,
              bounce_ulid TEXT NOT NULL,
              updated_at REAL NOT NULL
            )
            """
        )
        invite_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(invites)")
        }
        if "bounce_ulid" not in invite_columns:
            connection.execute("ALTER TABLE invites ADD COLUMN bounce_ulid TEXT")
        reaction_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(reactions)")
        }
        if "timecode_s" not in reaction_columns:
            connection.execute(
                "ALTER TABLE reactions ADD COLUMN timecode_s REAL"
            )
        backfill_eras(connection)
        backfill_derived_tags(connection)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_shares_created "
            "ON shares(created_at DESC)"
        )
        violations = list(connection.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError("released-song migration broke foreign keys")
        indexed = int(fetch_one(connection, "SELECT COUNT(*) FROM songs_fts")[0])
        songs = int(fetch_one(connection, "SELECT COUNT(*) FROM songs")[0])
        if songs_migrated or indexed != songs:
            connection.execute("INSERT INTO songs_fts(songs_fts) VALUES('rebuild')")
        rebuild_search_index(connection, force=songs_migrated)
        connection.execute(
            """
            INSERT INTO meta(key, value) VALUES('web_schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(WEB_SCHEMA_VERSION),),
        )

    mutate(path, apply)


def create_alert(
    path: str | Path,
    *,
    kind: str,
    message: str,
    share_id: int | None = None,
    severity: str = "warning",
) -> None:
    def insert(connection: apsw.Connection) -> None:
        existing = fetch_one(
            connection,
            """
            SELECT id FROM app_alerts
            WHERE kind=? AND share_id IS ? AND acknowledged_at IS NULL
              AND created_at >= datetime('now', '-1 hour')
            LIMIT 1
            """,
            (kind, share_id),
        )
        if existing is None:
            connection.execute(
                """
                INSERT INTO app_alerts(
                  severity, kind, share_id, message, created_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (severity, kind, share_id, message[:500], utc_now()),
            )

    try:
        mutate(path, insert, attempts=1)
    except DatabaseBusy:
        LOGGER.critical("could not persist owner alert: %s", message)
