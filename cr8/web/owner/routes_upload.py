"""Getting audio into the crate without touching the machine it runs on.

Until now adding music meant putting a file on this Mac. That works for one
person sitting in front of it and for nobody else. Uploads land in a drops root
that this app owns — never in the corpus, which is a read-only mirror another
machine syncs — and from there the ordinary pipeline picks them up: parse,
resolve, mirror build, key and tempo detection. A dropped file becomes a
catalogued track by the same path as everything else.

Two things here are deliberate rather than incidental:

  Authentication is decided from headers, before a single byte of the body is
  read. Otherwise an unauthenticated caller can make the server swallow half a
  gigabyte before being told no.

  The size cap first rejects an oversized Content-Length. Form parsing then
  buffers each upload into a SpooledTemporaryFile before the copy loop gets it;
  that loop counts the bytes again and deletes its output if the cap is exceeded.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ...config import ConfigError, load_config
from ...db import utc_now
from ...paths import drop_relpath
from ...public_ids import new_ulid
from ...tooling import find_tool, run_tool
from ..common import tokens
from ..common.auth import user_session
from ..common.database import fetch_all, fetch_one, mutate, reading
from .deps import settings as _settings


router = APIRouter()

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_FILENAME = 120
CHUNK = 1024 * 1024
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv"})


def _drops_root(request: Request) -> Path:
    settings = _settings(request)
    configured = getattr(settings, "drops_root", None)
    if configured:
        return Path(configured)
    try:
        return load_config(settings.base_dir / "config.toml").corpus.resolved_drops_root
    except (ConfigError, FileNotFoundError, OSError):
        return settings.base_dir / "drops"


def _allowed_extensions(request: Request) -> set[str]:
    settings = _settings(request)
    try:
        return set(load_config(settings.base_dir / "config.toml").audio.extensions)
    except (ConfigError, FileNotFoundError, OSError):
        return {".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac"}


def _identify(request: Request) -> tuple[str, str]:
    """Who is uploading, decided before the body is touched.

    A signed-in person in a browser, or a machine holding an upload token. The
    token path is what Henry's watcher uses; it is hashed the same way invites
    are and can be revoked from the admin page.
    """
    session = user_session(request, _settings(request))
    if session is not None:
        return session.username, "browser"

    header = request.headers.get("authorization", "")
    raw_token = ""
    if header.lower().startswith("bearer "):
        raw_token = header[7:].strip()
    if not raw_token:
        raw_token = request.headers.get("x-cr8-token", "").strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="login or an upload token required")

    settings = _settings(request)
    digest = tokens.digest(raw_token, settings.session_secret)
    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection,
            "SELECT * FROM api_tokens WHERE token_sha256=? AND kind='upload'",
            (digest,),
        )
    state = tokens.check_state(row)
    if not state.usable:
        raise HTTPException(status_code=401, detail=f"token {state.reason}")

    mutate(
        settings.db_path,
        lambda connection: connection.execute(
            """
            UPDATE api_tokens
            SET use_count=use_count+1, last_used_at=?
            WHERE id=?
            """,
            (utc_now(), int(row["id"])),
        ),
    )
    return str(row["username"]), "watcher"


def safe_filename(raw: str) -> str:
    """A name that cannot leave the directory it is meant for.

    Takes the basename, strips anything that is not obviously a filename
    character, and keeps the extension. "../../etc/passwd.wav" comes out as
    "etc_passwd.wav" and lands in the uploader's own folder like everything
    else.
    """
    base = os.path.basename(raw.replace("\\", "/")).strip()
    base = base.replace("\x00", "")
    # Split on the last dot ourselves rather than asking pathlib. Python 3.14
    # changed how it parses names that begin with dots - "....wav" is stem
    # "....wav" with no suffix on 3.14 and stem "..." with suffix ".wav" on
    # 3.13 - so the same upload was sanitised differently depending on which
    # machine received it, and on one of them lost its extension entirely and
    # was then refused by the audio whitelist.
    stripped = base.lstrip(".")
    if "." in stripped:
        stem, _, extension = stripped.rpartition(".")
        suffix = f".{extension}".casefold()
    else:
        stem, suffix = stripped, ""
    stem = re.sub(r"[^\w \-.()&']+", "_", stem, flags=re.UNICODE).strip(" .")
    stem = re.sub(r"_{2,}", "_", stem)
    if not stem:
        stem = "upload"
    return f"{stem[:MAX_FILENAME]}{suffix}"


@router.get("/api/uploads")
def recent_uploads(request: Request) -> JSONResponse:
    session = user_session(request, _settings(request))
    if session is None:
        raise HTTPException(status_code=401, detail="login required")
    with reading(_settings(request).db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT u.ulid, u.filename, u.relpath, u.size_bytes, u.uploaded_by,
                   u.source, u.created_at, u.state, u.detail,
                   f.id AS file_id, f.parse_status, f.bounce_id,
                   b.public_id AS bounce_ulid, s.title, s.public_id AS song_ulid
            FROM uploads AS u
            LEFT JOIN files AS f ON f.relpath=u.relpath
            LEFT JOIN bounces AS b ON b.id=f.bounce_id
            LEFT JOIN songs AS s ON s.id=b.song_id
            ORDER BY u.created_at DESC, u.id DESC
            LIMIT 100
            """,
            (),
        )
    return JSONResponse(
        {
            "uploads": [
                {
                    "ulid": str(row["ulid"]),
                    "filename": str(row["filename"]),
                    "size_bytes": int(row["size_bytes"]),
                    "uploaded_by": str(row["uploaded_by"]),
                    "source": str(row["source"]),
                    "created_at": str(row["created_at"]),
                    # State is derived from what the pipeline actually did, not
                    # from whether we managed to poke it: a file waiting behind
                    # a running ingest is pending, not failed.
                    "state": _state_for(row),
                    "detail": row["detail"],
                    "title": row["title"],
                    "song_ulid": row["song_ulid"],
                    "bounce_ulid": row["bounce_ulid"],
                }
                for row in rows
            ]
        }
    )


