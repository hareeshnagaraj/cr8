"""Mandatory dependency and database security floors."""

from __future__ import annotations

from importlib import metadata
import re

import apsw


STARLETTE_MINIMUM = (0, 49, 1)
SQLITE_MINIMUM = (3, 53, 2)


class RuntimeFloorError(RuntimeError):
    """Raised when a vulnerable runtime would otherwise boot."""


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if parts is None:
        raise RuntimeFloorError(f"cannot parse runtime version: {value}")
    return tuple(int(part) for part in parts.groups())


def check_runtime() -> None:
    starlette = metadata.version("starlette")
    sqlite = apsw.sqlitelibversion()
    if _version_tuple(starlette) < STARLETTE_MINIMUM:
        raise RuntimeFloorError(
            f"Starlette {starlette} is below required 0.49.1 "
            "(CVE-2025-62727); refusing to boot"
        )
    if _version_tuple(sqlite) < SQLITE_MINIMUM:
        raise RuntimeFloorError(
            f"SQLite {sqlite} is below required 3.53.2 "
            "(CVE-2026-11822/FTS5); refusing to boot"
        )
