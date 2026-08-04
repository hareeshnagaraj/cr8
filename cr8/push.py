"""Guarded rsync transport for a provisioned jukebox."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from urllib.request import Request, urlopen

from .config import Config
from .mirror import SENTINEL
from .tooling import find_tool, run_tool


@dataclass(frozen=True)
class PushSummary:
    tracks: int
    destination: str
    dry_run: bool
    rescan_posted: bool


def _destination_path(destination: str) -> str:
    if ":" not in destination:
        return destination
    _, path = destination.split(":", 1)
    return path


def push_mirror(
    connection: sqlite3.Connection,
    config: Config,
    destination: str,
    *,
    mirror_root: Path | None = None,
    dry_run: bool = False,
    rescan_url: str | None = None,
) -> PushSummary:
    source = (mirror_root or (config.state_dir / "mirror")).resolve()
    if ":" in str(source):
        raise ValueError("mirror source path must be colon-free")
    if not source.is_dir() or source.is_symlink():
        raise ValueError("mirror source must be a real directory")
    if not (source / SENTINEL).is_file():
        raise ValueError(f"mirror source is missing {SENTINEL}")
    target_path = _destination_path(destination)
    if not target_path or ":" in target_path:
        raise ValueError("destination path must be non-empty and colon-free")
    count = sum(1 for path in (source / "tracks").glob("*.mp3") if path.is_file())
    expectation = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM bounces AS b
            WHERE EXISTS (
              SELECT 1 FROM files AS f
              WHERE f.bounce_id=b.id AND f.layer='curated'
                AND f.missing_since IS NULL
            )
            """
        ).fetchone()[0]
    )
    if count * 10 < expectation * 9:
        raise ValueError(
            f"push guard: mirror has {count} tracks, below 90% of expected {expectation}"
        )
    rsync = find_tool("rsync", state_dir=config.state_dir)
    if rsync is None:
        raise ValueError("missing required tool: rsync")
    args: list[str | Path] = ["-az", "--delete", "--max-delete=50"]
    if dry_run:
        args.append("--dry-run")
    args.extend((f"{source}/", destination))
    result = run_tool(rsync, args, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"rsync failed: {result.stderr.strip()}")
    posted = False
    if rescan_url and not dry_run:
        request = Request(rescan_url, data=b"", method="POST")
        with urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"rescan hook returned HTTP {response.status}")
        posted = True
    return PushSummary(
        tracks=count,
        destination=destination,
        dry_run=dry_run,
        rescan_posted=posted,
    )
