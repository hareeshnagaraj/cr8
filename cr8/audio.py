"""Read-only source-audio selection, hashing, and probing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3

from .config import Config
from .tooling import find_tool, run_tool
from .paths import source_path


LOSSLESS_EXTENSIONS = frozenset({".wav", ".aif", ".aiff", ".flac"})


@dataclass(frozen=True)
class AudioFile:
    id: int
    bounce_id: int
    relpath: str
    path: Path
    ext: str
    duration_s: float | None
    sha256: str | None


@dataclass(frozen=True)
class SourceChoice:
    source: AudioFile
    encoder_settings: str
    mismatch: tuple[AudioFile, AudioFile, float | None] | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def probe_duration(path: Path, *, state_dir: Path | None = None) -> float | None:
    ffprobe = find_tool("ffprobe", state_dir=state_dir)
    if ffprobe is None:
        return None
    try:
        result = run_tool(
            ffprobe,
            (
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                path,
            ),
            timeout=60,
        )
    except (OSError, TimeoutError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def bounce_files(
    connection: sqlite3.Connection, config: Config, bounce_id: int
) -> list[AudioFile]:
    rows = connection.execute(
        """
        SELECT id, bounce_id, relpath, ext, duration_s, sha256
        FROM files
        WHERE bounce_id=? AND layer='curated' AND missing_since IS NULL
        ORDER BY relpath
        """,
        (bounce_id,),
    ).fetchall()
    return [
        AudioFile(
            id=int(row["id"]),
            bounce_id=int(row["bounce_id"]),
            relpath=str(row["relpath"]),
            path=source_path(config, str(row["relpath"])),
            ext=str(row["ext"] or Path(str(row["relpath"])).suffix).casefold(),
            duration_s=(
                float(row["duration_s"]) if row["duration_s"] is not None else None
            ),
            sha256=str(row["sha256"]) if row["sha256"] else None,
        )
        for row in rows
    ]


def analysis_source(files: list[AudioFile]) -> AudioFile | None:
    order = {".wav": 0, ".aif": 1, ".aiff": 2, ".flac": 3, ".mp3": 4, ".m4a": 5}
    existing = [item for item in files if item.path.is_file()]
    return min(existing, key=lambda item: (order.get(item.ext, 99), item.relpath)) if existing else None


def choose_mirror_source(
    files: list[AudioFile], *, state_dir: Path | None = None
) -> SourceChoice:
    existing = [item for item in files if item.path.is_file()]
    if not existing:
        raise ValueError("bounce has no readable source file")
    mp3s = sorted((item for item in existing if item.ext == ".mp3"), key=lambda item: item.relpath)
    lossless = sorted(
        (item for item in existing if item.ext in LOSSLESS_EXTENSIONS),
        key=lambda item: ({".wav": 0, ".aif": 1, ".aiff": 2, ".flac": 3}[item.ext], item.relpath),
    )
    if mp3s and lossless:
        mp3 = mp3s[0]
        original = lossless[0]
        mp3_duration = mp3.duration_s or probe_duration(mp3.path, state_dir=state_dir)
        original_duration = original.duration_s or probe_duration(
            original.path, state_dir=state_dir
        )
        delta = (
            abs(mp3_duration - original_duration)
            if mp3_duration is not None and original_duration is not None
            else None
        )
        if delta is not None and delta <= 1.5:
            return SourceChoice(mp3, "copy-mp3")
        return SourceChoice(
            original,
            "libmp3lame-cbr-320k",
            mismatch=(original, mp3, delta),
        )
    if mp3s:
        return SourceChoice(mp3s[0], "copy-mp3")
    if lossless:
        return SourceChoice(lossless[0], "libmp3lame-cbr-320k")
    source = sorted(existing, key=lambda item: item.relpath)[0]
    return SourceChoice(source, "libmp3lame-cbr-320k")
