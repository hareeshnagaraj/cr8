"""Owner library and song detail HTML."""

from __future__ import annotations

from pathlib import Path
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..common.collections import collection_summaries
from ..common.downloads import (
    SELECTION_MAX_BYTES,
    SELECTION_MAX_COUNT,
    bounce_download_options,
)
from ..common.queries import (
    LibraryFacetCounts,
    LibraryFilter,
    SearchError,
    active_reactions,
    chip_vocabulary,
    filter_vocabulary,
    filter_vocabulary_counts,
    library_facet_counts,
    library_songs,
    notes_for_track,
    prioritized_dig,
    song_detail,
    song_neighbours,
    track_is_unheard,
    untagged_dimension_counts,
    untagged_vibe_count,
)
from ..common.tagging import song_tag_panel
from ..common.templates import make_templates
from .deps import context as _context, session_or_redirect, settings as get_settings
from .helpers import _detail_panel_values, _owner_library_url, _stem_panel_values


router = APIRouter()
templates = make_templates(Path(__file__).parent / "templates")

@router.get("/", response_class=HTMLResponse)
def library(
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
    untagged: bool = False,
    sort: str = "newest",
    seed: str = "",
    skip_sketches: bool = False,
    offset: int = 0,
) -> Response:
    session = session_or_redirect(request)
    if isinstance(session, RedirectResponse):
        return session
    allowed_sorts = {
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
    }
    if sort not in allowed_sorts:
        sort = "newest"
    offset = min(max(offset, 0), 10_000)
    if sort == "random" and not seed:
        seed = secrets.token_hex(8)
    settings = get_settings(request)
    tag_values = {
        "vibe": list(dict.fromkeys(vibe or [])),
        "instr": list(dict.fromkeys(instr or [])),
        "collab": list(dict.fromkeys(collab or [])),
        "use": list(dict.fromkeys(use or [])),
    }
    if dim in tag_values and value and value not in tag_values[dim]:
        tag_values[dim].append(value)
    selected_untagged = list(
        dict.fromkeys(
            item
            for item in (untagged_dim or [])
            if item in {"vibe", "instr", "collab", "use"}
        )
    )
    if untagged and "vibe" not in selected_untagged:
        selected_untagged.append("vibe")
    current = {
        "q": q,
        "status": status,
        "era": era,
        "key": key,
        "dim": None,
        "value": None,
        "vibe": tag_values["vibe"],
        "instr": tag_values["instr"],
        "collab": tag_values["collab"],
        "use": tag_values["use"],
        "untagged_dim": selected_untagged,
        "unheard": unheard,
        "hearted": hearted,
        "untagged": False,
        "sort": sort,
        "seed": seed,
        "skip_sketches": skip_sketches,
    }
    try:
        basic_facet_counts = library_facet_counts(
            settings, actor=session.username
        )
        shuffle_all_songs = library_songs(
            settings,
            LibraryFilter(),
            actor=session.username,
            limit=1,
        )
        songs = library_songs(
            settings,
            LibraryFilter(
                query=q,
                status=status,
                era=era,
                key_value=key,
                dim=dim,
                value=value,
                tag_values=tag_values,
                untagged_dims=selected_untagged,
                unheard=unheard,
                hearted=hearted,
                random_seed=seed,
                skip_short_sketches=skip_sketches,
            ),
            actor=session.username,
            sort=sort,
        )
        error = None
        code = 200
        dig_tracks = prioritized_dig(
            settings,
            songs,
            share_id=0,
            actor=session.username,
        )
        dig_untagged_tracks = [
            track for track in dig_tracks if bool(track.get("untagged"))
        ]
    except SearchError:
        songs = []
        basic_facet_counts = LibraryFacetCounts({}, {}, {}, {}, 0, 0)
        shuffle_all_songs = []
        dig_tracks = []
        dig_untagged_tracks = []
        error = "Search could not be completed."
        code = 400
    page_size = 48
    page = songs[offset : offset + page_size]
    for item in page:
        options = bounce_download_options(
            settings, str(item["bounce_ulid"])
        )
        original = next(
            (
                option
                for option in options
                if option["format"] == "original"
            ),
            None,
        )
        item["download_original_size"] = (
            int(original["size"]) if original else 0
        )
    next_url = (
        _owner_library_url(current, offset=offset + page_size)
        if offset + page_size < len(songs)
        else None
    )
    era_values = sorted(
        {
            (
                "unknown" if name.casefold() == "undated" else name.casefold(),
                name,
            )
            for name, count in basic_facet_counts.eras.items()
            if count
        },
        key=lambda item: item[1].casefold(),
    )
    key_values = sorted(
        (value for value, count in basic_facet_counts.keys.items() if count),
        key=str.casefold,
    )
    released_count = int(basic_facet_counts.statuses.get("released", 0))
    status_values = sorted(
        (
            value
            for value, count in basic_facet_counts.statuses.items()
            if value != "released" and count
        ),
        key=str.casefold,
    )
    if released_count:
        status_values.append("released")
    era_counts = {
        "unknown" if name.casefold() == "undated" else name.casefold(): count
        for name, count in basic_facet_counts.eras.items()
    }
    key_counts = basic_facet_counts.keys
    basic_status_counts = basic_facet_counts.statuses
    vocab = filter_vocabulary(settings)
    tag_counts = filter_vocabulary_counts(settings)
    filters = {
        "era": [
            {
                "label": label,
                "url": _owner_library_url(
                    current,
                    era=None if era == css else css,
                ),
                "active": era == css,
                "count": era_counts[css],
            }
            for css, label in era_values
        ],
        "key": [
            {
                "label": item,
                "url": _owner_library_url(
                    current,
                    key=None if key == item else item,
                ),
                "active": key == item,
                "count": key_counts[item],
            }
            for item in key_values
        ],
        "status": [
            {
                "label": item,
                "url": _owner_library_url(
                    current,
                    status=None if status == item else item,
                ),
                "active": status == item,
                "released": item == "released",
                "count": basic_status_counts[item],
            }
            for item in status_values
        ],
    }
    tag_filters: dict[str, list[dict[str, Any]]] = {}
    for tag_dim in ("vibe", "instr", "collab", "use"):
        selected_values = tag_values[tag_dim]
        dimension_rows: list[dict[str, Any]] = []
        for item in tag_counts[tag_dim]:
            item_value = str(item["value"])
            active = item_value in selected_values
            normal_values = [] if active else [item_value]
            multi_values = (
                [
                    selected
                    for selected in selected_values
                    if selected != item_value
                ]
                if active
                else [*selected_values, item_value]
            )
            remaining_untagged = [
                selected
                for selected in selected_untagged
                if selected != tag_dim
            ]
            dimension_rows.append(
                {
                    "label": item_value,
                    "url": _owner_library_url(
                        current,
                        **{
                            tag_dim: normal_values,
                            "untagged_dim": remaining_untagged,
                        },
                    ),
                    "multi_url": _owner_library_url(
                        current,
                        **{
                            tag_dim: multi_values,
                            "untagged_dim": remaining_untagged,
                        },
                    ),
                    "active": active,
                    "count": int(item["count"]),
                }
            )
        tag_filters[tag_dim] = dimension_rows
    tag_untagged_counts = untagged_dimension_counts(settings)
    tag_untagged_filters = {}
    for tag_dim in ("vibe", "instr", "collab", "use"):
        active = tag_dim in selected_untagged
        remaining = [
            item for item in selected_untagged if item != tag_dim
        ]
        tag_untagged_filters[tag_dim] = {
            "active": active,
            "count": tag_untagged_counts[tag_dim],
            "url": _owner_library_url(
                current,
                **{
                    tag_dim: [],
                    "untagged_dim": (
                        remaining if active else [*selected_untagged, tag_dim]
                    ),
                },
            ),
        }
    active_filters: list[dict[str, str]] = []
    for name, selected_value in (
        ("search", q),
        ("status", status or ""),
        ("era", era or ""),
        ("key", key or ""),
    ):
        if selected_value:
            active_filters.append(
                {
                    "label": f"{name}: {selected_value}",
                    "url": _owner_library_url(
                        current,
                        **{("q" if name == "search" else name): None},
                    ),
                }
            )
    for tag_dim, selected_values in tag_values.items():
        for selected_value in selected_values:
            active_filters.append(
                {
                    "label": f"{tag_dim}: {selected_value}",
                    "url": _owner_library_url(
                        current,
                        **{
                            tag_dim: [
                                item
                                for item in selected_values
                                if item != selected_value
                            ]
                        },
                    ),
                }
            )
    for tag_dim in selected_untagged:
        active_filters.append(
            {
                "label": f"{tag_dim}: untagged",
                "url": _owner_library_url(
                    current,
                    untagged_dim=[
                        item for item in selected_untagged if item != tag_dim
                    ],
                ),
            }
        )
    context = _context(
        request,
        session,
        songs=page,
        total=len(songs),
        offset=offset,
        next_url=next_url,
        vocab=vocab,
        filters=filters,
        tag_filters=tag_filters,
        tag_untagged_counts=tag_untagged_counts,
        tag_untagged_filters=tag_untagged_filters,
        active_filters=active_filters,
        unheard_count=basic_facet_counts.unheard,
        hearted_count=basic_facet_counts.hearted,
        untagged_count=untagged_vibe_count(settings),
        unheard_url=_owner_library_url(current, unheard=not unheard),
        hearted_url=_owner_library_url(current, hearted=not hearted),
        untagged_url=_owner_library_url(
            current,
            untagged_dim=(
                [
                    item
                    for item in selected_untagged
                    if item != "vibe"
                ]
                if "vibe" in selected_untagged
                else [*selected_untagged, "vibe"]
            ),
            vibe=[],
        ),
        quality_url=_owner_library_url(
            current,
            skip_sketches=not skip_sketches,
        ),
        collections=collection_summaries(settings),
        sorts=[
            {
                "label": item,
                "url": _owner_library_url(
                    current,
                    sort=item,
                    seed=secrets.token_hex(8) if item == "random" else None,
                ),
                "active": sort == item,
            }
            for item in ("newest", "oldest", "longest", "shortest", "random")
        ],
        header_sorts={
            name: {
                "url": _owner_library_url(
                    current,
                    sort=(
                        descending
                        if sort == ascending
                        else ascending
                    ),
                    seed=None,
                ),
                "active": sort in {ascending, descending},
                "direction": (
                    "↓"
                    if sort == descending
                    else ("↑" if sort == ascending else "↕")
                ),
            }
            for name, ascending, descending in (
                ("title", "title", "title-desc"),
                ("era", "era", "era-desc"),
                ("key", "key", "key-desc"),
                ("bpm", "bpm", "bpm-desc"),
                ("length", "shortest", "longest"),
                ("versions", "versions", "versions-desc"),
            )
        },
        shuffle_all_seed=shuffle_all_songs[0] if shuffle_all_songs else None,
        shuffle_this_seed=secrets.choice(songs) if songs else None,
        dig_seed=dig_tracks[0] if dig_tracks else None,
        dig_untagged_seed=(
            dig_untagged_tracks[0] if dig_untagged_tracks else None
        ),
        shuffle_label=(
            f"shuffle these {len(songs)}" if songs else None
        ),
        query=q,
        selected=current,
        error=error,
        preview_detail=(
            _detail_panel_values(settings, str(page[0]["song_ulid"]))
            if page else None
        ),
        selection_max_count=SELECTION_MAX_COUNT,
        selection_max_bytes=SELECTION_MAX_BYTES,
    )
    if request.headers.get("HX-Request") == "true" and offset:
        return templates.TemplateResponse(
            request=request,
            name="owner/fragments/library_rows.html",
            context=context,
            status_code=code,
        )
    return templates.TemplateResponse(
        request=request,
        name="owner/library.html",
        context=context,
        status_code=code,
    )