def _state_for(row: Any) -> str:
    if str(row["state"]) == "rejected":
        return "rejected"
    if row["bounce_ulid"]:
        return "ingested"
    if row["file_id"] is not None:
        if str(row["parse_status"] or "") == "residue":
            return "needs_review"
        return "ingesting"
    return "pending"


@router.post("/api/upload")
async def upload(request: Request) -> JSONResponse:
    # Before the body. This is the whole point of doing it here.
    username, source = _identify(request)
    settings = _settings(request)
    allowed = _allowed_extensions(request)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES * 1.05:
        raise HTTPException(status_code=413, detail="that file is too big")

    form = await request.form()
    items = [value for key, value in form.multi_items() if hasattr(value, "filename")]
    if not items:
        raise HTTPException(status_code=400, detail="no file in the request")

    destination_root = _drops_root(request) / username
    destination_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for item in items:
        results.append(
            await _store_one(
                item,
                username=username,
                source=source,
                allowed=allowed,
                destination_root=destination_root,
                settings=settings,
            )
        )

    stored = [entry for entry in results if entry["ok"]]
    if stored:
        _poke_ingest(settings)
    return JSONResponse(
        {"stored": len(stored), "files": results},
        status_code=201 if stored else 400,
    )


async def _store_one(
    item: Any,
    *,
    username: str,
    source: str,
    allowed: set[str],
    destination_root: Path,
    settings: AppSettings,
) -> dict[str, Any]:
    original = str(getattr(item, "filename", "") or "")
    filename = safe_filename(original)
    suffix = Path(filename).suffix.casefold()
    is_video = suffix in VIDEO_EXTENSIONS
    if suffix not in allowed and not is_video:
        return {
            "ok": False,
            "filename": original,
            "error": f"{suffix or 'that kind of file'} is not audio we catalogue",
        }

    if is_video:
        extracted = await _extract_uploaded_video(
            item,
            filename=filename,
            destination_root=destination_root,
            settings=settings,
        )
        if isinstance(extracted, str):
            return {"ok": False, "filename": original, "error": extracted}
        target, written, sha256 = extracted
    else:
        target = _available_target(destination_root, filename)
        try:
            written, sha256 = await _write_upload(item, target)
        except _TooBig:
            target.unlink(missing_ok=True)
            return {
                "ok": False,
                "filename": original,
                "error": "that file is over the 512 MB limit",
            }
        except OSError as exc:
            target.unlink(missing_ok=True)
            return {
                "ok": False,
                "filename": original,
                "error": f"could not save it: {exc}",
            }
        if written == 0:
            target.unlink(missing_ok=True)
            return {"ok": False, "filename": original, "error": "that file was empty"}

    relpath = drop_relpath(username, target.name)
    duplicate: dict[str, Any] = {}

    def record(connection: Any) -> None:
        existing = fetch_one(
            connection, "SELECT filename FROM uploads WHERE sha256=?", (sha256,)
        )
        if existing is not None:
            duplicate["of"] = str(existing["filename"])
            return
        connection.execute(
            """
            INSERT INTO uploads(
              ulid, filename, relpath, sha256, size_bytes, uploaded_by,
              source, created_at, state
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                new_ulid(),
                target.name,
                relpath,
                sha256,
                written,
                username,
                source,
                utc_now(),
            ),
        )

    mutate(settings.db_path, record)

    if duplicate:
        # Already in the crate. Uploading the same bounce twice is a thing
        # people do; it should be a quiet no-op, not a second copy.
        target.unlink(missing_ok=True)
        return {
            "ok": True,
            "filename": target.name,
            "duplicate_of": duplicate["of"],
            "bytes": written,
        }

    return {"ok": True, "filename": target.name, "bytes": written}


def _available_target(destination_root: Path, filename: str) -> Path:
    target = destination_root / filename
    if target.exists():
        suffix = target.suffix.casefold()
        target = destination_root / f"{target.stem}-{new_ulid()[-6:]}{suffix}"
    return target


async def _write_upload(item: Any, target: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    written = 0
    with target.open("wb") as handle:
        while True:
            chunk = await item.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                raise _TooBig()
            digest.update(chunk)
            handle.write(chunk)
    return written, digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


async def _extract_uploaded_video(
    item: Any,
    *,
    filename: str,
    destination_root: Path,
    settings: AppSettings,
) -> tuple[Path, int, str] | str:
    with tempfile.TemporaryDirectory(prefix="cr8-video-") as temporary:
        temporary_root = Path(temporary)
        source = temporary_root / filename
        try:
            uploaded_bytes, _ = await _write_upload(item, source)
        except _TooBig:
            return "that file is over the 512 MB limit"
        except OSError as exc:
            return f"could not save it: {exc}"
        if uploaded_bytes == 0:
            return "that file was empty"

        extracted = temporary_root / f"{Path(filename).stem}.m4a"
        failure = _extract_video_audio(source, extracted, settings=settings)
        if failure:
            return failure

        target = _available_target(destination_root, extracted.name)
        staging = destination_root / f".{target.name}.{new_ulid()}.part"
        try:
            shutil.copyfile(extracted, staging)
            os.replace(staging, target)
            written = target.stat().st_size
            sha256 = _file_sha256(target)
        except OSError as exc:
            staging.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            return f"could not save the extracted audio: {exc}"

        # Future full-video storage branches here; v1 discards the temporary source.
        return target, written, sha256


def _extract_video_audio(
    source: Path,
    target: Path,
    *,
    settings: AppSettings,
) -> str | None:
    ffmpeg = find_tool("ffmpeg", state_dir=settings.base_dir)
    ffprobe = find_tool("ffprobe", state_dir=settings.base_dir)
    if ffmpeg is None or ffprobe is None:
        return "server video extraction is unavailable — ffmpeg is not installed"

    probe = run_tool(
        ffprobe,
        (
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            source,
        ),
        timeout=30,
    )
    if probe.returncode != 0:
        detail = probe.stderr.strip() or "ffprobe could not read the file"
        return f"could not read that video's audio: {detail}"
    codec = probe.stdout.strip().splitlines()
    if not codec:
        return "that video does not have an audio track"

    command: list[str | Path] = [
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        source,
        "-map",
        "0:a:0",
        "-vn",
    ]
    if codec[0].casefold() == "aac":
        command.extend(("-c:a", "copy"))
    else:
        command.extend(("-c:a", "aac", "-b:a", "192k"))
    command.extend(("-movflags", "+faststart", target))
    result = run_tool(ffmpeg, command, timeout=180)
    if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        detail = result.stderr.strip() or "ffmpeg could not extract the audio track"
        return f"could not extract that video's audio: {detail}"
    return None


class _TooBig(Exception):
    pass


def _poke_ingest(settings: AppSettings) -> None:
    """Ask the pipeline to look now rather than at the next launchd tick.

    The ingest lock is deliberately non-blocking, so a tick already running
    means this poke is dropped. That is fine and must stay silent: the upload
    is on disk and the watcher will find it within a few minutes either way.
    The page reports state from the database, never from whether this worked.
    """
    import subprocess
    import sys

    try:
        subprocess.Popen(
            [sys.executable, "-m", "cr8.cli", "ingest-tick"],
            cwd=str(settings.base_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return
