"""HTMX fragment endpoints for owner tagging and panels."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from ..common.database import fetch_one, reading
from ..common.queries import (
    chip_vocabulary,
    filter_vocabulary,
    notes_for_track,
    song_detail,
    track_by_ulid,
)
from ..common.reactions import add_note, toggle
from ..common.tagging import song_tag_panel, toggle_song_tag
from ..common.templates import make_templates
from ..common.text import clean_text
from ..common.undo import push_undo, snapshot_tag_write
from .deps import context as _context, session_or_401, settings as get_settings
from .helpers import _detail_panel_values, _stem_panel_values, _tag_context


router = APIRouter()
templates = make_templates(Path(__file__).parent / "templates")

@router.get(
    "/songs/{song_ulid}/tag-panel", response_class=HTMLResponse
)
def tag_panel_fragment(request: Request, song_ulid: str) -> Response:
    session_or_401(request)
    panel = song_tag_panel(get_settings(request), song_ulid)
    if panel is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/tag_panel.html",
        context={"request": request, "panel": panel},
    )


@router.get(
    "/fragments/detail/{song_ulid}", response_class=HTMLResponse
)
def detail_panel_fragment(request: Request, song_ulid: str) -> Response:
    session_or_401(request)
    values = _detail_panel_values(get_settings(request), song_ulid)
    if values is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/detail_panel.html",
        context={"request": request, "song_ulid": song_ulid, **values},
    )


@router.get(
    "/songs/{song_ulid}/row-detail", response_class=HTMLResponse
)
def row_detail_fragment(request: Request, song_ulid: str) -> Response:
    session_or_401(request)
    result = song_detail(get_settings(request), song_ulid)
    if result is None:
        raise HTTPException(status_code=404)
    detail, versions = result
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/row_detail.html",
        context={
            "request": request,
            "song": detail,
            "versions": versions,
            "tag_panel": song_tag_panel(get_settings(request), song_ulid),
        },
    )


@router.post(
    "/songs/{song_ulid}/tags/toggle", response_class=HTMLResponse
)
async def toggle_song_tag_route(
    request: Request, song_ulid: str
) -> Response:
    session = session_or_401(request)
    form = await request.form()
    settings = get_settings(request)
    try:
        undo_kind, undo_payload = snapshot_tag_write(
            settings,
            song_ulid=song_ulid,
            dim=str(form.get("dim", "")),
            value=str(form.get("value", "")),
        )
        state = toggle_song_tag(
            settings,
            song_ulid=song_ulid,
            dim=str(form.get("dim", "")),
            value=str(form.get("value", "")),
            bounce_ulid=(
                str(form.get("bounce_ulid"))
                if form.get("bounce_ulid")
                else None
            ),
            actor=session.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dimension = clean_text(str(form.get("dim", "")), limit=20).casefold()
    tag_value = clean_text(str(form.get("value", "")), limit=40).casefold()
    undo_message = (
        f"removed {tag_value}"
        if state["action"] == "remove"
        else f"set {dimension} {tag_value}"
        if state["action"] == "set"
        else f"tagged {tag_value}"
    )
    undo_id = push_undo(
        settings,
        session_id=session.session_id,
        kind=undo_kind,
        label=undo_message,
        payload=undo_payload,
    )
    if str(form.get("render", "")) == "chip":
        track = track_by_ulid(
            get_settings(request),
            str(form.get("bounce_ulid", "")),
        )
        if track is None:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(
            request=request,
            name="owner/fragments/chip.html",
            context={
                "request": request,
                "track": track,
                "value": clean_text(
                    str(form.get("value", "")), limit=40
                ).casefold(),
                "pressed": bool(state["active"]),
                "undo_id": undo_id,
                "undo_message": undo_message,
            },
        )
    panel = song_tag_panel(get_settings(request), song_ulid)
    assert panel is not None
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/tag_panel.html",
        context={
            "request": request,
            "panel": panel,
            "undo_id": undo_id,
            "undo_message": undo_message,
        },
    )


@router.get(
    "/songs/{song_ulid}/stems",
    response_class=HTMLResponse,
)
def song_stems(request: Request, song_ulid: str) -> Response:
    session = session_or_401(request)
    result = song_detail(get_settings(request), song_ulid)
    if result is None:
        raise HTTPException(status_code=404)
    _detail, versions = result
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/stems.html",
        context=_context(
            request,
            session,
            song_ulid=song_ulid,
            **_stem_panel_values(get_settings(request), versions),
        ),
    )


@router.get("/fragments/tagbar/{bounce_ulid}", response_class=HTMLResponse)
def tagbar(request: Request, bounce_ulid: str) -> Response:
    session = session_or_401(request)
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/tagbar.html",
        context={
            "request": request,
            **_tag_context(
                get_settings(request), bounce_ulid, actor=session.username
            ),
        },
    )


@router.post(
    "/reactions/{bounce_ulid}/heart", response_class=HTMLResponse
)
async def owner_heart(request: Request, bounce_ulid: str) -> Response:
    session = session_or_401(request)
    settings = get_settings(request)
    track = track_by_ulid(settings, bounce_ulid)
    if track is None:
        raise HTTPException(status_code=404)
    with reading(settings.db_path) as connection:
        prior_active = fetch_one(
            connection,
            """
            SELECT 1 FROM reactions
            WHERE bounce_ulid=? AND actor=? AND kind='heart'
              AND deleted_at IS NULL LIMIT 1
            """,
            (bounce_ulid, session.username),
        ) is not None
    state = toggle(
        settings,
        bounce_ulid=bounce_ulid,
        actor=session.username,
        kind="heart",
    )
    undo_message = "hearted song" if state.active else "removed heart"
    undo_id = push_undo(
        settings,
        session_id=session.session_id,
        kind="heart",
        label=undo_message,
        payload={
            "bounce_ulid": bounce_ulid,
            "song_id": int(track["song_id"]),
            "song_ulid": str(track["song_ulid"]),
            "prior_active": prior_active,
        },
    )
    await request.app.state.events.publish()
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/heart.html",
        context={
            "request": request,
            "track": track,
            "pressed": state.active,
            "undo_id": undo_id,
            "undo_message": undo_message,
        },
    )


@router.post(
    "/reactions/{bounce_ulid}/note", response_class=HTMLResponse
)
async def owner_note(request: Request, bounce_ulid: str) -> Response:
    session = session_or_401(request)
    form = await request.form()
    try:
        add_note(
            get_settings(request),
            bounce_ulid=bounce_ulid,
            actor=session.username,
            note=str(form.get("note", "")),
            timecode_s=float(form.get("timecode_s", 0) or 0),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="note needs text"
        ) from exc
    await request.app.state.events.publish()
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/notes.html",
        context={
            "request": request,
            "bounce_ulid": bounce_ulid,
            "notes": notes_for_track(
                get_settings(request), bounce_ulid=bounce_ulid
            ),
        },
        status_code=201,
    )


@router.post(
    "/reactions/{bounce_ulid}/chip", response_class=HTMLResponse
)
async def owner_chip(request: Request, bounce_ulid: str) -> Response:
    session = session_or_401(request)
    form = await request.form()
    value = str(form.get("value", ""))
    settings = get_settings(request)
    track = track_by_ulid(settings, bounce_ulid)
    if track is None:
        raise HTTPException(status_code=404)
    try:
        undo_kind, undo_payload = snapshot_tag_write(
            settings,
            song_ulid=str(track["song_ulid"]),
            dim="vibe",
            value=value,
        )
        state = toggle_song_tag(
            settings,
            song_ulid=str(track["song_ulid"]),
            bounce_ulid=bounce_ulid,
            actor=session.username,
            dim="vibe",
            value=value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid chip") from exc
    clean_value = clean_text(value, limit=40).casefold()
    undo_message = (
        f"tagged {clean_value}"
        if state["active"]
        else f"removed {clean_value}"
    )
    undo_id = push_undo(
        settings,
        session_id=session.session_id,
        kind=undo_kind,
        label=undo_message,
        payload=undo_payload,
    )
    await request.app.state.events.publish()
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/chip.html",
        context={
            "request": request,
            "track": track,
            "value": clean_value,
            "pressed": bool(state["active"]),
            "undo_id": undo_id,
            "undo_message": undo_message,
        },
    )


@router.get("/more/{bounce_ulid}", response_class=HTMLResponse)
def more_sheet(request: Request, bounce_ulid: str) -> Response:
    session = session_or_401(request)
    settings = get_settings(request)
    context = _tag_context(settings, bounce_ulid, actor=session.username)
    context["chips"] = chip_vocabulary(settings)
    context["vocab"] = filter_vocabulary(settings)
    context["notes"] = notes_for_track(settings, bounce_ulid=bounce_ulid)
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/more.html",
        context={"request": request, **context},
    )

