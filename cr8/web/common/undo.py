"""Pure DB undo stack for owner tag/heart/bulk writes."""

from __future__ import annotations

import json
from typing import Any

from ...db import utc_now
from .database import fetch_one, mutate, reading
from .settings import AppSettings
from .text import clean_text


def insert_undo(
    connection: Any,
    *,
    session_id: int,
    kind: str,
    label: str,
    payload: dict[str, Any],
) -> int:
    connection.execute(
        """
        INSERT INTO undo_entries(
          session_id, kind, label, payload_json, created_at
        ) VALUES(?, ?, ?, ?, ?)
        """,
        (
            session_id,
            kind,
            label,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            utc_now(),
        ),
    )
    return int(connection.last_insert_rowid())


def push_undo(
    settings: AppSettings,
    *,
    session_id: int,
    kind: str,
    label: str,
    payload: dict[str, Any],
) -> int:
    return mutate(
        settings.db_path,
        lambda connection: insert_undo(
            connection,
            session_id=session_id,
            kind=kind,
            label=label,
            payload=payload,
        ),
    )


def snapshot_tag_write(
    settings: AppSettings,
    *,
    song_ulid: str,
    dim: str,
    value: str,
) -> tuple[str, dict[str, Any]]:
    dimension = clean_text(dim, limit=20).casefold()
    tag_value = clean_text(value, limit=40)
    if dimension in {"vibe", "instr", "collab", "use", "problem"}:
        tag_value = tag_value.casefold()
    with reading(settings.db_path) as connection:
        song = fetch_one(
            connection,
            """
            SELECT id, public_id, status, keeper, key_canon, key_source,
                   human_touched
            FROM songs WHERE public_id=?
            """,
            (song_ulid,),
        )
        if song is None:
            raise ValueError("song unavailable")
        if dimension in {"status", "keeper", "key"}:
            fields = (
                ("status", "human_touched")
                if dimension == "status"
                else ("keeper", "human_touched")
                if dimension == "keeper"
                else ("key_canon", "key_source", "human_touched")
            )
            return (
                "field",
                {
                    "song_id": int(song["id"]),
                    "song_ulid": song_ulid,
                    "fields": {field: song[field] for field in fields},
                },
            )
        prior = fetch_one(
            connection,
            """
            SELECT source, author, created_at FROM song_tags
            WHERE song_id=? AND dim=? AND value=?
            """,
            (int(song["id"]), dimension, tag_value),
        )
    return (
        "tag",
        {
            "song_id": int(song["id"]),
            "song_ulid": song_ulid,
            "dim": dimension,
            "value": tag_value,
            "prior": dict(prior) if prior is not None else None,
        },
    )


def restore_tag(
    connection: Any, change: dict[str, Any], *, actor: str
) -> None:
    prior = change.get("prior")
    parameters = (
        int(change["song_id"]),
        str(change["dim"]),
        str(change["value"]),
    )
    if prior is None:
        connection.execute(
            "DELETE FROM song_tags WHERE song_id=? AND dim=? AND value=?",
            parameters,
        )
    else:
        connection.execute(
            """
            INSERT INTO song_tags(
              song_id, dim, value, source, author, created_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(song_id, dim, value) DO UPDATE SET
              source=excluded.source,
              author=excluded.author,
              created_at=excluded.created_at
            """,
            (
                *parameters,
                prior.get("source"),
                prior.get("author"),
                prior.get("created_at"),
            ),
        )
    bounce = fetch_one(
        connection,
        """
        SELECT public_id FROM bounces WHERE song_id=?
        ORDER BY COALESCE(bounce_date,'') DESC,
                 COALESCE(version,0) DESC, id DESC LIMIT 1
        """,
        (int(change["song_id"]),),
    )
    if bounce is not None:
        connection.execute(
            """
            INSERT INTO reactions(
              bounce_ulid, song_id, actor, kind, dim, value, created_at
            ) VALUES(?, ?, ?, 'chip', ?, ?, ?)
            """,
            (
                str(bounce["public_id"]),
                int(change["song_id"]),
                f"{actor}:audit:undo",
                str(change["dim"]),
                str(change["value"]),
                utc_now(),
            ),
        )


def undo_last(
    settings: AppSettings, *, session_id: int, actor: str
) -> dict[str, Any] | None:
    def apply(connection: Any) -> dict[str, Any] | None:
        entry = fetch_one(
            connection,
            """
            SELECT * FROM undo_entries
            WHERE session_id=? AND undone_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (session_id,),
        )
        if entry is None:
            return None
        payload = json.loads(str(entry["payload_json"]))
        kind = str(entry["kind"])
        song_ulids: list[str] = []
        if kind == "heart":
            bounce_ulid = str(payload["bounce_ulid"])
            active = fetch_one(
                connection,
                """
                SELECT id FROM reactions
                WHERE bounce_ulid=? AND actor=? AND kind='heart'
                  AND deleted_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (bounce_ulid, actor),
            )
            desired = bool(payload["prior_active"])
            if desired and active is None:
                connection.execute(
                    """
                    INSERT INTO reactions(
                      bounce_ulid, song_id, actor, kind, created_at
                    ) VALUES(?, ?, ?, 'heart', ?)
                    """,
                    (
                        bounce_ulid,
                        int(payload["song_id"]),
                        actor,
                        utc_now(),
                    ),
                )
            elif not desired and active is not None:
                connection.execute(
                    "UPDATE reactions SET deleted_at=? WHERE id=?",
                    (utc_now(), int(active["id"])),
                )
            song_ulids.append(str(payload["song_ulid"]))
        elif kind == "tag":
            restore_tag(connection, payload, actor=actor)
            song_ulids.append(str(payload["song_ulid"]))
        elif kind == "field":
            fields = dict(payload["fields"])
            assignments = ", ".join(f"{field}=?" for field in fields)
            connection.execute(
                f"UPDATE songs SET {assignments} WHERE id=?",
                (*fields.values(), int(payload["song_id"])),
            )
            song_ulids.append(str(payload["song_ulid"]))
        elif kind == "bulk":
            for change in payload.get("tags", []):
                restore_tag(connection, change, actor=actor)
                song_ulids.append(str(change["song_ulid"]))
            for change in payload.get("fields", []):
                fields = dict(change["values"])
                assignments = ", ".join(f"{field}=?" for field in fields)
                connection.execute(
                    f"UPDATE songs SET {assignments} WHERE id=?",
                    (*fields.values(), int(change["song_id"])),
                )
                song_ulids.append(str(change["song_ulid"]))
        else:
            raise RuntimeError("unknown undo entry")
        connection.execute(
            "UPDATE undo_entries SET undone_at=? WHERE id=?",
            (utc_now(), int(entry["id"])),
        )
        return {
            "label": str(entry["label"]),
            "song_ulids": list(dict.fromkeys(song_ulids)),
        }

    return mutate(settings.db_path, apply)
