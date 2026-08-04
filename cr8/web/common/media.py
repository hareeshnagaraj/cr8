"""Catalog-derived, contained mirror artifact serving."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from .queries import track_by_ulid
from .settings import AppSettings


Artifact = Literal["audio", "peaks", "art"]
PreviewStyle = Literal["spectral", "envelope"]


def artifact_path(
    settings: AppSettings, track: dict[str, object], artifact: Artifact
) -> Path:
    root = settings.mirror_root.resolve(strict=True)
    if artifact == "audio":
        candidate = root / str(track["mirror_relpath"])
    elif artifact == "peaks":
        candidate = root / "peaks" / f"{track['bounce_ulid']}.json"
    else:
        candidate = root / "art" / f"{track['song_ulid']}.jpg"
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="artifact unavailable") from exc
    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=404, detail="artifact unavailable")
    return resolved


def serve(
    request: Request,
    settings: AppSettings,
    *,
    bounce_ulid: str,
    artifact: Artifact,
    download: bool = False,
) -> FileResponse:
    track = track_by_ulid(settings, bounce_ulid)
    if track is None:
        raise HTTPException(status_code=404, detail="track unavailable")
    path = artifact_path(settings, track, artifact)
    media_type = {
        "audio": "audio/mpeg",
        "peaks": "application/json",
        "art": "image/jpeg",
    }[artifact]
    filename = None
    if download:
        source = Path(str(track["source_stem"])).stem or str(track["title"])
        qualifier = (
            f"-{track['stem_kind']}" if track.get("stem_kind") else ""
        )
        filename = f"{source}{qualifier}.mp3"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


def serve_preview(
    request: Request,
    settings: AppSettings,
    *,
    bounce_ulid: str,
    style: PreviewStyle,
) -> FileResponse:
    track = track_by_ulid(settings, bounce_ulid)
    if track is None or style not in {"spectral", "envelope"}:
        raise HTTPException(status_code=404, detail="preview unavailable")
    root = settings.mirror_root.resolve(strict=True)
    candidate = root / "art-preview" / style / f"{track['bounce_ulid']}.jpg"
    try:
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="preview unavailable") from exc
    if not path.is_relative_to(root):
        raise HTTPException(status_code=404, detail="preview unavailable")
    return FileResponse(
        path,
        media_type="image/jpeg",
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


def serve_strip(
    request: Request,
    settings: AppSettings,
    *,
    bounce_ulid: str,
) -> FileResponse:
    track = track_by_ulid(settings, bounce_ulid)
    if track is None:
        raise HTTPException(status_code=404, detail="strip unavailable")
    root = settings.mirror_root.resolve(strict=True)
    candidate = root / "art-strips" / f"{track['bounce_ulid']}.jpg"
    try:
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="strip unavailable") from exc
    if not path.is_relative_to(root):
        raise HTTPException(status_code=404, detail="strip unavailable")
    return FileResponse(
        path,
        media_type="image/jpeg",
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )
