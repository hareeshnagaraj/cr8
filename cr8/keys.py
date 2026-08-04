"""Key-spelling normalization and Camelot mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import yaml


_PITCH_FOLD = {
    "db": "c#",
    "eb": "d#",
    "gb": "f#",
    "ab": "g#",
    "bb": "a#",
}

_DISPLAY = {
    "c": "C",
    "c#": "C#",
    "d": "D",
    "d#": "D#",
    "e": "E",
    "f": "F",
    "f#": "F#",
    "g": "G",
    "g#": "G#",
    "a": "A",
    "a#": "A#",
    "b": "B",
}

_CAMELOT_MINOR = {
    "g#": "1A",
    "d#": "2A",
    "a#": "3A",
    "f": "4A",
    "c": "5A",
    "g": "6A",
    "d": "7A",
    "a": "8A",
    "e": "9A",
    "b": "10A",
    "f#": "11A",
    "c#": "12A",
}

_CAMELOT_MAJOR = {
    "b": "1B",
    "f#": "2B",
    "c#": "3B",
    "g#": "4B",
    "d#": "5B",
    "a#": "6B",
    "f": "7B",
    "c": "8B",
    "g": "9B",
    "d": "10B",
    "a": "11B",
    "e": "12B",
}


def _clean(raw: str) -> str:
    return " ".join(raw.strip().casefold().split())


def load_keymap(path: str | Path) -> dict[str, str]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("spellings"), dict):
        raise ValueError("keymap.yaml must contain a spellings mapping")
    result: dict[str, str] = {}
    for spelling, canonical in payload["spellings"].items():
        if not isinstance(spelling, str) or not isinstance(canonical, str):
            raise ValueError("keymap spellings and values must be strings")
        result[_clean(spelling)] = canonical
    return result


def default_spellings() -> dict[str, str]:
    """Return the complete required spelling map without doing I/O."""
    result: dict[str, str] = {}
    for raw_pitch in (
        "c",
        "c#",
        "db",
        "d",
        "d#",
        "eb",
        "e",
        "f",
        "f#",
        "gb",
        "g",
        "g#",
        "ab",
        "a",
        "a#",
        "bb",
        "b",
    ):
        pitch = _PITCH_FOLD.get(raw_pitch, raw_pitch)
        display = _DISPLAY[pitch]
        minor = f"{display} minor"
        major = f"{display} major"
        for suffix in ("m", " m", "min", "minor"):
            result[f"{raw_pitch}{suffix}"] = minor
        for suffix in ("maj", "major"):
            result[f"{raw_pitch}{suffix}"] = major
    return result


def normalize(
    key_raw: str | None, keymap: Mapping[str, str] | None = None
) -> tuple[str | None, str | None]:
    """Normalize a supported key spelling to canonical name and Camelot code."""
    if key_raw is None:
        return None, None
    cleaned = _clean(key_raw)
    if cleaned == "none":
        return "none", None
    spellings = keymap if keymap is not None else default_spellings()
    canonical = spellings.get(cleaned)
    if canonical is None:
        canonical = next(
            (
                value
                for value in set(spellings.values())
                if value.casefold() == cleaned
            ),
            None,
        )
    if canonical is None:
        return None, None
    pitch_display, mode = canonical.rsplit(" ", 1)
    pitch = pitch_display.casefold()
    camelot = (_CAMELOT_MINOR if mode == "minor" else _CAMELOT_MAJOR)[pitch]
    return canonical, camelot


def canonical_pitch_mode(canonical: str | None) -> str | None:
    """Return a comparison key for conflict detection."""
    if not canonical or canonical == "none":
        return canonical
    pitch_display, mode = canonical.rsplit(" ", 1)
    pitch = _PITCH_FOLD.get(pitch_display.casefold(), pitch_display.casefold())
    return f"{pitch}:{mode}"


def from_camelot(value: str | None) -> tuple[str | None, str | None]:
    """Convert a Camelot code into the canonical key representation."""
    if value is None:
        return None, None
    cleaned = value.strip().upper()
    inverse = {
        code: f"{_DISPLAY[pitch]} {mode}"
        for mode, mapping in (("minor", _CAMELOT_MINOR), ("major", _CAMELOT_MAJOR))
        for pitch, code in mapping.items()
    }
    canonical = inverse.get(cleaned)
    return (canonical, cleaned) if canonical else (None, None)
