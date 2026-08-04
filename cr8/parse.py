"""Pure filename parsing functions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Mapping, Sequence


_VERSION_RE = re.compile(r"^v([0-7])$", re.IGNORECASE)
_RECORDED_RE = re.compile(r"^.+ \d{4} \[\d{4}-\d{2}-\d{2} \d{6}\]$")
_PROCESSED_RE = re.compile(
    r"^(?:Freeze|Consolidate|Reverse|Crop)(?:\s|$)", re.IGNORECASE
)
_MIXROLES = {
    "novox": "novox",
    "vox": "vox",
    "inst": "inst",
    "instrumental": "inst",
    "bass": "bass",
    "gtar": "gtar",
    "gtr": "gtar",
    "guitar": "gtar",
    "acap": "acap",
    "acapella": "acap",
    "stems": "stems",
}
_TUNING_PREFIXES = ("dropc#", "dropd", "dropc", "dropb", "dadgad")


@dataclass(frozen=True)
class ParsedName:
    date: str | None
    date_source: str
    date_suspect: bool
    title_tokens: list[str]
    key_raw: str | None
    version: int | None
    mixrole: str
    bpm: int | None
    collab: str | None
    tunings: list[str]
    parse_branch: str


def is_project_internal(stem: str, relpath: str | None = None) -> bool:
    normalized_path = (relpath or "").replace("\\", "/").casefold()
    return bool(
        _RECORDED_RE.match(stem)
        or _PROCESSED_RE.match(stem)
        or "/samples/imported/" in f"/{normalized_path}/"
    )


def _mtime_datetime(mtime: float | datetime | date | None) -> datetime:
    if isinstance(mtime, datetime):
        return mtime
    if isinstance(mtime, date):
        return datetime.combine(mtime, datetime.min.time())
    if mtime is None:
        return datetime.now()
    return datetime.fromtimestamp(mtime)


def _valid_filename_date(
    month: int,
    day: int,
    year: int,
    *,
    today: date,
) -> tuple[date | None, bool]:
    suspect = False
    if month > 12 and day <= 12:
        month, day = day, month
        suspect = True
    try:
        value = date(year, month, day)
    except ValueError:
        return None, suspect
    if value < date(2019, 1, 1) or value > today + timedelta(days=2):
        return None, suspect
    return value, suspect


def _year(two_or_four_digits: str) -> int:
    value = int(two_or_four_digits)
    if len(two_or_four_digits) == 4:
        return value
    return 2000 + value


def _date_distance_suspect(value: date, mtime_date: date) -> bool:
    return abs((value - mtime_date).days) > 45


def _repair_date_text(stem: str) -> str:
    repaired = re.sub(r"-{2,}", "-", stem.strip())
    repaired = re.sub(
        r"^(\d{1,2})_(\d{1,2})_(\d{2,4})(?=-|$)",
        lambda match: "-".join(match.groups()),
        repaired,
    )
    repaired = re.sub(
        r"(?<=-)(\d{1,2})_(\d{1,2})_(\d{2,4})(?=-|$)",
        lambda match: "-".join(match.groups()),
        repaired,
    )
    return repaired


def _extract_date(
    stem: str,
    *,
    mtime: datetime,
    today: date,
) -> tuple[str, str, date | None, str, bool]:
    """Return branch, rest, parsed date, source, suspect."""
    repaired = _repair_date_text(stem)
    mtime_date = mtime.date()

    prefix = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{2,4})(?:-(.*))?$", repaired)
    if prefix:
        month, day, year_raw, rest = prefix.groups()
        value, suspect = _valid_filename_date(
            int(month), int(day), _year(year_raw), today=today
        )
        if value is None:
            return "B1", rest or "", mtime_date, "mtime", True
        suspect = suspect or _date_distance_suspect(value, mtime_date)
        return "B1", rest or "", value, "filename", suspect

    glued = re.match(r"^(\d{3,4})-(\d{2})(?:-(.*))?$", repaired)
    if glued:
        glued_month_day, year_raw, rest = glued.groups()
        candidates: list[tuple[date, bool]] = []
        for split in (1, 2):
            if split >= len(glued_month_day):
                continue
            value, suspect = _valid_filename_date(
                int(glued_month_day[:split]),
                int(glued_month_day[split:]),
                _year(year_raw),
                today=today,
            )
            if value is not None:
                candidates.append((value, suspect))
        if len(candidates) == 1:
            value, suspect = candidates[0]
            suspect = suspect or _date_distance_suspect(value, mtime_date)
            return "B1", rest or "", value, "filename", suspect
        return "B4", repaired, mtime_date, "mtime", True

    suffix_matches = list(
        re.finditer(r"(?:^|-)(\d{1,2})-(\d{1,2})-(\d{2,4})(?=-|$)", repaired)
    )
    for match in reversed(suffix_matches):
        if match.start() == 0:
            continue
        month, day, year_raw = match.groups()
        value, suspect = _valid_filename_date(
            int(month), int(day), _year(year_raw), today=today
        )
        before = repaired[: match.start()].rstrip("-")
        after = repaired[match.end() :].lstrip("-")
        rest = "-".join(part for part in (before, after) if part)
        if value is None:
            return "B2", rest, mtime_date, "mtime", True
        suspect = suspect or _date_distance_suspect(value, mtime_date)
        return "B2", rest, value, "filename", suspect

    no_year = re.match(r"^(\d{1,2})-(\d{1,2})(?:-(.*))$", repaired)
    if no_year:
        month, day, rest = no_year.groups()
        year = mtime.year
        if (int(month), int(day)) > (mtime.month, mtime.day):
            year -= 1
        value, suspect = _valid_filename_date(
            int(month), int(day), year, today=today
        )
        if value is None:
            return "B3", rest, mtime_date, "mtime", True
        suspect = suspect or _date_distance_suspect(value, mtime_date)
        return "B3", rest, value, "filename", suspect

    return "B4", repaired, mtime_date, "mtime", False


def _split_title_tokens(rest: str) -> list[str]:
    return [token.strip() for token in re.split(r"[-=]+", rest) if token.strip()]


def parse_name(
    stem: str,
    *,
    mtime: float | datetime | date | None = None,
    keymap: Mapping[str, str] | None = None,
    known_collabs: Sequence[str] = (),
    today: date | None = None,
) -> ParsedName:
    """Parse a filename stem without performing I/O."""
    mtime_dt = _mtime_datetime(mtime)
    today_value = today or date.today()
    branch, rest, parsed_date, date_source, date_suspect = _extract_date(
        stem, mtime=mtime_dt, today=today_value
    )
    tokens = _split_title_tokens(rest)
    spellings = {key.casefold(): value for key, value in (keymap or {}).items()}
    collabs = {value.casefold() for value in known_collabs}

    version: int | None = None
    mixrole = "main"
    bpm: int | None = None
    key_raw: str | None = None
    collab: str | None = None
    tunings: list[str] = []

    kept: list[str] = []
    bpm_edge_open = True
    for token in reversed(tokens):
        lowered = " ".join(token.casefold().split())
        version_match = _VERSION_RE.fullmatch(lowered)
        if version is None and version_match:
            version = int(version_match.group(1))
            continue
        if mixrole == "main" and lowered in _MIXROLES:
            mixrole = _MIXROLES[lowered]
            continue
        if bpm is None and bpm_edge_open and lowered.isdigit() and 60 <= int(lowered) <= 200:
            bpm = int(lowered)
            continue
        if not lowered.isdigit():
            bpm_edge_open = False
        if key_raw is None and lowered in spellings:
            key_raw = lowered
            continue
        if collab is None and lowered in collabs:
            collab = lowered
            continue

        embedded_collab = next(
            (
                value
                for value in sorted(collabs, key=len, reverse=True)
                if lowered.startswith(value) and lowered != value
            ),
            None,
        )
        if collab is None and embedded_collab:
            collab = embedded_collab
            remainder = token[len(embedded_collab) :].strip(" _-")
            if remainder:
                kept.append(remainder)
            continue

        tuning_match = next(
            (prefix for prefix in _TUNING_PREFIXES if lowered.startswith(prefix)), None
        )
        if tuning_match:
            tunings.append(tuning_match)
            remainder = token[len(tuning_match) :].strip(" _-")
            if remainder:
                kept.append(remainder)
            continue
        if lowered.startswith("drop") and re.fullmatch(r"drop[^ ]+", lowered):
            tunings.append(lowered)
            continue

        kept.append(token)

    title_tokens = list(reversed(kept))
    tunings.reverse()
    return ParsedName(
        date=parsed_date.isoformat() if parsed_date is not None else None,
        date_source=date_source,
        date_suspect=date_suspect,
        title_tokens=title_tokens,
        key_raw=key_raw,
        version=version,
        mixrole=mixrole,
        bpm=bpm,
        collab=collab,
        tunings=tunings,
        parse_branch=branch,
    )
