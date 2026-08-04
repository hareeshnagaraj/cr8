"""Pure /api/* JSON surface for the owner app."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import secrets
from typing import Annotated, Any

from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..common.collections import (
    collection_summaries,
    collection_summary,
    collection_tracks,
)
from ..common.database import fetch_all, fetch_one, reading
from ..common.presence import listeners as presence_listeners
from ..common.queries import (
    FACET_STATUS_VALUES,
    LibraryFilter,
    SearchError,
    activity_rows,
    dig_summary,
    filter_vocabulary_counts,
    library_facet_counts,
    library_songs,
    notes_for_track,
    prioritized_dig,
    replaygain,
    stems_for_bounce,
    track_by_ulid,
)
from ..common.reactions import add_note, toggle
from ..common.tagging import song_tag_panel, toggle_song_tag
from ..common.text import clean_text, era_css
from ..common.undo import snapshot_tag_write
from ..common.auth import user_exists
from .deps import session_or_401, settings as get_settings
from .helpers import _queue_items, _today_triage_count, _triage_tracks


router = APIRouter()

@router.get("/api/tracks/{bounce_ulid}")
def track_json(request: Request, bounce_ulid: str) -> JSONResponse:
    session = session_or_401(request)
    settings = get_settings(request)
    track = track_by_ulid(settings, bounce_ulid)
    if track is None:
        raise HTTPException(status_code=404)
    payload = {
        **track,
        "audio_url": f"/m/{bounce_ulid}",
        "peaks_url": f"/peaks/{bounce_ulid}",
        "artwork_url": f"/art/{bounce_ulid}",
        "replaygain": replaygain(settings, track),
    }
    return JSONResponse(payload)


@router.get("/api/dig")
def owner_dig(
    request: Request,
    q: str = "",
    status: str | None = None,
    era: str | None = None,
    key: str | None = None,
    dim: str | None = None,
    value: str | None = None,
    vibe: Annotated[list[str] | None, Query()] = None,
    instr: Annotated[list[str] | None, Query()] = None,
    collab: Annotated[list[str] | None, Query()] = None,
    use: Annotated[list[str] | None, Query()] = None,
    untagged_dim: Annotated[list[str] | None, Query()] = None,
    unheard: bool = False,
    hearted: bool = False,
    skip_sketches: bool = False,
    untagged: bool = False,
) -> JSONResponse:
    session = session_or_401(request)
    settings = get_settings(request)
    tracks = prioritized_dig(
        settings,
        library_songs(
            settings,
            LibraryFilter(
                query=q,
                status=status,
                era=era,
                key_value=key,
                dim=dim,
                value=value,
                tag_values={
                    "vibe": vibe or [],
                    "instr": instr or [],
                    "collab": collab or [],
                    "use": use or [],
                },
                untagged_dims=untagged_dim or [],
                unheard=unheard,
                hearted=hearted,
                skip_short_sketches=skip_sketches,
                untagged_vibe=untagged,
            ),
            actor=session.username,
        ),
        share_id=0,
        actor=session.username,
    )
    # The full decorated tracks ride along: without them the dig page fetched
    # /api/tracks once per item — ~650 requests that burned the rate budget
    # and 429'd the whole session.
    return JSONResponse(
        {
            "tracks": _queue_items(tracks),
            "details": tracks,
            "dig_summary": dig_summary(settings, showing=len(tracks)),
            "mode": "dig",
        }
    )


@router.get("/api/library-queue")
def owner_library_queue(
    request: Request,
    q: str = "",
    status: str | None = None,
    era: str | None = None,
    key: str | None = None,
    dim: str | None = None,
    value: str | None = None,
    vibe: Annotated[list[str] | None, Query()] = None,
    instr: Annotated[list[str] | None, Query()] = None,
    collab: Annotated[list[str] | None, Query()] = None,
    use: Annotated[list[str] | None, Query()] = None,
    untagged_dim: Annotated[list[str] | None, Query()] = None,
    unheard: bool = False,
    hearted: bool = False,
    sort: str = "newest",
    seed: str = "",
    skip_sketches: bool = False,
    untagged: bool = False,
) -> JSONResponse:
    session = session_or_401(request)
    if sort not in {
        "newest",
        "oldest",
        "longest",
        "shortest",
        "random",
        "title",
        "title-desc",
        "era",
        "era-desc",
        "key",
        "key-desc",
        "bpm",
        "bpm-desc",
        "versions",
        "versions-desc",
    }:
        sort = "newest"
    if sort == "random" and not seed:
        seed = secrets.token_hex(8)
    try:
        tracks = library_songs(
            get_settings(request),
            LibraryFilter(
                query=q,
                status=status,
                era=era,
                key_value=key,
                dim=dim,
                value=value,
                tag_values={
                    "vibe": vibe or [],
                    "instr": instr or [],
                    "collab": collab or [],
                    "use": use or [],
                },
                untagged_dims=untagged_dim or [],
                unheard=unheard,
                hearted=hearted,
                random_seed=seed,
                skip_short_sketches=skip_sketches,
                untagged_vibe=untagged,
            ),
            actor=session.username,
            sort=sort,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail="invalid search") from exc
    return JSONResponse({"tracks": _queue_items(tracks), "mode": "queue"})


@router.get("/api/library")
def owner_library_json(
    request: Request,
    q: str = "",
    status: str | None = None,
    era: str | None = None,
    key: str | None = None,
    vibe: Annotated[list[str] | None, Query()] = None,
    instr: Annotated[list[str] | None, Query()] = None,
    collab: Annotated[list[str] | None, Query()] = None,
    use: Annotated[list[str] | None, Query()] = None,
    unheard: bool = False,
    hearted: bool = False,
    keeper_min: int | None = None,
    sort: str = "newest",
    random_seed: str = "",
    bpm_min: float | None = None,
    bpm_max: float | None = None,
    limit: int = 500,
    offset: int = 0,
) -> JSONResponse:
    """The library as JSON, with the columns a table needs.

    /api/library-queue deliberately projects down to what the player consumes.
    A row grid needs key, bpm, duration, era and version count as well, so this
    returns the query rows whole rather than making the client fetch each song.
    """
    session = session_or_401(request)
    try:
        rows = library_songs(
            get_settings(request),
            LibraryFilter(
                query=q,
                status=status,
                era=era,
                key_value=key,
                tag_values={
                    "vibe": vibe or [],
                    "instr": instr or [],
                    "collab": collab or [],
                    "use": use or [],
                },
                unheard=unheard,
                hearted=hearted,
                keeper_min=keeper_min,
                random_seed=random_seed,
                bpm_min=bpm_min,
                bpm_max=bpm_max,
            ),
            actor=session.username,
            sort=sort,
            include_vibe_tags=True,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail="invalid search") from exc
    window = rows[offset : offset + max(1, min(limit, 1000))]
    return JSONResponse(
        {
            "total": len(rows),
            "offset": offset,
            "tracks": [
                {key: value for key, value in dict(row).items()}
                for row in window
            ],
        }
    )


@router.get("/api/cover-previews")
def owner_cover_previews_json(request: Request) -> JSONResponse:
    session_or_401(request)
    root = get_settings(request).mirror_root.resolve()
    availability: dict[str, list[str]] = {}
    for style in ("spectral", "envelope"):
        directory = root / "art-preview" / style
        availability[style] = sorted(
            path.stem for path in directory.glob("*.jpg") if path.is_file()
        )
    return JSONResponse(availability)


@router.get("/api/session-check")
def owner_session_check(request: Request) -> JSONResponse:
    session = session_or_401(request)
    return JSONResponse({"username": session.username})


@router.get("/api/needs-setup")
def owner_needs_setup(request: Request) -> JSONResponse:
    settings = get_settings(request)
    return JSONResponse({"needs_setup": not user_exists(settings)})


@router.get("/api/since-you-were-here")
def owner_since_you_were_here(request: Request) -> JSONResponse:
    session = session_or_401(request)
    settings = get_settings(request)
    with reading(settings.db_path) as connection:
        previous_session = fetch_one(
            connection,
            """
            SELECT last_seen FROM sessions
            WHERE user_id=? AND id!=?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (session.user_id, session.session_id),
        )
    if previous_session is None or previous_session["last_seen"] is None:
        return JSONResponse({"quiet": True})

    try:
        previous_last_seen = datetime.fromisoformat(
            str(previous_session["last_seen"])
        )
    except ValueError:
        return JSONResponse({"quiet": True})
    if previous_last_seen.tzinfo is None:
        previous_last_seen = previous_last_seen.replace(tzinfo=UTC)
    else:
        previous_last_seen = previous_last_seen.astimezone(UTC)
    if datetime.now(UTC) - previous_last_seen < timedelta(hours=6):
        return JSONResponse({"quiet": True})

    since = previous_last_seen.isoformat()
    with reading(settings.db_path) as connection:
        new_songs = fetch_one(
            connection,
            """
            SELECT COUNT(*) AS count FROM (
              SELECT b.song_id
              FROM bounces AS b
              JOIN files AS f ON f.bounce_id=b.id
              WHERE f.first_seen IS NOT NULL
              GROUP BY b.song_id
              HAVING MIN(f.first_seen)>?
            )
            """,
            (since,),
        )
        people = fetch_one(
            connection,
            """
            SELECT COUNT(DISTINCT actor) AS count
            FROM playback_events
            WHERE share_id=0 AND started_at>? AND actor!=?
            """,
            (since, session.username),
        )
    assert new_songs is not None and people is not None
    return JSONResponse(
        {
            "new_songs": int(new_songs["count"]),
            "people": int(people["count"]),
        }
    )


