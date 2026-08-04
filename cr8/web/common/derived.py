"""Provenance-safe derived tags and Chromaprint neighbours."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

import acoustid

from ...db import utc_now
from .text import era_for_date


_INSTRUMENT_TOKENS = {
    "bass": {"bass"},
    "drums": {"beat", "beats", "drum", "drums"},
    "guitar": {"gtr", "gtar", "guitar"},
    "hangdrum": {"hang", "hangdrum"},
    "keys": {"key", "keys", "piano", "rhodes"},
    "synth": {"synth", "synths"},
    "vocals": {"acap", "vocal", "vocals", "vox"},
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", value.casefold())
        if token
    }


def derived_values(
    *, source_stem: str, mixrole: str, collab_raw: str | None
) -> list[tuple[str, str, str]]:
    """Return `(dimension, value, provenance)` without subjective guesses."""
    values: list[tuple[str, str, str]] = []
    collab = " ".join(str(collab_raw or "").casefold().split())
    if collab:
        values.append(("collab", collab, "filename"))

    source_tokens = _tokens(source_stem)
    mixrole_tokens = _tokens(mixrole)
    for instrument, aliases in _INSTRUMENT_TOKENS.items():
        if mixrole_tokens & aliases:
            values.append(("instr", instrument, "mixrole"))
        elif source_tokens & aliases:
            values.append(("instr", instrument, "filename"))
    return values


def _bpm_band(value: float) -> str:
    if value < 80:
        return "bpm · under 80"
    if value < 100:
        return "bpm · 80–99"
    if value < 115:
        return "bpm · 100–114"
    if value < 125:
        return "bpm · 115–124"
    if value < 140:
        return "bpm · 125–139"
    return "bpm · 140+"


def _duration_band(value: float) -> str:
    if value < 120:
        return "duration · under 2m"
    if value < 240:
        return "duration · 2–4m"
    if value < 360:
        return "duration · 4–6m"
    return "duration · 6m+"


def _insert_derived(
    connection: Any,
    *,
    song_id: int,
    dim: str,
    value: str,
    source: str,
) -> int:
    connection.execute(
        """
        INSERT INTO song_tags(
          song_id, dim, value, source, author, created_at
        ) VALUES(?, ?, ?, ?, 'catalog', ?)
        ON CONFLICT(song_id, dim, value) DO UPDATE SET
          source=excluded.source,
          author=excluded.author,
          created_at=excluded.created_at
        WHERE song_tags.source!='human'
          AND (
            song_tags.source!=excluded.source
            OR COALESCE(song_tags.author, '')!=excluded.author
          )
        """,
        (song_id, dim, value, source, utc_now()),
    )
    return int(connection.changes())


def backfill_eras(connection: Any) -> int:
    """Materialize named era ranges and assign every song, including undated."""
    eras = (
        ("PELICANA", None, "2023-12-31", "oklch(0.72 0.15 25)"),
        ("NOVA1", "2024-01-01", "2025-12-31", "oklch(0.78 0.13 195)"),
        ("working", "2026-01-01", None, "oklch(0.86 0.16 115)"),
        ("undated", None, None, "rgba(255,255,255,.14)"),
    )
    for era in eras:
        connection.execute(
            """
            INSERT INTO eras(name, date_start, date_end, color)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
              date_start=excluded.date_start,
              date_end=excluded.date_end,
              color=excluded.color
            """,
            era,
        )
    era_ids = {
        str(row["name"]): int(row["id"])
        for row in connection.execute("SELECT id, name FROM eras")
    }
    changed = 0
    for song in connection.execute(
        "SELECT id, first_date, last_date, era_id FROM songs"
    ):
        name, _css = era_for_date(
            str(song["last_date"] or song["first_date"])
            if song["last_date"] or song["first_date"]
            else None
        )
        era_id = era_ids[name]
        if song["era_id"] != era_id:
            connection.execute(
                "UPDATE songs SET era_id=? WHERE id=?",
                (era_id, int(song["id"])),
            )
            changed += int(connection.changes())
    return changed


def backfill_derived_tags(connection: Any) -> int:
    """Populate missing derived tags while preserving every existing row."""
    rows = connection.execute(
        """
        SELECT song_id, source_stem, mixrole, collab_raw
        FROM bounces
        ORDER BY song_id, id
        """
    )
    inserted = 0
    for row in rows:
        for dim, value, source in derived_values(
            source_stem=str(row["source_stem"]),
            mixrole=str(row["mixrole"]),
            collab_raw=(
                str(row["collab_raw"])
                if row["collab_raw"] is not None
                else None
            ),
        ):
            inserted += _insert_derived(
                connection,
                song_id=int(row["song_id"]),
                dim=dim,
                value=value,
                source=source,
            )
    has_files = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='files'
        """
    ).fetchone() is not None
    facts_sql = (
        """
        WITH versions AS (
          SELECT b.song_id, COUNT(DISTINCT b.id) AS version_count,
                 MAX(f.duration_s) AS duration_s
          FROM bounces AS b
          JOIN mirror_files AS mf ON mf.bounce_id=b.id
          LEFT JOIN files AS f
            ON f.bounce_id=b.id AND f.layer='curated'
              AND f.missing_since IS NULL
          GROUP BY b.song_id
        ),
        stemmed AS (
          SELECT DISTINCT b.song_id
          FROM stems AS st
          JOIN bounces AS b ON b.id=st.bounce_id
          WHERE st.mirror_relpath IS NOT NULL
        )
        SELECT s.id AS song_id, s.key_canon, s.key_camelot, s.bpm,
               s.first_date, s.last_date,
               COALESCE(v.version_count, 0) AS version_count,
               v.duration_s,
               stemmed.song_id IS NOT NULL AS has_stems
        FROM songs AS s
        LEFT JOIN versions AS v ON v.song_id=s.id
        LEFT JOIN stemmed ON stemmed.song_id=s.id
        """
        if has_files
        else
        """
        WITH versions AS (
          SELECT b.song_id, COUNT(DISTINCT b.id) AS version_count
          FROM bounces AS b
          JOIN mirror_files AS mf ON mf.bounce_id=b.id
          GROUP BY b.song_id
        )
        SELECT s.id AS song_id, s.key_canon, s.key_camelot, s.bpm,
               s.first_date, s.last_date,
               COALESCE(v.version_count, 0) AS version_count,
               NULL AS duration_s,
               0 AS has_stems
        FROM songs AS s
        LEFT JOIN versions AS v ON v.song_id=s.id
        """
    )
    facts = connection.execute(facts_sql)
    for row in facts:
        song_id = int(row["song_id"])
        derived: list[tuple[str, str]] = []
        if row["key_canon"]:
            derived.append(("derived-key", f"key · {row['key_canon']}"))
        if row["key_camelot"]:
            derived.append(
                ("derived-key", f"camelot · {str(row['key_camelot']).upper()}")
            )
        if row["bpm"] is not None:
            derived.append(("derived-bpm", _bpm_band(float(row["bpm"]))))
        era_name, _css = era_for_date(
            str(row["last_date"] or row["first_date"])
            if row["last_date"] or row["first_date"]
            else None
        )
        derived.append(("derived-era", f"era · {era_name}"))
        if row["duration_s"] is not None:
            derived.append(
                (
                    "derived-duration",
                    _duration_band(float(row["duration_s"])),
                )
            )
        if int(row["version_count"]) > 1:
            derived.append(("derived-version", "versions · multiple"))
        if bool(row["has_stems"]):
            derived.append(("derived-stems", "stems · available"))
        for source, value in derived:
            inserted += _insert_derived(
                connection,
                song_id=song_id,
                dim="use",
                value=value.casefold(),
                source=source,
            )
    return inserted


