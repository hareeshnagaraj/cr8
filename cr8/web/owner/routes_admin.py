"""Admin surface: who else gets in, and how they stop getting in.

Kept out of routes.py because that file is already 2,800 lines and this is the
one area where a mistake hands someone else an account. Everything here is
admin-only except the join endpoints, which are how a person without an account
gets one — those are guarded by the invite token instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ...db import utc_now
from ...public_ids import new_ulid
from ..common import tokens
from ..common.auth import (
    AuthError,
    create_member,
    create_user_session,
)
from ..common.database import fetch_all, fetch_one, mutate, reading
from ..common.queries import track_by_ulid
from ..common.text import clean_text
from .deps import admin_or_403, settings as _settings


router = APIRouter()

MAX_LABEL = 60
DEFAULT_EXPIRY_DAYS = 14
INVITE_SELECT = """
SELECT i.*, s.title AS song_title
FROM invites AS i
LEFT JOIN bounces AS b ON b.public_id=i.bounce_ulid
LEFT JOIN songs AS s ON s.id=b.song_id
"""


def _require_admin(request: Request) -> Any:
    # Preserve this module's historical 401 detail ("login required").
    return admin_or_403(request, detail_401="login required")


def _invite_row(row: Any) -> dict[str, Any]:
    state = tokens.check_state(row)
    return {
        "ulid": str(row["ulid"]),
        "label": str(row["label"] or ""),
        "role": str(row["role"]),
        "created_by": str(row["created_by"]),
        "created_at": str(row["created_at"]),
        "expires_at": row["expires_at"],
        "max_uses": row["max_uses"],
        "use_count": int(row["use_count"] or 0),
        "revoked_at": row["revoked_at"],
        "claimed_by": row["claimed_by"],
        "bounce_ulid": row["bounce_ulid"],
        "song_title": row.get("song_title"),
        "state": state.reason,
        "usable": state.usable,
    }


def _join_base_url(request: Request) -> str:
    """Where an invite link should point.

    During the Cloudflare Access soak the domain refuses anyone who is not
    already allowed, which is exactly wrong for an invite, so the base URL is
    configurable and defaults to whatever host the admin is using right now.
    """
    settings = _settings(request)
    configured = getattr(settings, "public_base_url", "") or ""
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/api/admin/members")
def admin_members(request: Request) -> JSONResponse:
    """The roster with roles, for the admin page. The plain /api/members every
    member can read deliberately leaves roles out."""
    session = _require_admin(request)
    with reading(_settings(request).db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT id, username, display, role, created_at
            FROM users ORDER BY created_at, id
            """,
            (),
        )
    return JSONResponse(
        {
            "members": [
                {
                    "id": int(row["id"]),
                    "username": str(row["username"]),
                    "display": str(row["display"] or row["username"]),
                    "role": str(row["role"]),
                    "created_at": str(row["created_at"] or ""),
                    "is_you": str(row["username"]) == session.username,
                }
                for row in rows
            ]
        }
    )


@router.post("/api/admin/members/{username}/remove")
def admin_remove_member(request: Request, username: str) -> JSONResponse:
    session = _require_admin(request)
    settings = _settings(request)
    if username == session.username:
        raise HTTPException(status_code=400, detail="you cannot remove yourself")

    def remove(connection: Any) -> bool:
        row = fetch_one(
            connection, "SELECT id FROM users WHERE username=?", (username,)
        )
        if row is None:
            return False
        user_id = int(row["id"])
        # Their plate goes with them; what they sent stays as history.
        connection.execute(
            """
            DELETE FROM listen_assignments
            WHERE assigned_to=? AND state IN ('pending','heard')
            """,
            (username,),
        )
        connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        connection.execute("DELETE FROM users WHERE id=?", (user_id,))
        return True

    if not mutate(settings.db_path, remove):
        raise HTTPException(status_code=404, detail="no such member")
    return JSONResponse({"removed": username})


@router.get("/api/admin/invites")
def list_invites(request: Request) -> JSONResponse:
    _require_admin(request)
    with reading(_settings(request).db_path) as connection:
        rows = fetch_all(
            connection,
            INVITE_SELECT + " ORDER BY i.created_at DESC LIMIT 200",
            (),
        )
    return JSONResponse({"invites": [_invite_row(row) for row in rows]})


