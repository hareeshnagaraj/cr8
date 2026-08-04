"""Contained, catalog-derived download assets and streaming ZIP output."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
import mimetypes
from pathlib import Path
import struct
import unicodedata
import zlib

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from ...paths import archive_location
from .database import fetch_all, fetch_one, reading
from .settings import AppSettings


SELECTION_MAX_COUNT = 50
SELECTION_MAX_BYTES = 2 * 1024 * 1024 * 1024
_LOSSLESS_EXTENSIONS = frozenset({".wav", ".aif", ".aiff", ".flac"})
_ZIP_FLAGS = 0x0808  # UTF-8 names plus a trailing data descriptor.
_ZIP_LOCAL = struct.Struct("<IHHHHHIIIHH")
_ZIP_DESCRIPTOR = struct.Struct("<IIII")
_ZIP_CENTRAL = struct.Struct("<IHHHHHHIIIHHHHHII")
_ZIP_END = struct.Struct("<IHHHHIIH")


@dataclass(frozen=True)
class DownloadAsset:
    path: Path
    filename: str
    media_type: str
    size: int


@dataclass(frozen=True)
class SelectionZip:
    assets: tuple[DownloadAsset, ...]
    requested_count: int
    requested_bytes: int

    @property
    def trimmed_count(self) -> int:
        return self.requested_count - len(self.assets)

    @property
    def included_bytes(self) -> int:
        return sum(asset.size for asset in self.assets)


def _contained_file(root: Path, relpath: str) -> Path:
    try:
        resolved_root = root.resolve(strict=True)
        relative = Path(relpath)
        if relative.is_absolute():
            raise ValueError("absolute catalog path")
        resolved = (resolved_root / relative).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="download unavailable") from exc
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="download unavailable")
    return resolved


def _contained_source_file(settings: AppSettings, relpath: str) -> Path:
    try:
        archive = archive_location(settings.archive_roots, relpath)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="download unavailable") from exc
    if archive is None:
        return _contained_file(settings.resolved_corpus_root, relpath)
    root, relative = archive
    return _contained_file(root, relative.as_posix())


def _clean_filename(value: str, *, fallback: str) -> str:
    name = unicodedata.normalize("NFC", Path(value).name)
    name = "".join(
        character
        for character in name
        if ord(character) >= 32 and character not in {'"', "/", "\\", ":"}
    ).strip(" .")
    return name or fallback


def _media_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _original_row(settings: AppSettings, bounce_ulid: str):
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT f.relpath, f.ext, b.source_stem
            FROM bounces AS b
            JOIN files AS f ON f.bounce_id=b.id
            WHERE b.public_id=? AND f.layer='curated'
              AND f.missing_since IS NULL
            ORDER BY CASE LOWER(COALESCE(f.ext, ''))
                       WHEN '.wav' THEN 0
                       WHEN '.aif' THEN 1
                       WHEN '.aiff' THEN 2
                       WHEN '.flac' THEN 3
                       ELSE 99
                     END,
                     f.relpath
            """,
            (bounce_ulid,),
        )
    return next(
        (
            row
            for row in rows
            if str(row["ext"] or Path(str(row["relpath"])).suffix).casefold()
            in _LOSSLESS_EXTENSIONS
        ),
        None,
    )


def bounce_download_asset(
    settings: AppSettings,
    bounce_ulid: str,
    *,
    format: str,
) -> DownloadAsset:
    if format == "original":
        row = _original_row(settings, bounce_ulid)
        if row is None:
            raise HTTPException(status_code=404, detail="original unavailable")
        path = _contained_source_file(settings, str(row["relpath"]))
        filename = _clean_filename(
            str(row["relpath"]), fallback=f"{bounce_ulid}{path.suffix}"
        )
    elif format == "mp3":
        with reading(settings.db_path) as connection:
            row = fetch_one(
                connection,
                """
                SELECT b.source_stem, mf.mirror_relpath
                FROM bounces AS b
                JOIN mirror_files AS mf ON mf.bounce_id=b.id
                WHERE b.public_id=?
                """,
                (bounce_ulid,),
            )
        if row is None:
            raise HTTPException(status_code=404, detail="rendition unavailable")
        path = _contained_file(
            settings.mirror_root, str(row["mirror_relpath"])
        )
        stem = _clean_filename(
            str(row["source_stem"]), fallback=bounce_ulid
        )
        filename = f"{Path(stem).stem}.mp3"
    else:
        raise HTTPException(status_code=400, detail="invalid download format")
    return DownloadAsset(
        path=path,
        filename=filename,
        media_type=_media_type(path),
        size=path.stat().st_size,
    )


def stem_download_asset(
    settings: AppSettings, stem_ulid: str
) -> DownloadAsset:
    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection,
            """
            SELECT st.archive_relpath, st.kind, b.source_stem
            FROM stems AS st
            JOIN stem_runs AS sr ON sr.id=st.run_id AND sr.ok=1
            JOIN bounces AS b ON b.id=st.bounce_id
            WHERE st.public_id=?
            """,
            (stem_ulid,),
        )
    if row is None:
        raise HTTPException(status_code=404, detail="stem unavailable")
    relpath = Path(str(row["archive_relpath"]))
    if relpath.parts and relpath.parts[0] == "stems":
        relpath = Path(*relpath.parts[1:])
    path = _contained_file(settings.resolved_stems_root, str(relpath))
    source = _clean_filename(
        str(row["source_stem"]), fallback=stem_ulid
    )
    filename = f"{Path(source).stem}-{row['kind']}{path.suffix.casefold()}"
    return DownloadAsset(
        path=path,
        filename=filename,
        media_type=_media_type(path),
        size=path.stat().st_size,
    )


