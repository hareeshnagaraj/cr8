"""Where a catalogued file actually lives on disk.

The corpus is a read-only mirror of another machine: it is never written,
renamed or retagged, and a sync program owns it. Uploads therefore cannot land
there, so they live under their own root and carry a `_drops/` prefix in their
relpath. Everything downstream — mirror builds, key detection, verification,
downloads — asks this module rather than inventing archive prefixes itself, so
there is a single answer to "where is this file" and it cannot drift.

    _drops/henry/bounce.wav          ->  <base>/drops/henry/bounce.wav
    _archive/2021-New-Projects/x.wav ->  <configured archive root>/x.wav
    2-15-25-demos/take3.wav          ->  <corpus root>/2-15-25-demos/take3.wav
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Config


DROPS_PREFIX = "_drops/"
ARCHIVE_PREFIX = "_archive/"


def is_drop(relpath: str) -> bool:
    return relpath.startswith(DROPS_PREFIX)


def is_archive(relpath: str) -> bool:
    return relpath.startswith(ARCHIVE_PREFIX)


def archive_root_key(root: Path) -> str:
    """Stable files-table prefix for one configured archive root."""
    return f"{ARCHIVE_PREFIX}{root.name}/"


def archive_relpath(root: Path, path: str | Path) -> str:
    """Root-qualified files-table path for a file inside an archive."""
    candidate = Path(path)
    relative = candidate.relative_to(root) if candidate.is_absolute() else candidate
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"archive path escapes root: {path}")
    return archive_root_key(root) + relative.as_posix()


def archive_location(
    archive_roots: tuple[Path, ...], relpath: str
) -> tuple[Path, Path] | None:
    """Return the configured root and inner path for an archive identity."""
    if not is_archive(relpath):
        return None
    remainder = relpath[len(ARCHIVE_PREFIX) :]
    root_name, separator, inner = remainder.partition("/")
    relative = Path(inner)
    if (
        not separator
        or not root_name
        or not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        raise ValueError(f"invalid archive relpath: {relpath}")
    for root in archive_roots:
        if root.name == root_name:
            return root, relative
    raise ValueError(f"archive root is not configured: {root_name}")


def scan_root_key(relpath: str) -> str:
    """Identity used to apply disappearance inference independently per root."""
    if not is_archive(relpath):
        return ""
    remainder = relpath[len(ARCHIVE_PREFIX) :]
    root_name, separator, _ = remainder.partition("/")
    return f"{ARCHIVE_PREFIX}{root_name}/" if separator and root_name else ARCHIVE_PREFIX


def source_root(config: "Config", relpath: str) -> Path:
    """Configured filesystem root for one files-table path."""
    if is_drop(relpath):
        return config.corpus.resolved_drops_root
    archive = archive_location(config.corpus.archive_roots, relpath)
    if archive is not None:
        return archive[0]
    return config.corpus.root


def source_path(config: "Config", relpath: str) -> Path:
    """Absolute path for a relpath recorded in the files table."""
    if is_drop(relpath):
        return config.corpus.resolved_drops_root / relpath[len(DROPS_PREFIX) :]
    archive = archive_location(config.corpus.archive_roots, relpath)
    if archive is not None:
        root, relative = archive
        return root / relative
    return config.corpus.root / relpath


def drop_relpath(username: str, filename: str) -> str:
    """The relpath an uploaded file is recorded under."""
    return f"{DROPS_PREFIX}{username}/{filename}"
