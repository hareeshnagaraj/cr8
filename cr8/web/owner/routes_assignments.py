"""Homework: putting a track on somebody else's plate.

Two people producing separately need a way to say "listen to this" that is not
a message thread. An assignment is a small piece of shared state: it shows up on
the recipient's plate, it survives a reload, and it clears when they say so.

The state machine is the feature:

      assign               listen past threshold            explicit tap
  ──────────▶ pending ──────────────────────────▶ heard ──────────────▶ done
                 │                                  │
                 └──────────── dismiss ─────────────┴────────────▶ dismissed

Listening never completes an assignment on its own. A scrub, an accidental
autoplay, or a five second preview would all clear the list under the recipient
and they would stop trusting it. Reaching the threshold marks it *listened*;
only a person marks it *done*.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ...db import utc_now
from ...public_ids import new_ulid
from ..common.database import fetch_all, fetch_one, mutate, reading
from ..common.queries import track_by_ulid
from ..common.settings import AppSettings
from ..common.text import clean_text
from .deps import session_or_401, settings as _settings


router = APIRouter()

OPEN_STATES = ("pending", "heard")
NOTE_LIMIT = 280

# What counts as having actually listened. A minute is long enough that nobody
# reaches it by accident, and the halfway rule keeps short sketches reachable.
HEARD_SECONDS = 60.0
SHORT_TRACK_SECONDS = 120.0
SHORT_TRACK_FRACTION = 0.5


def _require_session(request: Request) -> Any:
    # Preserve this module's historical 401 detail ("login required").
    return session_or_401(request, detail="login required")


ASSIGNMENT_SQL = """
SELECT a.*
FROM listen_assignments AS a
WHERE {where}
ORDER BY a.created_at DESC, a.id DESC
"""


def _project(settings: AppSettings, row: Any) -> dict[str, Any]:
    track = track_by_ulid(settings, str(row["bounce_ulid"])) or {}
    return {
        "ulid": str(row["ulid"]),
        "bounce_ulid": str(row["bounce_ulid"]),
        "song_ulid": track.get("song_ulid"),
        "title": track.get("title") or "(missing track)",
        "key_canon": track.get("key_canon"),
        "bpm": track.get("bpm"),
        "duration_s": track.get("duration_s"),
        "era": track.get("era"),
        "date_label": track.get("date_label"),
        "assigned_by": str(row["assigned_by"]),
        "assigned_to": str(row["assigned_to"]),
        "note": row["note"] or "",
        "state": str(row["state"]),
        "created_at": str(row["created_at"]),
        "heard_at": row["heard_at"],
        "done_at": row["done_at"],
    }


@router.get("/api/members")
def members(request: Request) -> JSONResponse:
    """Who you can send a track to. Any member may address any other member;
    the roster is not privileged information inside a band of two.
    """
    session = _require_session(request)
    with reading(_settings(request).db_path) as connection:
        rows = fetch_all(
            connection,
            "SELECT username, display FROM users ORDER BY display, username",
            (),
        )
    return JSONResponse(
        {
            "members": [
                {
                    "username": str(row["username"]),
                    "display": str(row["display"] or row["username"]),
                    "is_you": str(row["username"]) == session.username,
                }
                for row in rows
            ]
        }
    )


@router.get("/api/assignments")
def my_assignments(request: Request, state: str = "open") -> JSONResponse:
    session = _require_session(request)
    settings = _settings(request)
    if state == "open":
        where = "a.assigned_to=? AND a.state IN ('pending','heard')"
        parameters: tuple[Any, ...] = (session.username,)
    else:
        if state not in {"pending", "heard", "done", "dismissed"}:
            raise HTTPException(status_code=400, detail="unknown state")
        where = "a.assigned_to=? AND a.state=?"
        parameters = (session.username, state)
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection, ASSIGNMENT_SQL.format(where=where), parameters
        )
    return JSONResponse(
        {"assignments": [_project(settings, row) for row in rows]}
    )


@router.get("/api/assignments/sent")
def sent_assignments(request: Request) -> JSONResponse:
    session = _require_session(request)
    settings = _settings(request)
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            ASSIGNMENT_SQL.format(where="a.assigned_by=?"),
            (session.username,),
        )
    return JSONResponse(
        {"assignments": [_project(settings, row) for row in rows]}
    )


@router.get("/api/assignments/count")
def assignment_count(request: Request) -> JSONResponse:
    """Deliberately cheap: this runs on a timer in the nav rail."""
    session = _require_session(request)
    with reading(_settings(request).db_path) as connection:
        row = fetch_one(
            connection,
            """
            SELECT COUNT(*) AS open_count
            FROM listen_assignments
            WHERE assigned_to=? AND state IN ('pending','heard')
            """,
            (session.username,),
        )
    return JSONResponse({"pending": int(row["open_count"] or 0)})


@router.post("/api/assignments")
async def assign(request: Request) -> JSONResponse:
    session = _require_session(request)
    settings = _settings(request)
    payload = await _json_body(request)

    recipient = clean_text(str(payload.get("to", "")), limit=40).casefold()
    if not recipient:
        raise HTTPException(status_code=400, detail="who is it for?")

    raw_ulids = payload.get("bounce_ulids")
    if not isinstance(raw_ulids, list) or not raw_ulids:
        raise HTTPException(status_code=400, detail="no tracks given")
    bounce_ulids = [str(value) for value in raw_ulids]

    note = clean_text(str(payload.get("note") or ""), limit=NOTE_LIMIT)
    now = utc_now()

    created: list[str] = []
    skipped: list[str] = []

    def insert(connection: Any) -> None:
        known = fetch_one(
            connection, "SELECT id FROM users WHERE username=?", (recipient,)
        )
        if known is None:
            raise HTTPException(status_code=400, detail="no such person")
        for bounce_ulid in bounce_ulids:
            bounce = fetch_one(
                connection,
                "SELECT id, song_id FROM bounces WHERE public_id=?",
                (bounce_ulid,),
            )
            if bounce is None:
                skipped.append(bounce_ulid)
                continue
            existing = fetch_one(
                connection,
                """
                SELECT id FROM listen_assignments
                WHERE assigned_to=? AND bounce_ulid=?
                  AND state IN ('pending','heard')
                """,
                (recipient, bounce_ulid),
            )
            if existing is not None:
                # Already on their plate. Sending it twice is not an error and
                # must not create a second card.
                skipped.append(bounce_ulid)
                continue
            ulid = new_ulid()
            connection.execute(
                """
                INSERT INTO listen_assignments(
                  ulid, bounce_ulid, song_id, assigned_to, assigned_by,
                  note, state, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    ulid,
                    bounce_ulid,
                    int(bounce["song_id"]),
                    recipient,
                    session.username,
                    note,
                    now,
                ),
            )
            created.append(ulid)

    mutate(settings.db_path, insert)

    rows: list[Any] = []
    with reading(settings.db_path) as connection:
        for ulid in created:
            row = fetch_one(
                connection,
                "SELECT * FROM listen_assignments WHERE ulid=?",
                (ulid,),
            )
            if row is not None:
                rows.append(row)

    return JSONResponse(
        {
            "created": len(created),
            "skipped": len(skipped),
            "assignments": [_project(settings, row) for row in rows],
        },
        status_code=201,
    )


