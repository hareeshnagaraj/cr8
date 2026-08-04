"""Residual owner routes: media, downloads, triage, selection, activity, tags admin."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio.to_thread
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import (
    EventSourceResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.sse import ServerSentEvent

from ...db import utc_now
from ...stems import enqueue_stem_job, enqueue_stem_jobs_for_songs
from ..common.auth import UserSession
from ..common.collections import create_collection
from ..common.database import fetch_all, fetch_one, mutate, reading
from ..common.derived import copy_human_tags, fingerprint_neighbours
from ..common.downloads import (
    bounce_download_asset,
    file_download_response,
    selection_download_response,
    selection_zip,
    stem_download_asset,
)
from ..common.media import PreviewStyle, serve, serve_preview, serve_strip
from ..common.queries import activity_rows, song_detail
from ..common.reactions import soft_delete, set_progress, verdict
from ..common.tagging import rewrite_vocabulary, vocabulary_rows
from ..common.templates import make_templates
from ..common.text import clean_text
from ..common.undo import insert_undo, undo_last
from .deps import (
    admin_or_403,
    admin_or_redirect,
    context as _context,
    session_or_401,
    session_or_redirect,
    settings as get_settings,
)
from .helpers import _stem_panel_values, _today_triage_count, _triage_tracks, _write_result
from .routes_assignments import advance_assignments_for_progress


router = APIRouter()
templates = make_templates(Path(__file__).parent / "templates")

@router.get("/healthz")
def health(request: Request) -> JSONResponse:
    with reading(get_settings(request).db_path) as connection:
        fetch_one(connection, "SELECT 1")
    return JSONResponse({"status": "ok", "app": "owner"})


@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request) -> Response:
    session = admin_or_redirect(request)
    if isinstance(session, RedirectResponse):
        return session
    return templates.TemplateResponse(
        request=request,
        name="owner/tags.html",
        context=_context(
            request,
            session,
            tags=vocabulary_rows(get_settings(request)),
        ),
    )


@router.post("/tags/rewrite")
async def rewrite_tag_route(request: Request) -> RedirectResponse:
    admin_or_403(request)
    form = await request.form()
    action = str(form.get("action", "rename"))
    replacement = (
        None if action == "delete" else str(form.get("replacement", ""))
    )
    try:
        rewrite_vocabulary(
            get_settings(request),
            dim=str(form.get("dim", "")),
            value=str(form.get("value", "")),
            replacement=replacement,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/tags", status_code=303)


@router.post("/songs/{song_ulid}/apply-neighbours")
async def apply_neighbour_tags(
    request: Request, song_ulid: str
) -> RedirectResponse:
    session_or_401(request)
    form = await request.form()
    requested = {
        str(value) for value in form.getlist("neighbour_ulid") if value
    }
    if not requested:
        raise HTTPException(status_code=400, detail="select a neighbour")

    def apply(connection: Any) -> None:
        source = fetch_one(
            connection,
            "SELECT id FROM songs WHERE public_id=?",
            (song_ulid,),
        )
        if source is None:
            raise ValueError("song unavailable")
        source_id = int(source["id"])
        available = {
            str(item["song_ulid"]): int(item["song_id"])
            for item in fingerprint_neighbours(connection, source_id, limit=20)
        }
        if not requested <= available.keys():
            raise ValueError("neighbour unavailable")
        copy_human_tags(
            connection,
            source_song_id=source_id,
            target_song_ids=[available[ulid] for ulid in sorted(requested)],
        )

    try:
        mutate(get_settings(request).db_path, apply)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(f"/songs/{song_ulid}", status_code=303)


@router.post("/stems/{bounce_ulid}", response_class=HTMLResponse)
def queue_stems(
    request: Request,
    bounce_ulid: str,
    recipe: Annotated[str, Form()] = "default-v1",
) -> Response:
    session = session_or_401(request)
    settings = get_settings(request)
    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection,
            """
            SELECT s.public_id AS song_ulid
            FROM bounces AS b JOIN songs AS s ON s.id=b.song_id
            WHERE b.public_id=?
            """,
            (bounce_ulid,),
        )
    if row is None:
        raise HTTPException(status_code=404)
    enqueue_stem_job(
        settings.db_path,
        bounce_ulid,
        recipe=recipe,
        requested_by=session.username,
    )
    if request.headers.get("HX-Request") != "true":
        return RedirectResponse(f"/songs/{row['song_ulid']}", status_code=303)
    result = song_detail(settings, str(row["song_ulid"]))
    assert result is not None
    _detail, versions = result
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/stems.html",
        context=_context(
            request,
            session,
            song_ulid=str(row["song_ulid"]),
            **_stem_panel_values(settings, versions),
        ),
        status_code=202,
    )


@router.get("/download/selection")
def download_selection(
    request: Request,
    ulids: str = "",
) -> Response:
    session_or_401(request)
    requested = [
        value.strip()
        for value in ulids.split(",")
        if value.strip()
    ]
    if not requested:
        raise HTTPException(status_code=400, detail="select at least one song")
    if len(requested) > 200 or any(len(value) > 64 for value in requested):
        raise HTTPException(status_code=400, detail="selection is too large")
    result = selection_zip(get_settings(request), requested)
    return selection_download_response(result)


@router.get("/download/stem/{stem_ulid}")
def download_stem(request: Request, stem_ulid: str) -> Response:
    session_or_401(request)
    return file_download_response(
        stem_download_asset(get_settings(request), stem_ulid)
    )


@router.get("/download/{bounce_ulid}")
def download_bounce(
    request: Request,
    bounce_ulid: str,
    format: Literal["original", "mp3"] = "original",
) -> Response:
    session_or_401(request)
    return file_download_response(
        bounce_download_asset(
            get_settings(request), bounce_ulid, format=format
        )
    )


@router.get("/m/{bounce_ulid}")
def owner_audio(request: Request, bounce_ulid: str) -> Response:
    session_or_401(request)
    return serve(
        request, get_settings(request), bounce_ulid=bounce_ulid, artifact="audio"
    )


@router.get("/peaks/{bounce_ulid}")
def owner_peaks(request: Request, bounce_ulid: str) -> Response:
    session_or_401(request)
    return serve(
        request, get_settings(request), bounce_ulid=bounce_ulid, artifact="peaks"
    )


@router.get("/art/{bounce_ulid}")
def owner_art(request: Request, bounce_ulid: str) -> Response:
    session_or_401(request)
    return serve(
        request, get_settings(request), bounce_ulid=bounce_ulid, artifact="art"
    )


@router.get("/art-preview/{style}/{bounce_ulid}")
def owner_art_preview(
    request: Request, style: PreviewStyle, bounce_ulid: str
) -> Response:
    session_or_401(request)
    return serve_preview(
        request,
        get_settings(request),
        bounce_ulid=bounce_ulid,
        style=style,
    )


@router.get("/art-strip/{bounce_ulid}")
def owner_art_strip(request: Request, bounce_ulid: str) -> Response:
    session_or_401(request)
    return serve_strip(
        request,
        get_settings(request),
        bounce_ulid=bounce_ulid,
    )


@router.post("/progress/{bounce_ulid}", status_code=204)
async def owner_progress(request: Request, bounce_ulid: str) -> Response:
    session = session_or_401(request)
    form = await request.form()

    def write_progress() -> None:
        # Two mutate() transactions with time.sleep busy-retries. This handler
        # is async for the form parse, and running these inline stalled the
        # whole event loop - every progress post could freeze every request
        # in the process. The threadpool is where blocking writes belong.
        heard_s = float(form.get("heard_s", 0) or 0)
        set_progress(
            get_settings(request),
            share_id=0,
            bounce_ulid=bounce_ulid,
            actor=session.username,
            state=str(form.get("state", "unheard")),
            heard_s=heard_s,
            started=str(form.get("started", "")).casefold() == "true",
        )
        advance_assignments_for_progress(
            get_settings(request),
            actor=session.username,
            bounce_ulid=bounce_ulid,
            heard_s=heard_s,
        )

    try:
        await anyio.to_thread.run_sync(write_progress)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid progress") from exc
    return Response(status_code=204)


@router.post("/songs/{song_ulid}/edit", response_class=HTMLResponse)
async def edit_song(request: Request, song_ulid: str) -> Response:
    session = session_or_401(request)
    form = await request.form()
    status = clean_text(str(form.get("status", "")), limit=20).casefold()
    collab = clean_text(str(form.get("collab", "")), limit=40).casefold()
    if status not in {
        "idea", "jam", "demo", "mixed", "finished", "released"
    }:
        raise HTTPException(status_code=400, detail="invalid status")

    def update(connection: Any) -> None:
        song_row = fetch_one(
            connection, "SELECT id FROM songs WHERE public_id=?", (song_ulid,)
        )
        if song_row is None:
            raise ValueError("song unavailable")
        song_id = int(song_row["id"])
        connection.execute(
            "UPDATE songs SET status=?, human_touched=1 WHERE id=?",
            (status, song_id),
        )
        if collab:
            connection.execute(
                """
                INSERT INTO song_tags(
                  song_id, dim, value, source, author, created_at
                ) VALUES(?, 'collab', ?, 'human', ?, ?)
                ON CONFLICT(song_id, dim, value) DO UPDATE SET
                  source='human', author=excluded.author,
                  created_at=excluded.created_at
                WHERE song_tags.source!='human'
                """,
                (song_id, collab, session.username, utc_now()),
            )

    mutate(get_settings(request).db_path, update)
    return _write_result(
        request,
        song_ulids=[song_ulid],
        message="Metadata committed.",
    )


@router.get("/triage", response_class=HTMLResponse)
def triage(request: Request) -> Response:
    session = session_or_redirect(request)
    if isinstance(session, RedirectResponse):
        return session
    settings = get_settings(request)
    queue = _triage_tracks(settings, actor=session.username)
    return templates.TemplateResponse(
        request=request,
        name="owner/triage.html",
        context=_context(
            request,
            session,
            track=queue[0] if queue else None,
            today_count=_today_triage_count(
                settings, actor=session.username
            ),
            undo=None,
        ),
    )


@router.post("/triage/{bounce_ulid}", response_class=HTMLResponse)
async def triage_verdict(request: Request, bounce_ulid: str) -> Response:
    session = session_or_401(request)
    form = await request.form()
    settings = get_settings(request)
    try:
        reaction_id = verdict(
            settings,
            bounce_ulid=bounce_ulid,
            actor=session.username,
            value=str(form.get("value", "")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid verdict") from exc
    await request.app.state.events.publish()
    queue = _triage_tracks(settings, actor=session.username)
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/triage_card.html",
        context={
            "request": request,
            "track": queue[0] if queue else None,
            "today_count": _today_triage_count(
                settings, actor=session.username
            ),
            "undo": reaction_id,
        },
    )


@router.post("/reactions/{reaction_id}/undo", response_class=HTMLResponse)
async def undo_reaction(request: Request, reaction_id: int) -> Response:
    session = session_or_401(request)
    removed = soft_delete(
        get_settings(request),
        reaction_id=reaction_id,
        actor=session.username,
    )
    await request.app.state.events.publish()
    return HTMLResponse(
        '<span class="toast">Undone.</span>'
        if removed
        else '<span class="toast">Already undone.</span>'
    )


@router.post("/undo", response_class=HTMLResponse)
async def undo_last_write(request: Request) -> Response:
    session = session_or_401(request)
    result = undo_last(
        get_settings(request),
        session_id=session.session_id,
        actor=session.username,
    )
    if result is None:
        return _write_result(request, message="Nothing to undo.")
    await request.app.state.events.publish()
    return _write_result(
        request,
        song_ulids=result["song_ulids"],
        message=f"Undid {result['label']}.",
    )


@router.post("/selection", response_class=HTMLResponse)
async def apply_selection(request: Request) -> Response:
    session = session_or_401(request)
    form = await request.form()
    song_ulids = [str(value) for value in form.getlist("song_ulid")]
    status = clean_text(str(form.get("status", "")), limit=20).casefold()
    action = clean_text(str(form.get("action", "")), limit=20).casefold()
    released_url = clean_text(
        str(form.get("released_url", "")), limit=500
    )
    instr = clean_text(str(form.get("instr", "")), limit=40).casefold()
    collab = clean_text(str(form.get("collab", "")), limit=40).casefold()
    tag_dim = clean_text(str(form.get("tag_dim", "")), limit=20).casefold()
    tag_value = clean_text(
        str(form.get("tag_value", "")), limit=40
    ).casefold()
    tag_action = clean_text(
        str(form.get("tag_action", "add")), limit=10
    ).casefold()
    if not song_ulids:
        raise HTTPException(status_code=400, detail="select at least one song")
    if action == "stems":
        enqueue_stem_jobs_for_songs(
            get_settings(request).db_path,
            song_ulids,
            requested_by=f"{session.username}-selection",
        )
        return _write_result(
            request,
            song_ulids=song_ulids,
            message=f"Queued stems for {len(song_ulids)} songs.",
        )
    if action == "collection":
        try:
            collection_ulid = create_collection(
                get_settings(request),
                name=str(form.get("collection_name", "")),
                song_ulids=song_ulids,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _write_result(
            request,
            message=(
                f"Collection created: /collections/{collection_ulid}"
            ),
        )
    if action == "released":
        status = "released"
    if status and status not in {
        "idea", "jam", "demo", "mixed", "finished", "released"
    }:
        raise HTTPException(status_code=400, detail="invalid status")
    if tag_dim and (
        tag_dim not in {"vibe", "instr", "collab", "use"}
        or not tag_value
        or tag_action not in {"add", "remove"}
    ):
        raise HTTPException(status_code=400, detail="invalid multi-song tag")

    def update(connection: Any) -> int | None:
        placeholders = ",".join("?" for _ in song_ulids)
        rows = list(
            connection.execute(
                f"""
                SELECT id, public_id, status, released_url, human_touched
                FROM songs WHERE public_id IN ({placeholders})
                """,
                song_ulids,
            )
        )
        ids = [int(row["id"]) for row in rows]
        if len(ids) != len(set(song_ulids)):
            raise ValueError("unknown song")
        tag_changes: list[dict[str, Any]] = []
        tag_requests = [
            (dim, value, "add")
            for dim, value in (("instr", instr), ("collab", collab))
            if value
        ]
        if tag_dim and tag_value:
            tag_requests.append((tag_dim, tag_value, tag_action))
        for row in rows:
            for change_dim, change_value, _change_action in dict.fromkeys(
                tag_requests
            ):
                prior = fetch_one(
                    connection,
                    """
                    SELECT source, author, created_at FROM song_tags
                    WHERE song_id=? AND dim=? AND value=?
                    """,
                    (int(row["id"]), change_dim, change_value),
                )
                tag_changes.append(
                    {
                        "song_id": int(row["id"]),
                        "song_ulid": str(row["public_id"]),
                        "dim": change_dim,
                        "value": change_value,
                        "prior": dict(prior) if prior is not None else None,
                    }
                )
        field_changes = (
            [
                {
                    "song_id": int(row["id"]),
                    "song_ulid": str(row["public_id"]),
                    "values": {
                        "status": row["status"],
                        "released_url": row["released_url"],
                        "human_touched": row["human_touched"],
                    },
                }
                for row in rows
            ]
            if status
            else []
        )
        undo_id = None
        if tag_changes or field_changes:
            if tag_dim and tag_value:
                label = (
                    f"removed {tag_value} from {len(ids)} songs"
                    if tag_action == "remove"
                    else f"tagged {len(ids)} songs {tag_value}"
                )
            elif status:
                label = f"set {len(ids)} songs {status}"
            else:
                label = f"tagged {len(ids)} songs"
            undo_id = insert_undo(
                connection,
                session_id=session.session_id,
                kind="bulk",
                label=label,
                payload={"tags": tag_changes, "fields": field_changes},
            )
        if status == "released":
            connection.executemany(
                """
                UPDATE songs
                SET status='released', released_url=?, human_touched=1
                WHERE id=?
                """,
                [(released_url or None, song_id) for song_id in ids],
            )
        elif status:
            connection.executemany(
                "UPDATE songs SET status=?, human_touched=1 WHERE id=?",
                [(status, song_id) for song_id in ids],
            )
        for dim, value in (("instr", instr), ("collab", collab)):
            if value:
                connection.executemany(
                    """
                    INSERT INTO song_tags(
                      song_id, dim, value, source, author, created_at
                    ) VALUES(?, ?, ?, 'human', ?, ?)
                    ON CONFLICT(song_id, dim, value) DO UPDATE SET
                      source='human', author=excluded.author,
                      created_at=excluded.created_at
                    WHERE song_tags.source!='human'
                    """,
                    [
                        (
                            song_id,
                            dim,
                            value,
                            session.username,
                            utc_now(),
                        )
                        for song_id in ids
                    ],
                )
        if tag_dim and tag_value:
            if tag_action == "remove":
                connection.executemany(
                    """
                    DELETE FROM song_tags
                    WHERE song_id=? AND dim=? AND value=?
                      AND source='human'
                    """,
                    [(song_id, tag_dim, tag_value) for song_id in ids],
                )
            else:
                connection.executemany(
                    """
                    INSERT INTO song_tags(
                      song_id, dim, value, source, author, created_at
                    ) VALUES(?, ?, ?, 'human', ?, ?)
                    ON CONFLICT(song_id, dim, value) DO UPDATE SET
                      source='human', author=excluded.author,
                      created_at=excluded.created_at
                    """,
                    [
                        (
                            song_id,
                            tag_dim,
                            tag_value,
                            session.username,
                            utc_now(),
                        )
                        for song_id in ids
                    ],
                )
        return undo_id

    undo_id = mutate(get_settings(request).db_path, update)
    if tag_dim and tag_value:
        message = (
            f"removed {tag_value} from {len(song_ulids)} songs"
            if tag_action == "remove"
            else f"tagged {len(song_ulids)} songs {tag_value}"
        )
    else:
        message = f"Updated {len(song_ulids)} songs."
    return _write_result(
        request,
        song_ulids=song_ulids,
        message=message,
        undo_id=undo_id,
    )


@router.get("/activity", response_class=HTMLResponse)
def activity(request: Request) -> Response:
    session = session_or_redirect(request)
    if isinstance(session, RedirectResponse):
        return session
    settings = get_settings(request)
    with reading(settings.db_path) as connection:
        alerts = fetch_all(
            connection,
            """
            SELECT a.*, s.ulid AS share_ulid, s.label AS share_label
            FROM app_alerts AS a
            LEFT JOIN shares AS s ON s.id=a.share_id
            WHERE a.acknowledged_at IS NULL
            ORDER BY a.id DESC
            """,
        )
    return templates.TemplateResponse(
        request=request,
        name="owner/activity.html",
        context=_context(
            request,
            session,
            activity=activity_rows(settings),
            alerts=alerts,
        ),
    )


@router.post("/alerts/{alert_id}/ack")
def acknowledge_alert(request: Request, alert_id: int) -> RedirectResponse:
    session_or_401(request)
    mutate(
        get_settings(request).db_path,
        lambda connection: connection.execute(
            """
            UPDATE app_alerts SET acknowledged_at=?
            WHERE id=? AND acknowledged_at IS NULL
            """,
            (utc_now(), alert_id),
        ),
    )
    return RedirectResponse("/activity", status_code=303)


@router.get("/activity/feed", response_class=HTMLResponse)
def activity_feed(request: Request) -> Response:
    session_or_401(request)
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/activity_feed.html",
        context={"request": request, "activity": activity_rows(get_settings(request))},
    )


@router.get("/activity/events", response_class=EventSourceResponse)
async def activity_events(
    request: Request,
    _session: Annotated[UserSession, Depends(session_or_401)],
):
    settings = get_settings(request)
    with reading(settings.db_path) as connection:
        row = fetch_one(connection, "SELECT COALESCE(MAX(id), 0) AS id FROM reactions")
        last_id = int(row["id"] if row else 0)
    async with request.app.state.events.subscribe() as queue:
        while True:
            try:
                await asyncio.wait_for(queue.get(), timeout=2.0)
                changed = True
            except TimeoutError:
                with reading(settings.db_path) as connection:
                    row = fetch_one(
                        connection, "SELECT COALESCE(MAX(id), 0) AS id FROM reactions"
                    )
                    current_id = int(row["id"] if row else 0)
                changed = current_id != last_id
                last_id = current_id
            if changed:
                yield ServerSentEvent(data={"kind": "poke"}, event="poke")