@router.get("/api/me")
def owner_me(request: Request) -> JSONResponse:
    """Who am I and what may I do. The UI hides admin surfaces with this; the
    server still enforces every one of them independently."""
    session = session_or_401(request)
    return JSONResponse(
        {
            "username": session.username,
            "display": session.display,
            "role": session.role,
            "is_admin": session.is_admin,
        }
    )


@router.get("/api/eras")
def owner_eras_json(request: Request) -> JSONResponse:
    """Eras table as the colour authority: name, css token, and colour."""
    session_or_401(request)
    with reading(get_settings(request).db_path) as connection:
        rows = fetch_all(
            connection, "SELECT name, color FROM eras ORDER BY name", ()
        )
    return JSONResponse(
        [
            {
                "name": str(row["name"]),
                "css": era_css(str(row["name"])),
                "color": str(row["color"]),
            }
            for row in rows
        ]
    )


@router.get("/api/facets")
def owner_facets(request: Request) -> JSONResponse:
    """Every filterable dimension with its counts, in one call.

    The filter rail otherwise needs a request per dimension, and the counts are
    what make a facet worth clicking.
    """
    session = session_or_401(request)
    settings = get_settings(request)
    # Status carries counts like every other facet. Without them a chip cannot
    # tell you that 470 of 472 songs already share its value, so clicking it
    # looks like it did nothing.
    basic_facet_counts = library_facet_counts(
        settings, actor=session.username
    )
    counts = [
        {
            "value": value,
            "count": int(basic_facet_counts.statuses.get(value, 0)),
        }
        for value in FACET_STATUS_VALUES
    ]
    return JSONResponse(
        {
            "status": counts,
            "tags": filter_vocabulary_counts(settings),
            "keys": [
                {"value": value, "count": count}
                for value, count in sorted(
                    basic_facet_counts.canonical_keys.items(),
                    key=lambda item: item[0].casefold(),
                )
            ],
            "unheard_count": basic_facet_counts.unheard,
        }
    )