@router.post("/api/admin/invites")
async def create_invite(request: Request) -> JSONResponse:
    session = _require_admin(request)
    settings = _settings(request)
    payload = await _json_body(request)

    label = clean_text(str(payload.get("label", "")), limit=MAX_LABEL)
    role = str(payload.get("role", "band"))
    if role not in {"owner", "band"}:
        raise HTTPException(status_code=400, detail="unknown role")

    raw_bounce_ulid = payload.get("bounce_ulid")
    if raw_bounce_ulid is not None and not isinstance(raw_bounce_ulid, str):
        raise HTTPException(status_code=400, detail="bounce_ulid must be text")
    bounce_ulid = str(raw_bounce_ulid or "").strip() or None
    if bounce_ulid and track_by_ulid(settings, bounce_ulid) is None:
        raise HTTPException(status_code=400, detail="unknown song")

    max_uses = payload.get("max_uses", 1)
    if max_uses is not None:
        try:
            max_uses = int(max_uses)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="max_uses must be a number")
        if max_uses < 1:
            raise HTTPException(status_code=400, detail="max_uses must be at least 1")

    days = payload.get("expires_days", DEFAULT_EXPIRY_DAYS)
    expires_at: str | None = None
    if days is not None:
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="expires_days must be a number")
        if days > 0:
            expires_at = (datetime.now(UTC) + timedelta(days=days)).isoformat()

    raw_token = tokens.mint()
    ulid = new_ulid()
    digest = tokens.digest(raw_token, settings.session_secret)
    now = utc_now()

    def insert(connection: Any) -> None:
        connection.execute(
            """
            INSERT INTO invites(
              ulid, label, role, token_sha256, created_by, created_at,
              expires_at, max_uses, use_count, bounce_ulid
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                ulid,
                label,
                role,
                digest,
                session.username,
                now,
                expires_at,
                max_uses,
                bounce_ulid,
            ),
        )

    mutate(settings.db_path, insert)

    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection, INVITE_SELECT + " WHERE i.ulid=?", (ulid,)
        )

    # The only time the raw token exists outside the browser that receives it.
    return JSONResponse(
        {
            "invite": _invite_row(row),
            "join_url": f"{_join_base_url(request)}/join/{raw_token}",
        },
        status_code=201,
    )


@router.post("/api/admin/invites/{ulid}/revoke")
def revoke_invite(request: Request, ulid: str) -> JSONResponse:
    _require_admin(request)
    settings = _settings(request)
    now = utc_now()

    def revoke(connection: Any) -> int:
        row = fetch_one(
            connection, "SELECT id, revoked_at FROM invites WHERE ulid=?", (ulid,)
        )
        if row is None:
            return 404
        if row["revoked_at"]:
            return 200
        connection.execute(
            "UPDATE invites SET revoked_at=? WHERE id=?", (now, int(row["id"]))
        )
        return 200

    status = mutate(settings.db_path, revoke)
    if status == 404:
        raise HTTPException(status_code=404, detail="no such invite")

    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection, INVITE_SELECT + " WHERE i.ulid=?", (ulid,)
        )
    return JSONResponse({"invite": _invite_row(row)})


@router.get("/api/admin/tokens")
def list_tokens(request: Request) -> JSONResponse:
    _require_admin(request)
    with reading(_settings(request).db_path) as connection:
        rows = fetch_all(
            connection,
            "SELECT * FROM api_tokens ORDER BY created_at DESC LIMIT 100",
            (),
        )
    return JSONResponse(
        {
            "tokens": [
                {
                    "ulid": str(row["ulid"]),
                    "label": str(row["label"] or ""),
                    "username": str(row["username"]),
                    "created_at": str(row["created_at"]),
                    "last_used_at": row["last_used_at"],
                    "use_count": int(row["use_count"] or 0),
                    "revoked_at": row["revoked_at"],
                    "state": tokens.check_state(row).reason,
                }
                for row in rows
            ]
        }
    )


@router.post("/api/admin/tokens")
async def create_token(request: Request) -> JSONResponse:
    """An upload token for a machine, so a watcher can post without a browser."""
    session = _require_admin(request)
    settings = _settings(request)
    payload = await _json_body(request)

    username = clean_text(str(payload.get("username", "")), limit=40).casefold()
    if not username:
        username = session.username
    with reading(settings.db_path) as connection:
        known = fetch_one(
            connection, "SELECT id FROM users WHERE username=?", (username,)
        )
    if known is None:
        raise HTTPException(status_code=400, detail="no such person")

    label = clean_text(str(payload.get("label", "")), limit=MAX_LABEL)
    raw_token = tokens.mint()
    ulid = new_ulid()

    def insert(connection: Any) -> None:
        connection.execute(
            """
            INSERT INTO api_tokens(
              ulid, label, kind, token_sha256, username, created_at, use_count
            ) VALUES(?, ?, 'upload', ?, ?, ?, 0)
            """,
            (
                ulid,
                label,
                tokens.digest(raw_token, settings.session_secret),
                username,
                utc_now(),
            ),
        )

    mutate(settings.db_path, insert)
    return JSONResponse(
        {
            "ulid": ulid,
            "username": username,
            "label": label,
            # Shown once, exactly like an invite link.
            "token": raw_token,
            "base_url": _join_base_url(request),
        },
        status_code=201,
    )


@router.post("/api/admin/tokens/{ulid}/revoke")
def revoke_token(request: Request, ulid: str) -> JSONResponse:
    _require_admin(request)
    settings = _settings(request)
    now = utc_now()

    def revoke(connection: Any) -> bool:
        row = fetch_one(
            connection, "SELECT id FROM api_tokens WHERE ulid=?", (ulid,)
        )
        if row is None:
            return False
        connection.execute(
            "UPDATE api_tokens SET revoked_at=? WHERE id=?", (now, int(row["id"]))
        )
        return True

    if not mutate(settings.db_path, revoke):
        raise HTTPException(status_code=404, detail="no such token")
    return JSONResponse({"revoked": ulid})


@router.get("/api/join/{raw_token}")
def inspect_invite(request: Request, raw_token: str) -> JSONResponse:
    """What the claim page shows before anyone types anything.

    Deliberately says nothing about who made the invite or what else exists;
    only whether this link still works.
    """
    settings = _settings(request)
    row = _invite_for_token(settings, raw_token)
    state = tokens.check_state(row)
    if not state.usable:
        return JSONResponse(
            {"usable": False, "reason": state.reason}, status_code=404
        )
    return JSONResponse(
        {"usable": True, "reason": "active", "role": str(row["role"]),
         "label": str(row["label"] or "")}
    )


@router.post("/api/join")
async def claim_invite(request: Request) -> JSONResponse:
    """Redeem an invite: create the account and sign the person straight in."""
    settings = _settings(request)
    payload = await _json_body(request)
    raw_token = str(payload.get("token", ""))
    username = str(payload.get("username", ""))
    display = str(payload.get("display", "")) or username
    password = str(payload.get("password", ""))

    if len(password) < 12:
        raise HTTPException(
            status_code=400, detail="password must be at least 12 characters"
        )

    row = _invite_for_token(settings, raw_token)
    state = tokens.check_state(row)
    if not state.usable:
        raise HTTPException(status_code=404, detail=f"invite {state.reason}")

    role = str(row["role"])
    invite_id = int(row["id"])
    bounce_ulid = str(row["bounce_ulid"] or "")

    # Claim the use first, in one immediate transaction, so two people racing
    # the last use of an invite cannot both win. If account creation then fails
    # (name taken, weak password) the claim is released.
    def claim(connection: Any) -> bool:
        current = fetch_one(
            connection,
            "SELECT use_count, max_uses, revoked_at FROM invites WHERE id=?",
            (invite_id,),
        )
        if current is None or current["revoked_at"]:
            return False
        limit = current["max_uses"]
        used = int(current["use_count"] or 0)
        if limit is not None and used >= int(limit):
            return False
        connection.execute(
            "UPDATE invites SET use_count=use_count+1 WHERE id=?", (invite_id,)
        )
        return True

    if not mutate(settings.db_path, claim):
        raise HTTPException(status_code=409, detail="invite exhausted")

    try:
        credentials = create_member(
            settings,
            username=username,
            display=display,
            password=password,
            role=role,
        )
    except AuthError as exc:
        mutate(
            settings.db_path,
            lambda connection: connection.execute(
                "UPDATE invites SET use_count=MAX(use_count-1, 0) WHERE id=?",
                (invite_id,),
            ),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    mutate(
        settings.db_path,
        lambda connection: connection.execute(
            "UPDATE invites SET claimed_by=? WHERE id=?",
            (credentials.username, invite_id),
        ),
    )

    raw_sid, session = create_user_session(settings, credentials.user_id)
    payload = {"username": session.username, "role": session.role}
    if bounce_ulid:
        track = track_by_ulid(settings, bounce_ulid)
        if track is not None:
            payload["redirect"] = f"/songs/{track['song_ulid']}?welcome=1"
    response = JSONResponse(payload, status_code=201)
    _set_cookie(response, settings, raw_sid)
    return response


def _invite_for_token(settings: AppSettings, raw_token: str) -> Any:
    if not raw_token:
        return None
    digest = tokens.digest(raw_token, settings.session_secret)
    with reading(settings.db_path) as connection:
        return fetch_one(
            connection, "SELECT * FROM invites WHERE token_sha256=?", (digest,)
        )


def _set_cookie(response: Any, settings: AppSettings, raw_sid: str) -> None:
    response.set_cookie(
        settings.cookie_name,
        raw_sid,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # malformed or empty body
        raise HTTPException(status_code=400, detail="expected a JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return payload
