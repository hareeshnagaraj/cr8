"""Authenticated tag editing and vocabulary management."""

from __future__ import annotations

from typing import Any

from ...db import utc_now
from .database import Row, fetch_all, fetch_one, mutate, reading
from .settings import AppSettings
from .text import clean_text


TAG_DIMS = ("vibe", "instr", "collab", "use", "problem")
VISIBLE_TAG_DIMS = ("vibe", "instr", "collab", "use")
STATUSES = ("idea", "jam", "demo", "mixed", "finished", "released")


def song_tag_panel(
    settings: AppSettings, song_ulid: str
) -> dict[str, Any] | None:
    with reading(settings.db_path) as connection:
        song = fetch_one(
            connection,
            """
            SELECT id, public_id, title, status, keeper, key_canon,
                   human_touched
            FROM songs WHERE public_id=?
            """,
            (song_ulid,),
        )
        if song is None:
            return None
        tags = fetch_all(
            connection,
            """
            SELECT dim, value, source, author FROM song_tags
            WHERE song_id=? ORDER BY dim, value COLLATE NOCASE
            """,
            (int(song["id"]),),
        )
        vocab: dict[str, list[str]] = {}
        vocab_options: dict[str, list[dict[str, Any]]] = {}
        for dim in VISIBLE_TAG_DIMS:
            rows = fetch_all(
                connection,
                """
                SELECT value, COUNT(DISTINCT song_id) AS frequency
                FROM song_tags WHERE dim=?
                GROUP BY value
                ORDER BY frequency DESC, value COLLATE NOCASE
                LIMIT 30
                """,
                (dim,),
            )
            vocab[dim] = [str(row["value"]) for row in rows]
            vocab_options[dim] = [
                {
                    "value": str(row["value"]),
                    "count": int(row["frequency"]),
                }
                for row in rows
            ]
        key_rows = fetch_all(
            connection,
            """
            SELECT key_canon AS value, COUNT(*) AS frequency
            FROM songs WHERE key_canon IS NOT NULL AND key_canon!=''
            GROUP BY key_canon
            ORDER BY frequency DESC, key_canon COLLATE NOCASE
            LIMIT 30
            """,
        )
    active = {
        dim: {
            str(tag["value"]): str(tag["source"])
            for tag in tags
            if tag["dim"] == dim
        }
        for dim in VISIBLE_TAG_DIMS
    }
    dimensions: list[dict[str, Any]] = [
        {
            "name": "status",
            "values": [
                {
                    "value": value,
                    "active": value == song["status"],
                    "source": (
                        "human"
                        if value == song["status"] and song["human_touched"]
                        else "judgment"
                    ),
                }
                for value in STATUSES
            ],
            "allow_new": False,
        },
        {
            "name": "keeper",
            "values": [
                {
                    "value": str(value),
                    "active": value == int(song["keeper"] or 0),
                    "source": (
                        "human"
                        if value == int(song["keeper"] or 0)
                        and song["human_touched"]
                        else "judgment"
                    ),
                }
                for value in range(6)
            ],
            "allow_new": False,
        },
        {
            "name": "key",
            "values": [
                {
                    "value": str(row["value"]),
                    "active": str(row["value"]) == str(song["key_canon"] or ""),
                    "source": (
                        "human"
                        if str(row["value"]) == str(song["key_canon"] or "")
                        and song["human_touched"]
                        else "catalog"
                    ),
                }
                for row in key_rows
            ],
            "allow_new": True,
        },
    ]
    for dim in VISIBLE_TAG_DIMS:
        values = list(dict.fromkeys((*active[dim], *vocab[dim])))
        dimensions.append(
            {
                "name": dim,
                "values": [
                    {
                        "value": value,
                        "active": value in active[dim],
                        "source": active[dim].get(value, ""),
                        "actor": next(
                            (
                                str(tag["author"] or "")
                                for tag in tags
                                if tag["dim"] == dim
                                and str(tag["value"]) == value
                            ),
                            "",
                        ),
                    }
                    for value in values
                ],
                "allow_new": True,
            }
        )
    return {
        "song": dict(song),
        "dimensions": dimensions,
        "vocab": vocab,
        "vocab_options": vocab_options,
    }


