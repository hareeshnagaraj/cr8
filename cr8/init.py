"""Fresh-clone bootstrap: tools, config, dirs, schema, next steps."""

from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Callable, Sequence, TextIO

from .db import connect
from .tooling import find_tool
from .web.common.database import migrate
from .web.common.settings import ensure_secret


HARD_TOOLS = ("ffmpeg", "ffprobe")
OPTIONAL_TOOLS = (
    "audiowaveform",
    "rsgain",
    "keyfinder-cli",
    "aubio",
    "fpcalc",
)


def _ask(prompt: str, *, input_fn: Callable[[str], str], default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input_fn(f"{prompt}{suffix}: ").strip()
    return answer or default


def _yes(prompt: str, *, input_fn: Callable[[str], str], default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input_fn(f"{prompt} [{hint}]: ").strip().casefold()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _toml_string_list(names: Sequence[str]) -> str:
    if not names:
        return "[]"
    inner = ",\n  ".join(f'"{name}"' for name in names)
    return f"[\n  {inner},\n]"


def _seed_curated_dirs(root: Path) -> list[str]:
    names: list[str] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return names
    for entry in entries:
        try:
            if entry.is_dir() and not entry.name.startswith("."):
                names.append(entry.name)
        except OSError:
            continue
    names.sort(key=str.casefold)
    return names


def _write_config(
    example: Path,
    destination: Path,
    *,
    corpus_root: Path,
    curated_dirs: Sequence[str],
) -> None:
    text = example.read_text(encoding="utf-8")
    root_literal = str(corpus_root).replace("\\", "\\\\").replace('"', '\\"')
    text = re.sub(
        r'(?m)^root\s*=\s*".*?"\s*$',
        f'root = "{root_literal}"',
        text,
        count=1,
    )
    text = re.sub(
        r"(?ms)^curated_dirs\s*=\s*\[.*?\]\s*$",
        f"curated_dirs = {_toml_string_list(curated_dirs)}",
        text,
        count=1,
    )
    destination.write_text(text, encoding="utf-8")


def _preflight(out: TextIO) -> bool:
    out.write("Tool preflight:\n")
    ok = True
    for name in HARD_TOOLS:
        path = find_tool(name)
        if path is None:
            out.write(f"  MISSING (required)  {name}\n")
            ok = False
        else:
            out.write(f"  ok                  {name}  ({path})\n")
    for name in OPTIONAL_TOOLS:
        path = find_tool(name)
        if path is None:
            out.write(f"  optional missing    {name}\n")
        else:
            out.write(f"  ok                  {name}  ({path})\n")
    return ok


def run_init(
    *,
    state_dir: Path | None = None,
    config_path: Path | None = None,
    input_fn: Callable[[str], str] | None = None,
    out: TextIO | None = None,
) -> int:
    """Bootstrap a clone so uvicorn + next can reach /setup."""
    input_fn = input_fn or input
    out = out or sys.stdout
    destination = (
        config_path or (state_dir or Path.cwd()) / "config.toml"
    ).expanduser().resolve()
    base = (state_dir or destination.parent).expanduser().resolve()
    example = base / "config.example.toml"
    if not example.is_file():
        out.write(f"cr8 init: missing {example}\n")
        return 2

    if not _preflight(out):
        out.write(
            "\nInstall ffmpeg and ffprobe (Homebrew: brew install ffmpeg), "
            "then re-run cr8 init.\n"
        )
        return 2

    if destination.is_file():
        if not _yes(
            f"{destination.name} already exists — overwrite",
            input_fn=input_fn,
            default=False,
        ):
            out.write("cr8 init: left existing config in place\n")
            return 0

    corpus_raw = _ask(
        "Corpus root (absolute path to your music folder)",
        input_fn=input_fn,
    )
    if not corpus_raw:
        out.write("cr8 init: corpus root is required\n")
        return 2
    corpus_root = Path(corpus_raw).expanduser().resolve()
    if not corpus_root.is_dir():
        out.write(f"cr8 init: not a directory: {corpus_root}\n")
        return 2

    curated: list[str] = []
    if _yes(
        "Seed curated_dirs from top-level subdirectories of the corpus root",
        input_fn=input_fn,
        default=True,
    ):
        curated = _seed_curated_dirs(corpus_root)
        out.write(f"  seeded {len(curated)} curated_dirs\n")

    _write_config(
        example,
        destination,
        corpus_root=corpus_root,
        curated_dirs=curated,
    )
    out.write(f"wrote {destination}\n")

    for relative, mode in (
        ("secrets", 0o700),
        ("logs", 0o755),
        ("mirror", 0o755),
        ("drops", 0o755),
    ):
        path = base / relative
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(mode)
        except OSError:
            pass
        out.write(f"ensured {path}\n")

    secret_path = base / "secrets" / "owner-session.key"
    ensure_secret(secret_path)
    out.write(f"session secret: {secret_path}\n")

    db_path = base / "catalog.db"
    connection = connect(db_path)
    connection.close()
    migrate(db_path)
    out.write(f"database: {db_path}\n")

    out.write(
        "\nNext steps:\n"
        "  1. python -m venv .venv && source .venv/bin/activate\n"
        "     pip install -e '.[dev]'\n"
        "  2. cd web && pnpm install && cd ..\n"
        "  3. ./.venv/bin/uvicorn cr8.web.owner.app:create_app "
        "--factory --host 127.0.0.1 --port 8080\n"
        "  4. (another terminal) cd web && pnpm dev  # :3100\n"
        "  5. open http://127.0.0.1:3100/setup\n"
    )
    return 0
