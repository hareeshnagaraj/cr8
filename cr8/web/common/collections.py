"""Ordered hand-built song collections."""

from __future__ import annotations

from typing import Any

from ...db import utc_now
from ...public_ids import new_ulid
from .database import Row, fetch_all, fetch_one, mutate, reading
from .queries import track_by_ulid
from .settings import AppSettings
from .text import clean_text


def collection_summaries(settings: AppSettings) -> list[Row]:
    with reading(settings.db_path) as connection:
        return fetch_all(
            connection,
            """
            SELECT c.*, COUNT(ci.bounce_ulid) AS track_count
            FROM collections AS c
            LEFT JOIN collection_items AS ci ON ci.collection_id=c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC, c.name COLLATE NOCASE
            """,
        )


def collection_summary(
    settings: AppSettings, collection_ulid: str
) -> Row | None:
    with reading(settings.db_path) as connection:
        return fetch_one(
            connection,
            """
            SELECT c.*, COUNT(ci.bounce_ulid) AS track_count
            FROM collections AS c
            LEFT JOIN collection_items AS ci ON ci.collection_id=c.id
            WHERE c.ulid=?
            GROUP BY c.id
            """,
            (collection_ulid,),
        )


def collection_bounce_ulids(
    settings: AppSettings, collection_ulid: str
) -> list[str]:
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT ci.bounce_ulid
            FROM collection_items AS ci
            JOIN collections AS c ON c.id=ci.collection_id
            WHERE c.ulid=?
            ORDER BY ci.position
            """,
            (collection_ulid,),
        )
    return [str(row["bounce_ulid"]) for row in rows]


def collection_song_ids(
    settings: AppSettings, collection_ulid: str
) -> set[int]:
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT DISTINCT b.song_id
            FROM collection_items AS ci
            JOIN collections AS c ON c.id=ci.collection_id
            JOIN bounces AS b ON b.public_id=ci.bounce_ulid
            WHERE c.ulid=?
            """,
            (collection_ulid,),
        )
    return {int(row["song_id"]) for row in rows}


def collection_tracks(
    settings: AppSettings, collection_ulid: str
) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for bounce_ulid in collection_bounce_ulids(settings, collection_ulid):
        track = track_by_ulid(settings, bounce_ulid)
        if track is not None:
            tracks.append(track)
    return tracks


def publicly_streamable_bounce_ulids(
    connection: Any, bounce_ulids: list[str]
) -> set[str]:
    """Return collection items backed by a mirrored bounce or mirrored stem."""
    if not bounce_ulids:
        return set()
    placeholders = ",".join("?" for _ in bounce_ulids)
    return {
        str(row["public_id"])
        for row in connection.execute(
            f"""
            SELECT b.public_id FROM bounces AS b
            JOIN mirror_files AS mf ON mf.bounce_id=b.id
            WHERE b.public_id IN ({placeholders})
            UNION
            SELECT st.public_id FROM stems AS st
            WHERE st.public_id IN ({placeholders})
              AND st.mirror_relpath IS NOT NULL
            """,
            [*bounce_ulids, *bounce_ulids],
        )
    }


def collection_share_snapshot(
    settings: AppSettings, collection_ulid: str
) -> tuple[Row | None, list[str], list[str]]:
    """Read one ordered snapshot and name anything it cannot safely expose."""
    with reading(settings.db_path) as connection:
        collection = fetch_one(
            connection,
            "SELECT * FROM collections WHERE ulid=?",
            (collection_ulid,),
        )
        if collection is None:
            return None, [], []
        rows = fetch_all(
            connection,
            """
            SELECT ci.bounce_ulid,
                   COALESCE(
                     (SELECT s.title
                      FROM bounces AS b JOIN songs AS s ON s.id=b.song_id
                      WHERE b.public_id=ci.bounce_ulid),
                     (SELECT s.title
                      FROM stems AS st
                      JOIN bounces AS b ON b.id=st.bounce_id
                      JOIN songs AS s ON s.id=b.song_id
                      WHERE st.public_id=ci.bounce_ulid),
                     ci.bounce_ulid
                   ) AS title
            FROM collection_items AS ci
            WHERE ci.collection_id=?
            ORDER BY ci.position
            """,
            (int(collection["id"]),),
        )
        ordered = [str(row["bounce_ulid"]) for row in rows]
        available = publicly_streamable_bounce_ulids(connection, ordered)
    unavailable = [
        str(row["title"])
        for row in rows
        if str(row["bounce_ulid"]) not in available
    ]
    return collection, ordered, unavailable


