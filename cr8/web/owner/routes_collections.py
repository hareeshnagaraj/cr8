"""Collection HTML pages and dual HTML/API mutation handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..common.collections import (
    collection_summaries,
    collection_summary,
    collection_tracks,
    create_collection,
    delete_collection,
    remove_collection_track,
    reorder_collection,
)
from ..common.queries import LibraryFilter, library_songs
from ..common.templates import make_templates
from .deps import context as _context, session_or_401, session_or_redirect, settings as get_settings


router = APIRouter()
templates = make_templates(Path(__file__).parent / "templates")

@router.get("/collections", response_class=HTMLResponse)
def collections_page(request: Request) -> Response:
    session = session_or_redirect(request)
    if isinstance(session, RedirectResponse):
        return session
    return templates.TemplateResponse(
        request=request,
        name="owner/collections.html",
        context=_context(
            request,
            session,
            collections=collection_summaries(get_settings(request)),
            library_count=len(
                library_songs(
                    get_settings(request),
                    LibraryFilter(),
                    actor=session.username,
                    limit=10_000,
                )
            ),
        ),
    )


@router.post("/api/collections")
@router.post("/collections")
async def create_collection_route(request: Request) -> RedirectResponse:
    session = session_or_401(request)
    form = await request.form()
    settings = get_settings(request)
    bounce_ulids = [
        str(value) for value in form.getlist("bounce_ulid") if value
    ]
    song_ulids = [
        str(value) for value in form.getlist("song_ulid") if value
    ]
    source = str(form.get("source", ""))
    if source not in {"filter", "queue", "selection"}:
        raise HTTPException(
            status_code=400,
            detail="collection source must be filter, queue, or selection",
        )
    if source == "filter":
        if bounce_ulids or song_ulids:
            raise HTTPException(
                status_code=400,
                detail="filter collections cannot include explicit songs",
            )
        tracks = library_songs(
            settings,
            LibraryFilter(
                query=str(form.get("q", "")),
                status=str(form.get("status", "")) or None,
                era=str(form.get("era", "")) or None,
                key_value=str(form.get("key", "")) or None,
                dim=str(form.get("dim", "")) or None,
                value=str(form.get("value", "")) or None,
                tag_values={
                    dimension: [
                        str(item)
                        for item in form.getlist(dimension)
                        if item
                    ]
                    for dimension in ("vibe", "instr", "collab", "use")
                },
                untagged_dims=[
                    str(item)
                    for item in form.getlist("untagged_dim")
                    if item
                ],
                unheard=str(form.get("unheard", "")).casefold() == "true",
                hearted=str(form.get("hearted", "")).casefold() == "true",
                untagged_vibe=(
                    str(form.get("untagged", "")).casefold() == "true"
                ),
                random_seed=str(form.get("seed", "")),
                skip_short_sketches=(
                    str(form.get("skip_sketches", "")).casefold() == "true"
                ),
            ),
            actor=session.username,
            sort=str(form.get("sort", "newest")),
        )
        bounce_ulids = [str(track["bounce_ulid"]) for track in tracks]
        song_ulids = []
    elif source == "queue":
        if song_ulids:
            raise HTTPException(
                status_code=400,
                detail="queue collections require queued bounces",
            )
        whole_library = {
            str(track["bounce_ulid"])
            for track in library_songs(
                settings,
                LibraryFilter(),
                actor=session.username,
                limit=10_000,
            )
        }
        if (
            whole_library
            and set(bounce_ulids) == whole_library
            and str(form.get("confirm_all", "")).casefold() != "true"
        ):
            raise HTTPException(
                status_code=400,
                detail="confirm before saving the whole library",
            )
    elif not bounce_ulids and not song_ulids:
        raise HTTPException(
            status_code=400,
            detail="selection collections require selected songs",
        )
    try:
        collection_ulid = create_collection(
            settings,
            name=str(form.get("name", "")),
            notes=str(form.get("notes", "")),
            bounce_ulids=bounce_ulids,
            song_ulids=song_ulids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    prefix = (
        "/api/collections"
        if request.url.path.startswith("/api/")
        else "/collections"
    )
    return RedirectResponse(f"{prefix}/{collection_ulid}", status_code=303)


@router.get("/collections/{collection_ulid}", response_class=HTMLResponse)
def collection_detail_page(
    request: Request, collection_ulid: str
) -> Response:
    session = session_or_redirect(request)
    if isinstance(session, RedirectResponse):
        return session
    settings = get_settings(request)
    collection = collection_summary(settings, collection_ulid)
    if collection is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="owner/collection.html",
        context=_context(
            request,
            session,
            collection=collection,
            tracks=collection_tracks(settings, collection_ulid),
        ),
    )


@router.post("/api/collections/{collection_ulid}/order", status_code=204)
@router.post("/collections/{collection_ulid}/order", status_code=204)
async def collection_order(
    request: Request, collection_ulid: str
) -> Response:
    session_or_401(request)
    form = await request.form()
    try:
        reorder_collection(
            get_settings(request),
            collection_ulid=collection_ulid,
            bounce_ulids=[
                str(value)
                for value in form.getlist("bounce_ulid")
                if value
            ],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post(
    "/api/collections/{collection_ulid}/remove/{bounce_ulid}",
    status_code=204,
)
@router.post(
    "/collections/{collection_ulid}/remove/{bounce_ulid}",
    status_code=204,
)
def collection_remove_track(
    request: Request,
    collection_ulid: str,
    bounce_ulid: str,
) -> Response:
    session_or_401(request)
    try:
        remove_collection_track(
            get_settings(request),
            collection_ulid=collection_ulid,
            bounce_ulid=bounce_ulid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=200)
    prefix = (
        "/api/collections"
        if request.url.path.startswith("/api/")
        else "/collections"
    )
    return RedirectResponse(f"{prefix}/{collection_ulid}", status_code=303)


@router.post("/api/collections/{collection_ulid}/delete", status_code=204)
def collection_delete(request: Request, collection_ulid: str) -> Response:
    session_or_401(request)
    try:
        delete_collection(
            get_settings(request),
            collection_ulid=collection_ulid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)