def _fingerprint_values(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item.strip()]


def _encoded_fingerprint(row: Any) -> tuple[float, bytes]:
    return (
        float(row["duration_s"] or 0),
        acoustid.chromaprint.encode_fingerprint(
            _fingerprint_values(str(row["fingerprint"])),
            1,
        ),
    )


def fingerprint_neighbours(
    connection: Any,
    song_id: int,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return standardized Chromaprint matches, or no guesses without data."""
    rows = list(
        connection.execute(
            """
            SELECT s.id AS song_id, s.public_id AS song_ulid, s.title,
                   f.duration_s, f.fingerprint
            FROM songs AS s
            JOIN bounces AS b ON b.song_id=s.id
            JOIN files AS f ON f.bounce_id=b.id
            WHERE f.fingerprint IS NOT NULL
              AND f.fingerprint!=''
              AND f.missing_since IS NULL
            ORDER BY s.id, f.id DESC
            """
        )
    )
    by_song: dict[int, Any] = {}
    for row in rows:
        by_song.setdefault(int(row["song_id"]), row)
    source = by_song.get(song_id)
    if source is None:
        return []
    try:
        encoded_source = _encoded_fingerprint(source)
    except (TypeError, ValueError):
        return []

    neighbours: list[dict[str, Any]] = []
    for candidate_id, candidate in by_song.items():
        if candidate_id == song_id:
            continue
        try:
            score = float(
                acoustid.compare_fingerprints(
                    encoded_source,
                    _encoded_fingerprint(candidate),
                )
            )
        except (TypeError, ValueError):
            continue
        if score <= 0:
            continue
        neighbours.append(
            {
                "song_id": candidate_id,
                "song_ulid": str(candidate["song_ulid"]),
                "title": str(candidate["title"]),
                "similarity": score,
                "similarity_label": f"{score:.0%}",
            }
        )
    neighbours.sort(
        key=lambda item: (-float(item["similarity"]), str(item["title"]).casefold())
    )
    return neighbours[: min(max(limit, 1), 20)]


def copy_human_tags(
    connection: Any,
    *,
    source_song_id: int,
    target_song_ids: Iterable[int],
) -> int:
    """Copy only confirmed human tags; an existing human row always wins."""
    source_tags = list(
        connection.execute(
            """
            SELECT dim, value FROM song_tags
            WHERE song_id=? AND source='human'
            ORDER BY dim, value
            """,
            (source_song_id,),
        )
    )
    changed = 0
    for target_song_id in dict.fromkeys(target_song_ids):
        if target_song_id == source_song_id:
            continue
        for tag in source_tags:
            connection.execute(
                """
                INSERT INTO song_tags(
                  song_id, dim, value, source, author, created_at
                ) VALUES(?, ?, ?, 'human', 'owner:neighbours', ?)
                ON CONFLICT(song_id, dim, value) DO UPDATE SET
                  source='human',
                  author='owner:neighbours',
                  created_at=excluded.created_at
                WHERE song_tags.source!='human'
                """,
                (
                    target_song_id,
                    str(tag["dim"]),
                    str(tag["value"]),
                    utc_now(),
                ),
            )
            changed += int(connection.changes())
    return changed