def latest_bounces_for_songs(
    connection: Any, song_ulids: list[str]
) -> list[str]:
    if not song_ulids:
        return []
    rows = list(
        connection.execute(
            """
            WITH ranked AS (
              SELECT s.public_id AS song_ulid, b.public_id AS bounce_ulid,
                     ROW_NUMBER() OVER (
                       PARTITION BY s.id
                       ORDER BY COALESCE(b.bounce_date, '') DESC,
                                COALESCE(b.version, 0) DESC, b.id DESC
                     ) AS newest
              FROM songs AS s
              JOIN bounces AS b ON b.song_id=s.id
              JOIN mirror_files AS mf ON mf.bounce_id=b.id
              WHERE s.public_id IN ({})
            )
            SELECT song_ulid, bounce_ulid FROM ranked WHERE newest=1
            """.format(",".join("?" for _ in song_ulids)),
            song_ulids,
        )
    )
    found = {
        str(row["song_ulid"]): str(row["bounce_ulid"]) for row in rows
    }
    if set(found) != set(song_ulids):
        raise ValueError("a selected song is unavailable")
    return [found[ulid] for ulid in song_ulids]


def create_collection(
    settings: AppSettings,
    *,
    name: str,
    notes: str = "",
    bounce_ulids: list[str] | None = None,
    song_ulids: list[str] | None = None,
) -> str:
    title = clean_text(name, limit=80)
    note = clean_text(notes, limit=500)
    if not title:
        raise ValueError("collection needs a name")
    requested_bounces = list(dict.fromkeys(bounce_ulids or []))
    requested_songs = list(dict.fromkeys(song_ulids or []))
    if not requested_bounces and not requested_songs:
        raise ValueError("collection needs at least one song")
    ulid = new_ulid()

    def insert(connection: Any) -> None:
        items = requested_bounces
        if requested_songs:
            items = latest_bounces_for_songs(connection, requested_songs)
        if items:
            # A library row's ulid is either a mirrored bounce or a separated
            # stem — the newest rows are often stems, and track_by_ulid plays
            # both. Anything playable belongs in a collection.
            available = publicly_streamable_bounce_ulids(connection, items)
            if available != set(items):
                raise ValueError("a collection song is unavailable")
        connection.execute(
            """
            INSERT INTO collections(ulid, name, notes, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (ulid, title, note or None, utc_now()),
        )
        collection_id = int(connection.last_insert_rowid())
        connection.executemany(
            """
            INSERT INTO collection_items(
              collection_id, bounce_ulid, position
            ) VALUES(?, ?, ?)
            """,
            [
                (collection_id, bounce_ulid, position)
                for position, bounce_ulid in enumerate(items)
            ],
        )

    mutate(settings.db_path, insert)
    return ulid


def reorder_collection(
    settings: AppSettings,
    *,
    collection_ulid: str,
    bounce_ulids: list[str],
) -> None:
    ordered = list(dict.fromkeys(bounce_ulids))

    def reorder(connection: Any) -> None:
        collection = fetch_one(
            connection,
            "SELECT id FROM collections WHERE ulid=?",
            (collection_ulid,),
        )
        if collection is None:
            raise ValueError("collection unavailable")
        collection_id = int(collection["id"])
        existing = {
            str(row["bounce_ulid"])
            for row in connection.execute(
                """
                SELECT bounce_ulid FROM collection_items
                WHERE collection_id=?
                """,
                (collection_id,),
            )
        }
        if existing != set(ordered) or len(existing) != len(ordered):
            raise ValueError("collection order is incomplete")
        connection.execute(
            """
            UPDATE collection_items
            SET position=position+1000000
            WHERE collection_id=?
            """,
            (collection_id,),
        )
        connection.executemany(
            """
            UPDATE collection_items SET position=?
            WHERE collection_id=? AND bounce_ulid=?
            """,
            [
                (position, collection_id, bounce_ulid)
                for position, bounce_ulid in enumerate(ordered)
            ],
        )

    mutate(settings.db_path, reorder)


def remove_collection_track(
    settings: AppSettings,
    *,
    collection_ulid: str,
    bounce_ulid: str,
) -> None:
    def remove(connection: Any) -> None:
        collection = fetch_one(
            connection,
            "SELECT id FROM collections WHERE ulid=?",
            (collection_ulid,),
        )
        if collection is None:
            raise ValueError("collection unavailable")
        collection_id = int(collection["id"])
        connection.execute(
            """
            DELETE FROM collection_items
            WHERE collection_id=? AND bounce_ulid=?
            """,
            (collection_id, bounce_ulid),
        )
        if connection.changes() != 1:
            raise ValueError("collection song unavailable")
        remaining = [
            str(row["bounce_ulid"])
            for row in connection.execute(
                """
                SELECT bounce_ulid FROM collection_items
                WHERE collection_id=? ORDER BY position
                """,
                (collection_id,),
            )
        ]
        connection.execute(
            """
            UPDATE collection_items
            SET position=position+1000000
            WHERE collection_id=?
            """,
            (collection_id,),
        )
        connection.executemany(
            """
            UPDATE collection_items SET position=?
            WHERE collection_id=? AND bounce_ulid=?
            """,
            [
                (position, collection_id, item)
                for position, item in enumerate(remaining)
            ],
        )

    mutate(settings.db_path, remove)


def delete_collection(
    settings: AppSettings,
    *,
    collection_ulid: str,
) -> None:
    def delete(connection: Any) -> None:
        connection.execute(
            "DELETE FROM collections WHERE ulid=?",
            (collection_ulid,),
        )
        if connection.changes() != 1:
            raise ValueError("collection unavailable")

    mutate(settings.db_path, delete)