def toggle_song_tag(
    settings: AppSettings,
    *,
    song_ulid: str,
    dim: str,
    value: str,
    actor: str,
    bounce_ulid: str | None = None,
) -> dict[str, Any]:
    dimension = clean_text(dim, limit=20).casefold()
    tag_value = clean_text(value, limit=40)
    if dimension in TAG_DIMS:
        tag_value = tag_value.casefold()
    if not tag_value:
        raise ValueError("tag needs a value")
    if dimension not in {*TAG_DIMS, "status", "keeper", "key"}:
        raise ValueError("invalid tag dimension")
    if dimension == "status" and tag_value not in STATUSES:
        raise ValueError("invalid status")
    if dimension == "keeper" and tag_value not in {
        "0", "1", "2", "3", "4", "5"
    }:
        raise ValueError("invalid keeper")

    def update(connection: Any) -> dict[str, Any]:
        song = fetch_one(
            connection,
            "SELECT id FROM songs WHERE public_id=?",
            (song_ulid,),
        )
        if song is None:
            raise ValueError("song unavailable")
        song_id = int(song["id"])
        if dimension == "status":
            connection.execute(
                """
                UPDATE songs SET status=?, human_touched=1 WHERE id=?
                """,
                (tag_value, song_id),
            )
            return {"active": True, "action": "set"}
        if dimension == "keeper":
            connection.execute(
                """
                UPDATE songs SET keeper=?, human_touched=1 WHERE id=?
                """,
                (int(tag_value), song_id),
            )
            return {"active": True, "action": "set"}
        if dimension == "key":
            connection.execute(
                """
                UPDATE songs
                SET key_canon=?, key_source='human', human_touched=1
                WHERE id=?
                """,
                (tag_value, song_id),
            )
            return {"active": True, "action": "set"}
        existing = fetch_one(
            connection,
            """
            SELECT source FROM song_tags
            WHERE song_id=? AND dim=? AND value=?
            """,
            (song_id, dimension, tag_value),
        )
        if existing is not None and str(existing["source"]) == "human":
            connection.execute(
                """
                DELETE FROM song_tags
                WHERE song_id=? AND dim=? AND value=? AND source='human'
                """,
                (song_id, dimension, tag_value),
            )
            active = False
            action = "remove"
        else:
            connection.execute(
                """
                INSERT INTO song_tags(
                  song_id, dim, value, source, author, created_at
                ) VALUES(?, ?, ?, 'human', ?, ?)
                ON CONFLICT(song_id, dim, value) DO UPDATE SET
                  source='human', author=excluded.author,
                  created_at=excluded.created_at
                """,
                (song_id, dimension, tag_value, actor, utc_now()),
            )
            active = True
            action = "add"
        provenance = bounce_ulid
        if not provenance:
            latest = fetch_one(
                connection,
                """
                SELECT public_id FROM bounces
                WHERE song_id=?
                ORDER BY COALESCE(bounce_date,'') DESC,
                         COALESCE(version,0) DESC, id DESC
                LIMIT 1
                """,
                (song_id,),
            )
            provenance = str(latest["public_id"]) if latest else song_ulid
        connection.execute(
            """
            INSERT INTO reactions(
              bounce_ulid, song_id, actor, kind, dim, value, created_at
            ) VALUES(?, ?, ?, 'chip', ?, ?, ?)
            """,
            (
                provenance,
                song_id,
                f"{actor}:audit:{action}",
                dimension,
                tag_value,
                utc_now(),
            ),
        )
        return {"active": active, "action": action}

    return mutate(settings.db_path, update)


def vocabulary_rows(settings: AppSettings) -> list[Row]:
    with reading(settings.db_path) as connection:
        return fetch_all(
            connection,
            """
            WITH all_tags AS (
              SELECT song_id, dim, value, source, author AS actor
              FROM song_tags
              UNION ALL
              SELECT song_id, dim, value, 'reaction', actor
              FROM reactions
              WHERE kind='chip' AND deleted_at IS NULL
                AND actor NOT LIKE '%:audit:%'
            )
            SELECT dim, value, COUNT(DISTINCT song_id) AS song_count,
                   GROUP_CONCAT(DISTINCT source) AS sources,
                   GROUP_CONCAT(DISTINCT actor) AS actors
            FROM all_tags
            WHERE dim IN ('vibe','instr','collab','use')
            GROUP BY dim, value
            ORDER BY dim, song_count DESC, value COLLATE NOCASE
            """,
        )


def rewrite_vocabulary(
    settings: AppSettings,
    *,
    dim: str,
    value: str,
    replacement: str | None,
) -> None:
    dimension = clean_text(dim, limit=20).casefold()
    source_value = clean_text(value, limit=40).casefold()
    target_value = clean_text(replacement or "", limit=40).casefold()
    if dimension not in TAG_DIMS or not source_value:
        raise ValueError("invalid tag")
    if replacement is not None and (
        not target_value or target_value == source_value
    ):
        raise ValueError("replacement must be different")

    def rewrite(connection: Any) -> None:
        rows = list(
            connection.execute(
                """
                SELECT song_id, source, author, created_at
                FROM song_tags WHERE dim=? AND value=?
                """,
                (dimension, source_value),
            )
        )
        if replacement is not None:
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO song_tags(
                      song_id, dim, value, source, author, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(song_id, dim, value) DO UPDATE SET
                      source=CASE
                        WHEN song_tags.source='human' THEN song_tags.source
                        ELSE excluded.source
                      END,
                      author=CASE
                        WHEN song_tags.source='human' THEN song_tags.author
                        ELSE excluded.author
                      END
                    """,
                    (
                        int(row["song_id"]),
                        dimension,
                        target_value,
                        str(row["source"]),
                        row["author"],
                        row["created_at"],
                    ),
                )
            connection.execute(
                """
                UPDATE reactions SET value=?
                WHERE kind='chip' AND dim=? AND value=? AND deleted_at IS NULL
                  AND actor NOT LIKE '%:audit:%'
                """,
                (target_value, dimension, source_value),
            )
        else:
            connection.execute(
                """
                UPDATE reactions SET deleted_at=?
                WHERE kind='chip' AND dim=? AND value=? AND deleted_at IS NULL
                  AND actor NOT LIKE '%:audit:%'
                """,
                (utc_now(), dimension, source_value),
            )
        connection.execute(
            "DELETE FROM song_tags WHERE dim=? AND value=?",
            (dimension, source_value),
        )

    mutate(settings.db_path, rewrite)
