"""Small presentation and input-safety helpers."""

from __future__ import annotations

from datetime import date
import math
import re
import unicodedata


CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: str, *, limit: int) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return CONTROL.sub("", normalized).strip()[:limit]


def duration_label(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(round(float(seconds))))
    return f"{total // 60}:{total % 60:02d}"


def approximate_minutes(seconds: float | int | None) -> str:
    if not seconds:
        return "0 min"
    return f"~{max(1, math.ceil(float(seconds) / 60))} min"


def era_css(name: str) -> str:
    """CSS token for an eras-table name (undated → unknown)."""
    folded = name.casefold()
    if folded == "undated":
        return "unknown"
    return folded


def era_for_date(value: str | None) -> tuple[str, str]:
    """Seed-time date→era mapping only. Runtime reads eras via songs.era_id."""
    if not value:
        name = "undated"
    else:
        try:
            year = date.fromisoformat(value[:10]).year
        except ValueError:
            name = "undated"
        else:
            if year >= 2026:
                name = "working"
            elif year >= 2024:
                name = "NOVA1"
            else:
                name = "PELICANA"
    return (name, era_css(name))


def display_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return value[:10]
    return parsed.strftime("%b %-d, %Y")


def display_date_range(
    first: str | None,
    last: str | None,
    *,
    today: date | None = None,
) -> str:
    current_year = (today or date.today()).year

    def parse(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    start = parse(first)
    end = parse(last)
    if start is None and end is None:
        return "—"
    if start is None or end is None or start == end:
        value = start or end
        assert value is not None
        suffix = f", {value.year}" if value.year != current_year else ""
        return f"{value.strftime('%b %-d')}{suffix}"
    if start.year != end.year:
        return (
            f"{start.strftime('%b %-d')}, {start.year} – "
            f"{end.strftime('%b %-d')}, {end.year}"
        )
    suffix = f", {end.year}" if end.year != current_year else ""
    return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}{suffix}"


def actor_display(actor: str) -> str:
    base = actor.split(":audit:", 1)[0]
    if base == "owner":
        return "Hareesh"
    if base.startswith("share:"):
        parts = base.split(":", 2)
        if len(parts) == 3:
            return parts[2]
    return base
