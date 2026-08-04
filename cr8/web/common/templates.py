"""Explicitly autoescaped Jinja environments."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .text import actor_display, display_date, display_date_range, duration_label


def make_templates(app_templates: Path) -> Jinja2Templates:
    common = Path(__file__).parent / "templates"
    environment = Environment(
        loader=FileSystemLoader([app_templates, common]),
        autoescape=select_autoescape(("html", "xml"), default=True),
        enable_async=False,
    )
    environment.filters["duration"] = duration_label
    environment.filters["display_date"] = display_date
    environment.filters["display_date_range"] = display_date_range
    environment.filters["actor_display"] = actor_display
    environment.globals["asset_v"] = _asset_version()
    return Jinja2Templates(env=environment)


def _asset_version() -> str:
    """Newest mtime across the static assets, as a cache-busting query value.

    Without this a CSS or JS fix is invisible until the viewer hard-refreshes, so
    a shipped improvement looks like no improvement at all.
    """
    static = Path(__file__).parent / "static"
    try:
        newest = max(path.stat().st_mtime for path in static.rglob("*") if path.is_file())
    except ValueError:
        return "0"
    return str(int(newest))
