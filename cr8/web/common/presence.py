"""Transactional listener presence writes and one-query read projection."""

from __future__ import annotations

import time
from typing import Any

from .database import fetch_all, mutate, reading
from .settings import AppSettings
from .text import era_css


def touch(
    settings: AppSettings,
    *,
    actor: str,
    bounce_ulid: str,
    started: bool,
    connection: Any | None = None,
) -> None:
    """Refresh one listener without moving it on a late prior-track flush."""
    now = time.time()

    def write(active: Any) -> None:
        active.execute(
            "DELETE FROM presence WHERE updated_at < ?",
            (now - 3600,),
        )
        active.execute(
            """
            INSERT INTO presence(username, bounce_ulid, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
              bounce_ulid=excluded.bounce_ulid,
              updated_at=excluded.updated_at
            WHERE ? OR presence.bounce_ulid=excluded.bounce_ulid
            """,
            (actor, bounce_ulid, now, int(started)),
        )

    if connection is not None:
        write(connection)
    else:
        mutate(settings.db_path, write)


def listeners(
    settings: AppSettings, *, window_s: int = 60
) -> list[dict[str, Any]]:
    now = time.time()
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT p.username, p.bounce_ulid, p.updated_at,
                   s.public_id AS song_ulid, s.title,
                   s.key_canon, s.bpm,
                   er.name AS era_name
            FROM presence AS p
            JOIN bounces AS b ON b.public_id=p.bounce_ulid
            JOIN songs AS s ON s.id=b.song_id
            LEFT JOIN eras AS er ON er.id=s.era_id
            WHERE p.updated_at > ?
            ORDER BY p.updated_at DESC
            """,
            (now - max(1, window_s),),
        )
    result: list[dict[str, Any]] = []
    for row in rows:
        name = str(row["era_name"] or "undated")
        result.append(
            {
                "actor": str(row["username"]),
                "bounce_ulid": str(row["bounce_ulid"]),
                "song_ulid": str(row["song_ulid"]),
                "title": str(row["title"]),
                "key_canon": row["key_canon"],
                "bpm": row["bpm"],
                "era": name,
                "era_css": era_css(name),
                "seen_s_ago": int(
                    max(0.0, now - float(row["updated_at"]))
                ),
            }
        )
    return result