def human_size(size: int) -> str:
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            amount = f"{value:.1f}" if value < 10 and unit != "B" else f"{value:.0f}"
            return f"{amount.rstrip('0').rstrip('.')} {unit}"
        value /= 1024
    return f"{size} B"


def bounce_download_options(
    settings: AppSettings, bounce_ulid: str
) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    for format in ("original", "mp3"):
        try:
            asset = bounce_download_asset(
                settings, bounce_ulid, format=format
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        options.append(
            {
                "format": format,
                "ext": asset.path.suffix.casefold().lstrip("."),
                "size": asset.size,
                "size_label": human_size(asset.size),
                "label": (
                    f"original · {asset.path.suffix.casefold().lstrip('.')} · "
                    f"{human_size(asset.size)}"
                    if format == "original"
                    else f"mp3 · 320 · {human_size(asset.size)}"
                ),
            }
        )
    return options


def selection_zip(
    settings: AppSettings, bounce_ulids: Sequence[str]
) -> SelectionZip:
    requested = tuple(dict.fromkeys(bounce_ulids))
    assets: list[DownloadAsset] = []
    requested_bytes = 0
    used_names: dict[str, int] = {}
    for bounce_ulid in requested:
        asset = bounce_download_asset(
            settings, bounce_ulid, format="original"
        )
        requested_bytes += asset.size
        if (
            len(assets) >= SELECTION_MAX_COUNT
            or sum(item.size for item in assets) + asset.size
            > SELECTION_MAX_BYTES
        ):
            continue
        count = used_names.get(asset.filename.casefold(), 0) + 1
        used_names[asset.filename.casefold()] = count
        if count > 1:
            path = Path(asset.filename)
            asset = replace(
                asset, filename=f"{path.stem}-{count}{path.suffix}"
            )
        assets.append(asset)
    if not assets:
        raise HTTPException(
            status_code=413, detail="selection exceeds the download cap"
        )
    return SelectionZip(
        assets=tuple(assets),
        requested_count=len(requested),
        requested_bytes=requested_bytes,
    )


def _dos_datetime(timestamp: float) -> tuple[int, int]:
    value = datetime.fromtimestamp(timestamp)
    year = min(max(value.year, 1980), 2107)
    dos_date = ((year - 1980) << 9) | (value.month << 5) | value.day
    dos_time = (value.hour << 11) | (value.minute << 5) | (value.second // 2)
    return dos_time, dos_date


def stream_zip(assets: Sequence[DownloadAsset]) -> Iterator[bytes]:
    central: list[bytes] = []
    offset = 0
    for asset in assets:
        encoded_name = asset.filename.encode("utf-8")
        modified_time, modified_date = _dos_datetime(
            asset.path.stat().st_mtime
        )
        local = _ZIP_LOCAL.pack(
            0x04034B50,
            20,
            _ZIP_FLAGS,
            0,
            modified_time,
            modified_date,
            0,
            0,
            0,
            len(encoded_name),
            0,
        )
        yield local
        yield encoded_name
        crc = 0
        size = 0
        with asset.path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                crc = zlib.crc32(chunk, crc)
                size += len(chunk)
                yield chunk
        descriptor = _ZIP_DESCRIPTOR.pack(
            0x08074B50, crc & 0xFFFFFFFF, size, size
        )
        yield descriptor
        central.append(
            _ZIP_CENTRAL.pack(
                0x02014B50,
                0x0314,
                20,
                _ZIP_FLAGS,
                0,
                modified_time,
                modified_date,
                crc & 0xFFFFFFFF,
                size,
                size,
                len(encoded_name),
                0,
                0,
                0,
                0,
                0o100644 << 16,
                offset,
            )
            + encoded_name
        )
        offset += len(local) + len(encoded_name) + size + len(descriptor)
    central_offset = offset
    for record in central:
        yield record
        offset += len(record)
    yield _ZIP_END.pack(
        0x06054B50,
        0,
        0,
        len(central),
        len(central),
        offset - central_offset,
        central_offset,
        0,
    )


def zip_content_length(assets: Sequence[DownloadAsset]) -> int:
    names = sum(len(asset.filename.encode("utf-8")) for asset in assets)
    return (
        sum(asset.size for asset in assets)
        + len(assets)
        * (_ZIP_LOCAL.size + _ZIP_DESCRIPTOR.size + _ZIP_CENTRAL.size)
        + names * 2
        + _ZIP_END.size
    )


def file_download_response(asset: DownloadAsset) -> FileResponse:
    return FileResponse(
        asset.path,
        media_type=asset.media_type,
        filename=asset.filename,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def selection_download_response(selection: SelectionZip) -> StreamingResponse:
    filename = f"crate-selected-{datetime.now().date().isoformat()}.zip"
    return StreamingResponse(
        stream_zip(selection.assets),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(zip_content_length(selection.assets)),
            "Cache-Control": "private, no-store",
            "X-CR8-Included-Count": str(len(selection.assets)),
            "X-CR8-Trimmed-Count": str(selection.trimmed_count),
        },
    )