def _set_state(
    request: Request, ulid: str, state: str
) -> JSONResponse:
    session = _require_session(request)
    settings = _settings(request)
    now = utc_now()

    def update(connection: Any) -> str | None:
        row = fetch_one(
            connection,
            """
            SELECT id, assigned_to, state
            FROM listen_assignments WHERE ulid=?
            """,
            (ulid,),
        )
        if row is None or str(row["assigned_to"]) != session.username:
            # Someone else's homework is not yours to close, and saying so
            # would confirm it exists.
            return None
        current = str(row["state"])
        if current == state:
            return current
        # Done is reachable from either open state. Requiring heard first meant
        # the button did nothing on anything you had not played, which is most
        # of what is on a plate - you already know the track, or you listened on
        # your phone, or you are just clearing it. A control that silently
        # refuses is worse than one that is not there.
        if current not in set(OPEN_STATES):
            raise HTTPException(
                status_code=409, detail="that is already closed"
            )
        if state == "done":
            connection.execute(
                "UPDATE listen_assignments SET state='done', done_at=? WHERE id=?",
                (now, int(row["id"])),
            )
        else:
            connection.execute(
                "UPDATE listen_assignments SET state='dismissed' WHERE id=?",
                (int(row["id"]),),
            )
        return state

    current_state = mutate(settings.db_path, update)
    if current_state is None:
        raise HTTPException(status_code=404, detail="no such assignment")
    return JSONResponse({"ulid": ulid, "state": current_state})


@router.post("/api/assignments/{ulid}/done")
def mark_done(request: Request, ulid: str) -> JSONResponse:
    return _set_state(request, ulid, "done")


@router.post("/api/assignments/{ulid}/dismiss")
def dismiss(request: Request, ulid: str) -> JSONResponse:
    return _set_state(request, ulid, "dismissed")


def listened_enough(heard_s: float, duration_s: float | None) -> bool:
    """Has this person actually listened, or did the track just go past?"""
    if not math.isfinite(heard_s):
        return False
    if heard_s >= HEARD_SECONDS:
        return True
    if (
        duration_s is not None
        and math.isfinite(duration_s)
        and 0 < duration_s < SHORT_TRACK_SECONDS
    ):
        return heard_s >= duration_s * SHORT_TRACK_FRACTION
    return False


def advance_assignments_for_progress(
    settings: AppSettings, *, actor: str, bounce_ulid: str, heard_s: float
) -> None:
    """Move a pending assignment to heard once the listen is real.

    Never sets done: that stays a deliberate act by the person who was asked.
    """

    def advance(connection: Any) -> None:
        row = fetch_one(
            connection,
            """
            SELECT a.id,
                   (SELECT MAX(f.duration_s) FROM files AS f
                    JOIN bounces AS b ON b.id=f.bounce_id
                    WHERE b.public_id=a.bounce_ulid AND f.layer='curated'
                      AND f.missing_since IS NULL) AS duration_s
            FROM listen_assignments AS a
            WHERE a.assigned_to=? AND a.bounce_ulid=? AND a.state='pending'
            """,
            (actor, bounce_ulid),
        )
        if row is None:
            return
        duration = row["duration_s"]
        if not listened_enough(heard_s, float(duration) if duration else None):
            return
        connection.execute(
            "UPDATE listen_assignments SET state='heard', heard_at=? WHERE id=?",
            (utc_now(), int(row["id"])),
        )

    mutate(settings.db_path, advance)


def clear_for_removed_member(connection: Any, username: str) -> None:
    """Their plate goes away with them; what they sent stays as history."""
    connection.execute(
        """
        DELETE FROM listen_assignments
        WHERE assigned_to=? AND state IN ('pending','heard')
        """,
        (username,),
    )


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="expected a JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return payload