@router.get("/songs/{song_ulid}", response_class=HTMLResponse)
def song(request: Request, song_ulid: str) -> Response:
    session = session_or_redirect(request)
    if isinstance(session, RedirectResponse):
        return session
    result = song_detail(get_settings(request), song_ulid)
    if result is None:
        raise HTTPException(status_code=404)
    detail, versions = result
    for version in versions:
        version["downloads"] = bounce_download_options(
            get_settings(request), str(version["bounce_ulid"])
        )
    stem_values = _stem_panel_values(get_settings(request), versions)
    return templates.TemplateResponse(
        request=request,
        name="owner/song.html",
        context=_context(
            request,
            session,
            song=detail,
            versions=versions,
            chips=chip_vocabulary(get_settings(request)),
            active=(
                active_reactions(
                    get_settings(request),
                    bounce_ulid=str(versions[0]["bounce_ulid"]),
                    actor=session.username,
                )
                if versions
                else {"heart": False, "chips": set(), "notes": []}
            ),
            unheard=(
                track_is_unheard(
                    get_settings(request),
                    str(versions[0]["bounce_ulid"]),
                    actor=session.username,
                )
                if versions
                else False
            ),
            neighbours=song_neighbours(get_settings(request), int(detail["id"])),
            tag_panel=song_tag_panel(get_settings(request), song_ulid),
            notes=(
                notes_for_track(
                    get_settings(request),
                    bounce_ulid=str(versions[0]["bounce_ulid"]),
                )
                if versions
                else []
            ),
            **stem_values,
        ),
    )

