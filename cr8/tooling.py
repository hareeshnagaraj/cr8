"""Safe discovery and invocation helpers for optional media tools."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Mapping, Sequence


def find_tool(name: str, *, state_dir: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if state_dir is not None:
        candidates.append(state_dir / "bin" / name)
    candidates.extend(
        (
            Path("/opt/homebrew/bin") / name,
            Path("/usr/local/bin") / name,
            Path("/usr/bin") / name,
        )
    )
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return candidate
    return None


def run_tool(
    executable: Path,
    args: Sequence[str | Path],
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [str(executable), *(str(value) for value in args)]
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            argv,
            124,
            stdout=str(exc.stdout or ""),
            stderr=f"timed out after {timeout} seconds",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, stdout="", stderr=str(exc))
