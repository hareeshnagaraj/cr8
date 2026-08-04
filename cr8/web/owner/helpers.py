"""Shared non-route helpers for owner HTML/API handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import Response

from ..common.database import fetch_all, fetch_one, reading
from ..common.downloads import (
    bounce_download_options,
    human_size,
    stem_download_asset,
)
from ..common.queries import (
    LibraryFilter,
    active_reactions,
    library_songs,
    song_detail,
    stem_job_for_bounce,
    stems_for_bounce,
    track_by_ulid,
)
from ..common.settings import AppSettings
from ..common.tagging import song_tag_panel
from ..common.templates import make_templates
from .deps import session_or_401, settings

templates = make_templates(Path(__file__).parent / "templates")


def _shuffle_label(
    count: int,
    *,
    q: str,
    status: str | None,
    era: str | None,
    key_value: str | None,
    dim: str | None,
    value: str | None,
    unheard: bool,
    hearted: bool,
) -> str | None:
    if count == 0:
        return None
    if value and dim:
        return f"shuffle {count} {value}"
    if unheard:
        return f"shuffle {count} unheard"
    if hearted:
        return f"shuffle {count} hearted"
    if key_value:
        return f"shuffle {count} {key_value}"
    if era:
        return f"shuffle {count} {era}"
    if status:
        return f"shuffle {count} {status}"
    if q.strip():
        return f"shuffle {count} matches"
    return None


def _owner_library_url(
    current: dict[str, Any],
    **changes: Any,
) -> str:
    values = {**current, **changes}
    compact = {
        key: value
        for key, value in values.items()
        if value not in (None, "", False)
    }
    query = urlencode(compact, doseq=True)
    return f"/?{query}" if query else "/"


def _queue_items(tracks: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for track in tracks:
        item = {
            "id": str(track["bounce_ulid"]),
            "trackUrl": f"/api/tracks/{track['bounce_ulid']}",
            "audioUrl": f"/m/{track['bounce_ulid']}",
            "title": str(track["title"]),
            "reason": str(track.get("reason") or ""),
        }
        if track.get("dig_reason"):
            item["dig_reason"] = str(track["dig_reason"])
            item["dig_reason_label"] = str(track["dig_reason_label"])
        items.append(item)
    return items


def _stem_panel_values(
    app_settings: AppSettings,
    versions: list[dict[str, Any]],
) -> dict[str, Any]:
    for version in versions:
        if "downloads" not in version:
            version["downloads"] = bounce_download_options(
                app_settings, str(version["bounce_ulid"])
            )
    latest = versions[0] if versions else None
    bounce_ulid = str(latest["bounce_ulid"]) if latest else ""
    separated = stems_for_bounce(app_settings, bounce_ulid) if latest else []
    for stem in separated:
        try:
            asset = stem_download_asset(
                app_settings, str(stem["bounce_ulid"])
            )
        except HTTPException:
            stem["download_size_label"] = ""
        else:
            stem["download_size_label"] = human_size(asset.size)
    return {
        "latest": latest,
        "source_stems": [
            version
            for version in versions
            if str(version["mixrole"])
            in {"vox", "novox", "inst", "acap", "bass", "gtar", "stems"}
        ],
        "separated_stems": separated,
        "stem_job": (
            stem_job_for_bounce(app_settings, bounce_ulid) if latest else None
        ),
        "has_default_stems": any(
            stem.get("stem_recipe") == "default-v1" for stem in separated
        ),
        "has_hq_stems": any(
            stem.get("stem_recipe") == "hq-v1" for stem in separated
        ),
        "stems_stale": any(bool(stem.get("stem_stale")) for stem in separated),
    }


def _detail_panel_values(
    app_settings: AppSettings, song_ulid: str
) -> dict[str, Any] | None:
    result = song_detail(app_settings, song_ulid)
    if result is None:
        return None
    song, versions = result
    for version in versions:
        version["downloads"] = bounce_download_options(
            app_settings, str(version["bounce_ulid"])
        )
    return {
        "song": song,
        "versions": versions,
        "panel": song_tag_panel(app_settings, song_ulid),
        **_stem_panel_values(app_settings, versions),
    }


def _write_result(
    request: Request,
    *,
    song_ulids: list[str] | tuple[str, ...] = (),
    message: str,
    undo_id: int | None = None,
) -> Response:
    app_settings = settings(request)
    actor = session_or_401(request).username
    wanted = set(song_ulids)
    candidates = library_songs(
        app_settings,
        LibraryFilter(song_ulids=song_ulids, include_released=True),
        actor=actor,
        limit=max(len(song_ulids), 1),
    )
    by_ulid = {
        str(song["song_ulid"]): song
        for song in candidates
        if str(song["song_ulid"]) in wanted
    }
    songs = [by_ulid[ulid] for ulid in song_ulids if ulid in by_ulid]
    removed = [ulid for ulid in song_ulids if ulid not in by_ulid]
    return templates.TemplateResponse(
        request=request,
        name="owner/fragments/write_result.html",
        context={
            "request": request,
            "message": message,
            "undo_id": undo_id,
            "songs": songs,
            "removed_song_ulids": removed,
        },
    )


def _tag_context(
    app_settings: AppSettings, bounce_ulid: str, *, actor: str
) -> dict[str, Any]:
    track = track_by_ulid(app_settings, bounce_ulid)
    if track is None:
        raise HTTPException(status_code=404)
    active = active_reactions(
        app_settings, bounce_ulid=bounce_ulid, actor=actor
    )
    panel = song_tag_panel(app_settings, str(track["song_ulid"]))
    vibe = next(
        (
            dimension
            for dimension in (panel or {}).get("dimensions", [])
            if dimension["name"] == "vibe"
        ),
        {"values": []},
    )
    active["chips"] = {
        str(item["value"]) for item in vibe["values"] if item["active"]
    }
    return {
        "track": track,
        "active": active,
        "chips": [
            str(item["value"]) for item in vibe["values"]
        ][:6],
    }


def _triage_tracks(
    app_settings: AppSettings, *, actor: str, limit: int = 20
) -> list[dict[str, Any]]:
    candidates = library_songs(
        app_settings, LibraryFilter(), actor=actor, limit=500
    )
    with reading(app_settings.db_path) as connection:
        decided = {
            str(row["bounce_ulid"])
            for row in fetch_all(
                connection,
                """
                SELECT DISTINCT bounce_ulid FROM reactions
                WHERE actor=? AND kind='verdict' AND deleted_at IS NULL
                """,
                (actor,),
            )
        }
    return [
        track
        for track in candidates
        if str(track["bounce_ulid"]) not in decided
    ][:limit]


def _today_triage_count(app_settings: AppSettings, *, actor: str) -> int:
    today = datetime.now(UTC).date().isoformat()
    with reading(app_settings.db_path) as connection:
        row = fetch_one(
            connection,
            """
            SELECT COUNT(*) AS count FROM reactions
            WHERE actor=? AND kind='verdict' AND deleted_at IS NULL
              AND substr(created_at, 1, 10)=?
            """,
            (actor, today),
        )
    return int(row["count"] if row else 0)