@router.get("/api/songs/{song_ulid}")
def owner_song_json(request: Request, song_ulid: str) -> JSONResponse:
    session_or_401(request)
    panel = song_tag_panel(get_settings(request), song_ulid)
    if panel is None:
        raise HTTPException(status_code=404, detail="song not found")
    return JSONResponse(jsonable_encoder(panel))


@router.post("/api/songs/{song_ulid}/tags/toggle")
async def owner_toggle_tag_json(
    request: Request, song_ulid: str
) -> JSONResponse:
    """The same write the Jinja panel performs, answering with JSON.

    Undo history is snapshotted exactly as the HTML path does, so a tag written
    from either surface can be undone from either surface.
    """
    session = session_or_401(request)
    body = await request.json()
    settings = get_settings(request)
    dim = str(body.get("dim", ""))
    value = str(body.get("value", ""))
    try:
        snapshot_tag_write(
            settings, song_ulid=song_ulid, dim=dim, value=value
        )
        result = toggle_song_tag(
            settings,
            song_ulid=song_ulid,
            dim=dim,
            value=value,
            actor=session.username,
            bounce_ulid=str(body.get("bounce_ulid") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(jsonable_encoder(result))


@router.get("/api/collections")
def owner_collections_json(request: Request) -> JSONResponse:
    session_or_401(request)
    return JSONResponse(
        jsonable_encoder(
            [dict(row) for row in collection_summaries(get_settings(request))]
        )
    )


@router.get("/api/collections/{collection_ulid}")
def owner_collection_json(request: Request, collection_ulid: str) -> JSONResponse:
    session_or_401(request)
    settings = get_settings(request)
    summary = collection_summary(settings, collection_ulid)
    if summary is None:
        raise HTTPException(status_code=404, detail="collection not found")
    return JSONResponse(
        jsonable_encoder(
            {
                "collection": dict(summary),
                "tracks": collection_tracks(settings, collection_ulid),
            }
        )
    )


@router.get("/api/stems/{bounce_ulid}")
def owner_stems_json(request: Request, bounce_ulid: str) -> JSONResponse:
    session_or_401(request)
    return JSONResponse(
        jsonable_encoder(stems_for_bounce(get_settings(request), bounce_ulid))
    )


@router.get("/api/triage")
def owner_triage_json(request: Request) -> JSONResponse:
    session = session_or_401(request)
    settings = get_settings(request)
    return JSONResponse(
        jsonable_encoder(
            {
                "queue": _triage_tracks(settings, actor=session.username),
                "today": _today_triage_count(settings, actor=session.username),
            }
        )
    )


@router.get("/api/activity")
def owner_activity_json(request: Request, limit: int = 60) -> JSONResponse:
    session_or_401(request)
    rows = activity_rows(get_settings(request), limit=max(1, min(limit, 200)))
    return JSONResponse(jsonable_encoder([dict(row) for row in rows]))


@router.get("/api/presence")
def owner_presence_json(request: Request) -> JSONResponse:
    session_or_401(request)
    return JSONResponse({"listeners": presence_listeners(get_settings(request))})


@router.post("/api/reactions/{bounce_ulid}/heart")
def owner_heart_json(request: Request, bounce_ulid: str) -> JSONResponse:
    """Heart toggle answering with state instead of a fragment."""
    session = session_or_401(request)
    settings = get_settings(request)
    track = track_by_ulid(settings, bounce_ulid)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    state = toggle(
        settings,
        bounce_ulid=bounce_ulid,
        actor=session.username,
        kind="heart",
    )
    return JSONResponse({"hearted": bool(state.active)})


@router.get("/api/notes/{bounce_ulid}")
def owner_notes_json(request: Request, bounce_ulid: str) -> JSONResponse:
    """Notes on a track, in time order rather than newest-first.

    The catalogue has stored a timecode against every note since the schema was
    written, and nothing has ever shown it. Read back ascending, because these
    are read against a waveform - a list that runs backwards through the track
    is a list you have to reorder in your head.
    """
    session_or_401(request)
    rows = notes_for_track(get_settings(request), bounce_ulid=bounce_ulid)
    return JSONResponse(
        sorted(
            (
                {
                    "id": int(row["id"]),
                    "actor": str(row["actor"]),
                    "note": str(row["value"] or ""),
                    "timecode_s": float(row["timecode_s"] or 0),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
            ),
            key=lambda note: (note["timecode_s"], note["id"]),
        )
    )


@router.post("/api/notes/{bounce_ulid}")
async def owner_add_note_json(request: Request, bounce_ulid: str) -> JSONResponse:
    session = session_or_401(request)
    settings = get_settings(request)
    if track_by_ulid(settings, bounce_ulid) is None:
        raise HTTPException(status_code=404, detail="track not found")
    body = await request.json()
    try:
        add_note(
            settings,
            bounce_ulid=bounce_ulid,
            actor=session.username,
            note=str(body.get("note", "")),
            timecode_s=float(body.get("timecode_s", 0) or 0),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="note needs text") from exc
    await request.app.state.events.publish()
    return JSONResponse({"ok": True}, status_code=201)


@router.get("/api/hearts")
def owner_hearts_json(request: Request) -> JSONResponse:
    session = session_or_401(request)
    rows = library_songs(
        get_settings(request),
        LibraryFilter(hearted=True),
        actor=session.username,
        limit=10_000,
    )
    return JSONResponse([str(row["bounce_ulid"]) for row in rows])


@router.get("/api/tag-queue")
def owner_tag_queue(
    request: Request,
    dim: str = "vibe",
    value: str = "",
) -> JSONResponse:
    session = session_or_401(request)
    dim = clean_text(dim, limit=20).casefold()
    value = clean_text(value, limit=40).casefold()
    if dim not in {"vibe", "instr", "collab", "use"} or not value:
        raise HTTPException(status_code=400, detail="invalid tag")
    tracks = library_songs(
        get_settings(request),
        LibraryFilter(dim=dim, value=value),
        actor=session.username,
    )
    return JSONResponse({"tracks": _queue_items(tracks), "mode": "queue"})

