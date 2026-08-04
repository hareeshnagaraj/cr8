"""Authenticated share management and the bare public listening surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ...db import utc_now
from ...public_ids import new_ulid
from ..common import tokens
from ..common.auth import user_session
from ..common.collections import collection_share_snapshot
from ..common.database import fetch_all, fetch_one, mutate, reading
from ..common.media import serve
from ..common.queries import track_by_ulid
from ..common.templates import make_templates
from ..common.text import clean_text
from .deps import settings as _settings


router = APIRouter()
templates = make_templates(Path(__file__).parent / "templates")

DEFAULT_HOURS = 4.0
COLLECTION_DEFAULT_HOURS = 168
COLLECTION_TTL_HOURS = frozenset({24, 168})
PUBLIC_GONE = "This link was turned off."
PUBLIC_EXPIRED = "This link has expired."


def _require_member(request: Request) -> Any:
    session = user_session(request, _settings(request))
    if session is None:
        raise HTTPException(status_code=401, detail="login required")
    return session


def _public_base_url(request: Request) -> str:
    configured = _settings(request).public_base_url
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="expected a JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return payload


def _frozen_scope(row: Any) -> list[str] | None:
    if row is None:
        return None
    try:
        scope = json.loads(str(row["scope_json"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        row["scope_mode"] != "frozen"
        or not isinstance(scope, list)
        or not scope
        or any(not isinstance(item, str) or not item for item in scope)
    ):
        return None
    return scope


def _single_track_scope(row: Any) -> str | None:
    if row is not None and row.get("landing_collection_id") is not None:
        return None
    scope = _frozen_scope(row)
    if scope is None or len(scope) != 1:
        return None
    return scope[0]


def _scope_item(row: Any, index: int) -> str | None:
    scope = _frozen_scope(row)
    if scope is None or index < 0 or index >= len(scope):
        return None
    return scope[index]


def _share_for_token(settings: AppSettings, raw_token: str) -> Any:
    if not raw_token:
        return None
    token_digest = tokens.digest(raw_token, settings.session_secret)
    with reading(settings.db_path) as connection:
        return fetch_one(
            connection,
            "SELECT * FROM shares WHERE token_sha256=?",
            (token_digest,),
        )


def _public_message(reason: str) -> str:
    if reason in {"expired", "exhausted"}:
        return PUBLIC_EXPIRED
    # Unknown and revoked links deliberately share one public response. Knowing
    # whether a secret ever existed is not useful to a listener.
    return PUBLIC_GONE


def _denied_landing(request: Request, reason: str) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="owner/share.html",
        context={"request": request, "track": None, "message": _public_message(reason)},
        status_code=410,
        headers={"X-Robots-Tag": "noindex, nofollow, noarchive"},
    )


def _owner_display(settings: AppSettings) -> str:
    with reading(settings.db_path) as connection:
        owner = fetch_one(
            connection,
            "SELECT display, username FROM users "
            "WHERE role='owner' ORDER BY id LIMIT 1",
        )
    if owner is None:
        return "Someone"
    return str(owner["display"] or owner["username"])


def _listed_share(
    row: Any, *, live_scope: list[str] | None = None
) -> dict[str, Any]:
    frozen_scope = _frozen_scope(row)
    is_collection = row.get("landing_collection_id") is not None
    return {
        "share_ulid": str(row["ulid"]),
        "created_at": str(row["created_at"]),
        "expires_at": str(row["expires_at"]),
        "use_count": int(row["use_count"] or 0),
        "diverged": bool(
            is_collection
            and frozen_scope is not None
            and live_scope is not None
            and frozen_scope != live_scope
        ),
    }


@router.post("/api/shares")
async def create_share(request: Request) -> JSONResponse:
    _require_member(request)
    settings = _settings(request)
    payload = await _json_body(request)
    bounce_ulid = str(payload.get("bounce_ulid", "")).strip()
    collection_ulid = str(payload.get("collection_ulid", "")).strip()
    if bool(bounce_ulid) == bool(collection_ulid):
        raise HTTPException(
            status_code=400,
            detail="provide exactly one of bounce_ulid or collection_ulid",
        )

    landing_collection_id: int | None = None
    note: str | None = None
    if collection_ulid:
        collection, scope, unavailable = collection_share_snapshot(
            settings, collection_ulid
        )
        if collection is None:
            raise HTTPException(status_code=404, detail="collection unavailable")
        if not scope:
            raise HTTPException(status_code=400, detail="collection is empty")
        if unavailable:
            raise HTTPException(
                status_code=409,
                detail="Unavailable tracks: " + ", ".join(unavailable),
            )
        ttl_hours = payload.get("ttl_hours", COLLECTION_DEFAULT_HOURS)
        if (
            isinstance(ttl_hours, bool)
            or not isinstance(ttl_hours, (int, float))
            or ttl_hours not in COLLECTION_TTL_HOURS
        ):
            raise HTTPException(
                status_code=400,
                detail="ttl_hours must be 24 or 168",
            )
        note_value = payload.get("note", "")
        if not isinstance(note_value, str):
            raise HTTPException(status_code=400, detail="note must be text")
        note = clean_text(note_value, limit=280) or None
        hours = float(ttl_hours)
        label = str(collection["name"])
        landing_collection_id = int(collection["id"])
    else:
        track = track_by_ulid(settings, bounce_ulid)
        if track is None:
            raise HTTPException(status_code=404, detail="track unavailable")
        scope = [bounce_ulid]
        value = payload.get("hours", DEFAULT_HOURS)
        if isinstance(value, bool):
            raise HTTPException(status_code=400, detail="hours must be positive")
        try:
            hours = float(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="hours must be a number")
        if not math.isfinite(hours) or hours <= 0:
            raise HTTPException(status_code=400, detail="hours must be positive")
        label = str(track["title"])

    raw_token = tokens.mint()
    share_ulid = new_ulid()
    expires_at = (
        datetime.now(UTC) + timedelta(hours=hours)
    ).replace(microsecond=0).isoformat()
    created_at = utc_now()

    def insert(connection: Any) -> None:
        connection.execute(
            """
            INSERT INTO shares(
              ulid, label, token_sha256, scope_mode, scope_json, expires_at,
              max_uses, use_count, revoked_at, created_at, include_stems,
              allow_downloads, landing_collection_id, note
            ) VALUES(?, ?, ?, 'frozen', ?, ?, NULL, 0, NULL, ?, 0, 0, ?, ?)
            """,
            (
                share_ulid,
                label,
                tokens.digest(raw_token, settings.session_secret),
                json.dumps(scope, separators=(",", ":")),
                expires_at,
                created_at,
                landing_collection_id,
                note,
            ),
        )

    mutate(settings.db_path, insert)
    return JSONResponse(
        {
            "url": f"{_public_base_url(request)}/s/{raw_token}",
            "expires_at": expires_at,
            "share_ulid": share_ulid,
        },
        status_code=201,
    )


@router.get("/api/shares")
def list_shares(
    request: Request,
    bounce_ulid: str | None = Query(default=None, min_length=1, max_length=64),
    collection_ulid: str | None = Query(
        default=None, min_length=1, max_length=64
    ),
) -> JSONResponse:
    _require_member(request)
    if bool(bounce_ulid) == bool(collection_ulid):
        raise HTTPException(
            status_code=400,
            detail="provide exactly one of bounce_ulid or collection_ulid",
        )
    with reading(_settings(request).db_path) as connection:
        live_scope: list[str] | None = None
        if collection_ulid:
            collection = fetch_one(
                connection,
                "SELECT id FROM collections WHERE ulid=?",
                (collection_ulid,),
            )
            if collection is None:
                raise HTTPException(status_code=404, detail="collection unavailable")
            collection_id = int(collection["id"])
            live_scope = [
                str(row["bounce_ulid"])
                for row in fetch_all(
                    connection,
                    """
                    SELECT bounce_ulid FROM collection_items
                    WHERE collection_id=? ORDER BY position
                    """,
                    (collection_id,),
                )
            ]
            rows = fetch_all(
                connection,
                """
                SELECT * FROM shares
                WHERE landing_collection_id=?
                ORDER BY created_at DESC, id DESC LIMIT 500
                """,
                (collection_id,),
            )
        else:
            rows = fetch_all(
                connection,
                "SELECT * FROM shares ORDER BY created_at DESC, id DESC LIMIT 500",
            )
    active = []
    for row in rows:
        if not tokens.check_state(row).usable:
            continue
        if bounce_ulid and _single_track_scope(row) != bounce_ulid:
            continue
        active.append(_listed_share(row, live_scope=live_scope))
    return JSONResponse({"shares": active})


@router.post("/api/shares/{share_ulid}/revoke")
def revoke_share(request: Request, share_ulid: str) -> JSONResponse:
    _require_member(request)
    settings = _settings(request)
    now = utc_now()

    def revoke(connection: Any) -> str | None:
        row = fetch_one(
            connection,
            "SELECT id, revoked_at FROM shares WHERE ulid=?",
            (share_ulid,),
        )
        if row is None:
            return None
        revoked_at = str(row["revoked_at"] or now)
        if not row["revoked_at"]:
            connection.execute(
                "UPDATE shares SET revoked_at=? WHERE id=?",
                (revoked_at, int(row["id"])),
            )
        return revoked_at

    revoked_at = mutate(settings.db_path, revoke)
    if revoked_at is None:
        raise HTTPException(status_code=404, detail="no such share")
    return JSONResponse({"share_ulid": share_ulid, "revoked_at": revoked_at})


@router.get("/s/{raw_token}", response_class=HTMLResponse)
def public_share(request: Request, raw_token: str) -> Response:
    settings = _settings(request)
    token_digest = tokens.digest(raw_token, settings.session_secret)

    def open_share(
        connection: Any,
    ) -> tuple[Any, tokens.TokenState, list[str] | None]:
        row = fetch_one(
            connection,
            """
            SELECT s.*, c.ulid AS landing_collection_ulid
            FROM shares AS s
            LEFT JOIN collections AS c ON c.id=s.landing_collection_id
            WHERE s.token_sha256=?
            """,
            (token_digest,),
        )
        state = tokens.check_state(row)
        scope = _frozen_scope(row) if state.usable else None
        if state.usable and scope is not None:
            connection.execute(
                "UPDATE shares SET use_count=COALESCE(use_count, 0) + 1 WHERE id=?",
                (int(row["id"]),),
            )
        return row, state, scope

    row, state, scope = mutate(settings.db_path, open_share)
    if not state.usable:
        return _denied_landing(request, state.reason)
    if row is None or scope is None:
        return _denied_landing(request, "revoked")

    if row.get("landing_collection_id") is not None:
        collection_ulid = row.get("landing_collection_ulid")
        if collection_ulid and user_session(request, settings) is not None:
            return RedirectResponse(
                f"/collections/{collection_ulid}", status_code=302
            )
        tracks = [track_by_ulid(settings, bounce_ulid) for bounce_ulid in scope]
        if any(track is None for track in tracks):
            return _denied_landing(request, "revoked")
        album_title = str(row["label"] or "Shared collection")
        note = str(row.get("note") or "")
        base = _public_base_url(request).rstrip("/")
        first = tracks[0] if tracks else None
        n = len(tracks)
        og_description = note or (
            f"{n} track{'s' if n != 1 else ''} shared on cr8"
            + (f" · starts with {first['title']}" if first else "")
        )
        return templates.TemplateResponse(
            request=request,
            name="owner/share_album.html",
            context={
                "request": request,
                "album_title": album_title,
                "note": note,
                "tracks": tracks,
                "raw_token": raw_token,
                "og_url": f"{base}/s/{raw_token}",
                "og_image": (
                    f"{base}/s/{raw_token}/art?i=0" if first else None
                ),
                "og_description": og_description,
            },
            headers={"X-Robots-Tag": "noindex, nofollow, noarchive"},
        )

    bounce_ulid = _single_track_scope(row)
    track = track_by_ulid(settings, bounce_ulid) if bounce_ulid else None
    if bounce_ulid is None or track is None:
        return _denied_landing(request, "revoked")
    base = _public_base_url(request).rstrip("/")
    # Keep catalog facts (key, bpm, tags, era) out of the public landing —
    # title + duration + who sent it is enough for a link preview.
    owner = _owner_display(settings)
    duration = str(track.get("duration_label") or "").strip()
    if owner and duration:
        og_description = f"From {owner} · {duration}"
    elif owner:
        og_description = f"From {owner} · shared on cr8"
    elif duration:
        og_description = f"{duration} · shared on cr8"
    else:
        og_description = "Shared on cr8"
    return templates.TemplateResponse(
        request=request,
        name="owner/share.html",
        context={
            "request": request,
            "track": track,
            "raw_token": raw_token,
            "owner_display": owner,
            "message": None,
            "og_url": f"{base}/s/{raw_token}",
            "og_image": f"{base}/s/{raw_token}/art",
            "og_description": og_description,
        },
        headers={"X-Robots-Tag": "noindex, nofollow, noarchive"},
    )


@router.get("/s/{raw_token}/audio")
def public_share_audio(
    request: Request,
    raw_token: str,
    i: int = Query(default=0, ge=0),
) -> Response:
    settings = _settings(request)
    row = _share_for_token(settings, raw_token)
    state = tokens.check_state(row)
    bounce_ulid = _scope_item(row, i) if state.usable else None
    if not state.usable or bounce_ulid is None:
        status = 410 if not state.usable else 404
        detail = _public_message(state.reason) if not state.usable else "track unavailable"
        raise HTTPException(status_code=status, detail=detail)
    return serve(
        request,
        settings,
        bounce_ulid=bounce_ulid,
        artifact="audio",
        download=False,
    )


@router.get("/s/{raw_token}/art")
def public_share_art(
    request: Request,
    raw_token: str,
    i: int = Query(default=0, ge=0),
) -> Response:
    settings = _settings(request)
    row = _share_for_token(settings, raw_token)
    state = tokens.check_state(row)
    bounce_ulid = _scope_item(row, i) if state.usable else None
    if not state.usable or bounce_ulid is None:
        status = 410 if not state.usable else 404
        detail = _public_message(state.reason) if not state.usable else "track unavailable"
        raise HTTPException(status_code=status, detail=detail)
    return serve(
        request,
        settings,
        bounce_ulid=bounce_ulid,
        artifact="art",
        download=False,
    )
