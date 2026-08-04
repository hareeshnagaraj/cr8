"""Append-only reactions and resumable listen progress."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from ...db import utc_now
from .database import fetch_one, mutate
from .presence import touch as touch_presence
from .settings import AppSettings
from .text import clean_text


@dataclass(frozen=True)
class ReactionState:
    active: bool
    reaction_id: int | None


def _song_id(connection: Any, bounce_ulid: str) -> int:
    row = fetch_one(
        connection,
        """
        SELECT song_id FROM bounces WHERE public_id=?
        UNION ALL
        SELECT b.song_id
        FROM stems AS st JOIN bounces AS b ON b.id=st.bounce_id
        WHERE st.public_id=?
        LIMIT 1
        """,
        (bounce_ulid, bounce_ulid),
    )
    if row is None:
        raise ValueError("track is unavailable")
    return int(row["song_id"])


def toggle(
    settings: AppSettings,
    *,
    bounce_ulid: str,
    actor: str,
    kind: str,
    dim: str | None = None,
    value: str | None = None,
) -> ReactionState:
    if kind not in {"heart", "chip"}:
        raise ValueError("invalid toggle kind")
    if kind == "chip":
        value = clean_text(value or "", limit=40).casefold()
        dim = clean_text(dim or "vibe", limit=20).casefold()
        if not value or dim not in {"vibe", "instr", "collab"}:
            raise ValueError("invalid chip")

    def apply(connection: Any) -> ReactionState:
        song_id = _song_id(connection, bounce_ulid)
        existing = fetch_one(
            connection,
            """
            SELECT id FROM reactions
            WHERE bounce_ulid=? AND actor=? AND kind=?
              AND dim IS ? AND value IS ? AND deleted_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (bounce_ulid, actor, kind, dim, value),
        )
        if existing is not None:
            connection.execute(
                """
                UPDATE reactions SET deleted_at=?
                WHERE id=? AND actor=? AND deleted_at IS NULL
                """,
                (utc_now(), int(existing["id"]), actor),
            )
            return ReactionState(False, int(existing["id"]))
        connection.execute(
            """
            INSERT INTO reactions(
              bounce_ulid, song_id, actor, kind, dim, value, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (bounce_ulid, song_id, actor, kind, dim, value, utc_now()),
        )
        return ReactionState(True, int(connection.last_insert_rowid()))

    return mutate(settings.db_path, apply)


def add_note(
    settings: AppSettings,
    *,
    bounce_ulid: str,
    actor: str,
    note: str,
    timecode_s: float = 0,
) -> int:
    value = clean_text(note, limit=280)
    if not value:
        raise ValueError("note is empty")
    heard = float(timecode_s)
    if not math.isfinite(heard):
        raise ValueError("invalid note time")
    heard = max(0.0, min(heard, 86_400.0))

    def insert(connection: Any) -> int:
        song_id = _song_id(connection, bounce_ulid)
        connection.execute(
            """
            INSERT INTO reactions(
              bounce_ulid, song_id, actor, kind, value, timecode_s, created_at
            ) VALUES(?, ?, ?, 'note', ?, ?, ?)
            """,
            (bounce_ulid, song_id, actor, value, heard, utc_now()),
        )
        return int(connection.last_insert_rowid())

    return mutate(settings.db_path, insert)


def verdict(
    settings: AppSettings, *, bounce_ulid: str, actor: str, value: str
) -> int:
    if value not in {"gem", "keep", "archive"}:
        raise ValueError("invalid verdict")

    def insert(connection: Any) -> int:
        song_id = _song_id(connection, bounce_ulid)
        connection.execute(
            """
            UPDATE reactions SET deleted_at=?
            WHERE bounce_ulid=? AND actor=? AND kind='verdict'
              AND deleted_at IS NULL
            """,
            (utc_now(), bounce_ulid, actor),
        )
        connection.execute(
            """
            INSERT INTO reactions(
              bounce_ulid, song_id, actor, kind, value, created_at
            ) VALUES(?, ?, ?, 'verdict', ?, ?)
            """,
            (bounce_ulid, song_id, actor, value, utc_now()),
        )
        reaction_id = int(connection.last_insert_rowid())
        if value == "gem":
            connection.execute(
                "UPDATE songs SET keeper=MAX(keeper, 5) WHERE id=?", (song_id,)
            )
        return reaction_id

    return mutate(settings.db_path, insert)


def soft_delete(
    settings: AppSettings, *, reaction_id: int, actor: str
) -> bool:
    def remove(connection: Any) -> bool:
        row = fetch_one(
            connection,
            """
            SELECT * FROM reactions
            WHERE id=? AND actor=? AND deleted_at IS NULL
            """,
            (reaction_id, actor),
        )
        if row is None:
            return False
        connection.execute(
            """
            UPDATE reactions SET deleted_at=?
            WHERE id=? AND actor=? AND deleted_at IS NULL
            """,
            (utc_now(), reaction_id, actor),
        )
        if (
            row["kind"] == "verdict"
            and row["value"] == "gem"
            and row["song_id"] is not None
        ):
            other = fetch_one(
                connection,
                """
                SELECT 1 FROM reactions
                WHERE song_id=? AND kind='verdict'
                  AND value='gem' AND deleted_at IS NULL
                LIMIT 1
                """,
                (int(row["song_id"]),),
            )
            if other is None:
                connection.execute(
                    "UPDATE songs SET keeper=0 WHERE id=? AND keeper=5",
                    (int(row["song_id"]),),
                )
        return True

    return mutate(settings.db_path, remove)


def set_progress(
    settings: AppSettings,
    *,
    share_id: int,
    bounce_ulid: str,
    actor: str,
    state: str,
    heard_s: float,
    started: bool = False,
) -> None:
    if state not in {"unheard", "heard", "skipped"}:
        raise ValueError("invalid progress state")
    heard = max(0.0, min(float(heard_s), 86_400.0))

    def upsert(connection: Any) -> None:
        _song_id(connection, bounce_ulid)
        if started:
            connection.execute(
                """
                INSERT INTO playback_events(
                  share_id, bounce_ulid, actor, started_at
                ) VALUES(?, ?, ?, ?)
                """,
                (share_id, bounce_ulid, actor, utc_now()),
            )
        connection.execute(
            """
            INSERT INTO listen_progress(
              share_id, bounce_ulid, actor, state, heard_s, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(share_id, bounce_ulid, actor) DO UPDATE SET
              state=excluded.state,
              heard_s=MAX(listen_progress.heard_s, excluded.heard_s),
              updated_at=excluded.updated_at
            """,
            (share_id, bounce_ulid, actor, state, heard, utc_now()),
        )
        touch_presence(
            settings,
            actor=actor,
            bounce_ulid=bounce_ulid,
            started=started,
            connection=connection,
        )

    mutate(settings.db_path, upsert)
