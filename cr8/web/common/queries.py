"""Read projections for the authenticated catalog application."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import json
from pathlib import Path
import random
from typing import Any

import apsw
from mutagen import MutagenError
from mutagen.id3 import ID3

from .database import Row, fetch_all, fetch_one, reading
from .derived import fingerprint_neighbours
from .settings import AppSettings
from .text import display_date, duration_label, era_css


class SearchError(ValueError):
    """A generic owner search failure that does not expose SQLite details."""


# Below this, a trigram index has nothing to match on and the LIKE path runs.
TRIGRAM_MINIMUM = 3


def quoted_fts_literal(value: str, *, maximum: int = 120) -> str:
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise SearchError("search is too long")
    if not cleaned:
        return ""
    escaped = cleaned.replace('"', '""')
    return f'"{escaped}"'


TRACK_SQL = """
SELECT b.id AS bounce_id, b.public_id AS bounce_ulid,
       b.song_id, b.source_stem, b.bounce_date, b.version, b.mixrole, b.collab_raw,
       s.public_id AS song_ulid, s.slug, s.title, s.status, s.keeper,
       s.key_canon, s.key_camelot, s.bpm, s.energy,
       s.first_date, s.last_date, s.notes, s.released_url,
       e.name AS era_name, e.color AS era_color,
       mf.mirror_relpath,
       (SELECT COUNT(*) FROM bounces bx
        JOIN mirror_files mx ON mx.bounce_id=bx.id
        WHERE bx.song_id=s.id) AS version_count,
       MAX(f.duration_s) AS duration_s
FROM bounces AS b
JOIN songs AS s ON s.id=b.song_id
JOIN mirror_files AS mf ON mf.bounce_id=b.id
LEFT JOIN files AS f
  ON f.bounce_id=b.id AND f.layer='curated' AND f.missing_since IS NULL
LEFT JOIN eras AS e ON e.id=s.era_id
WHERE b.public_id=?
GROUP BY b.id
"""


STEM_TRACK_SQL = """
SELECT b.id AS bounce_id, st.public_id AS bounce_ulid,
       b.song_id, b.source_stem, b.bounce_date, b.version, st.kind AS mixrole, b.collab_raw,
       s.public_id AS song_ulid, s.slug, s.title, s.status, s.keeper,
       s.key_canon, s.key_camelot, s.bpm, s.energy,
       s.first_date, s.last_date, s.notes, s.released_url,
       e.name AS era_name, e.color AS era_color,
       st.mirror_relpath,
       (SELECT COUNT(*) FROM bounces bx
        JOIN mirror_files mx ON mx.bounce_id=bx.id
        WHERE bx.song_id=s.id) AS version_count,
       st.duration_s,
       st.kind AS stem_kind,
       sr.recipe AS stem_recipe,
       b.public_id AS parent_bounce_ulid,
       CASE
         WHEN sf.sha256 IS NOT NULL AND sf.sha256 != sr.src_sha256 THEN 1
         ELSE 0
       END AS stem_stale
FROM stems AS st
JOIN stem_runs AS sr ON sr.id=st.run_id AND sr.ok=1
JOIN bounces AS b ON b.id=st.bounce_id
JOIN songs AS s ON s.id=b.song_id
LEFT JOIN eras AS e ON e.id=s.era_id
LEFT JOIN files AS sf ON sf.bounce_id=b.id AND sf.relpath=sr.src_relpath
WHERE st.public_id=? AND st.mirror_relpath IS NOT NULL
"""


def _decorate_track(row: Row) -> dict[str, Any]:
    item = dict(row)
    name = str(row["era_name"] or "undated")
    item["era"] = name
    item["era_css"] = era_css(name)
    item["duration_label"] = duration_label(row["duration_s"])
    item["date_label"] = display_date(row["bounce_date"] or row["last_date"])
    item["version_label"] = (
        str(row["stem_kind"])
        if row.get("stem_kind")
        else (f"v{int(row['version'])}" if row["version"] is not None else "mix")
    )
    return item


def track_by_ulid(settings: AppSettings, bounce_ulid: str) -> dict[str, Any] | None:
    with reading(settings.db_path) as connection:
        row = fetch_one(connection, TRACK_SQL, (bounce_ulid,))
        if row is None:
            row = fetch_one(connection, STEM_TRACK_SQL, (bounce_ulid,))
    return _decorate_track(row) if row is not None else None


def stems_for_bounce(
    settings: AppSettings, bounce_ulid: str
) -> list[dict[str, Any]]:
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            STEM_TRACK_SQL.replace(
                "WHERE st.public_id=? AND st.mirror_relpath IS NOT NULL",
                "WHERE b.public_id=? AND st.mirror_relpath IS NOT NULL",
            )
            + """
            ORDER BY CASE sr.recipe WHEN 'default-v1' THEN 0 ELSE 1 END,
                     CASE st.kind
                       WHEN 'vocals' THEN 0 WHEN 'instrumental' THEN 1
                       WHEN 'drums' THEN 2 WHEN 'bass' THEN 3 ELSE 4 END
            """,
            (bounce_ulid,),
        )
    return [_decorate_track(row) for row in rows]


def stems_for_scope(
    settings: AppSettings,
    scope: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    if not scope:
        return []
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            STEM_TRACK_SQL.replace(
                "WHERE st.public_id=? AND st.mirror_relpath IS NOT NULL",
                "WHERE b.public_id IN ({}) AND st.mirror_relpath IS NOT NULL".format(
                    ",".join("?" for _ in scope)
                ),
            )
            + """
            ORDER BY b.id,
                     CASE sr.recipe WHEN 'default-v1' THEN 0 ELSE 1 END,
                     CASE st.kind
                       WHEN 'vocals' THEN 0 WHEN 'instrumental' THEN 1
                       WHEN 'drums' THEN 2 WHEN 'bass' THEN 3 ELSE 4 END
            """,
            tuple(scope),
        )
    return [_decorate_track(row) for row in rows]


def stem_job_for_bounce(settings: AppSettings, bounce_ulid: str) -> Row | None:
    with reading(settings.db_path) as connection:
        return fetch_one(
            connection,
            """
            SELECT j.* FROM jobs AS j
            JOIN bounces AS b ON b.id=j.target_id
            WHERE j.kind='stems' AND b.public_id=?
            ORDER BY
              CASE j.state
                WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2
              END,
              j.id DESC
            LIMIT 1
            """,
            (bounce_ulid,),
        )


def prioritized_dig(
    settings: AppSettings,
    tracks: list[dict[str, Any]],
    *,
    share_id: int,
    actor: str,
    randomizer: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Prefer untagged discovery, then never and least-recently played."""
    items = [dict(track) for track in tracks]
    bounce_ulids = list(
        dict.fromkeys(str(track["bounce_ulid"]) for track in items)
    )
    if not bounce_ulids:
        return []
    song_ids = list(dict.fromkeys(int(track["song_id"]) for track in items))
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT bounce_ulid, COUNT(*) AS play_count,
                   MAX(started_at) AS last_played_at
            FROM playback_events
            WHERE share_id=? AND actor=?
              AND bounce_ulid IN ({})
            GROUP BY bounce_ulid
            """.format(",".join("?" for _ in bounce_ulids)),
            (share_id, actor, *bounce_ulids),
        )
        tag_rows = fetch_all(
            connection,
            """
            SELECT DISTINCT song_id
            FROM song_tags
            WHERE dim='vibe' AND song_id IN ({})
            UNION
            SELECT DISTINCT song_id
            FROM reactions
            WHERE kind='chip' AND dim='vibe' AND deleted_at IS NULL
              AND actor NOT LIKE '%:audit:%'
              AND song_id IN ({})
            """.format(
                ",".join("?" for _ in song_ids),
                ",".join("?" for _ in song_ids),
            ),
            (*song_ids, *song_ids),
        )
    stats = {str(row["bounce_ulid"]): row for row in rows}
    tagged_song_ids = {int(row["song_id"]) for row in tag_rows}
    for item in items:
        row = stats.get(str(item["bounce_ulid"]))
        item["play_count"] = int(row["play_count"]) if row else 0
        item["last_played_at"] = (
            str(row["last_played_at"]) if row and row["last_played_at"] else ""
        )
        item["untagged"] = int(item["song_id"]) not in tagged_song_ids
        if item["untagged"] and row is None:
            item["reason"] = "never played · no vibe yet"
        elif item["untagged"]:
            item["reason"] = "no vibe yet"
        elif row is None:
            item["reason"] = "never played"
        else:
            item["reason"] = (
                f"not since {display_date(str(row['last_played_at']))}"
            )
        if item["untagged"]:
            item["dig_reason"] = "untagged"
            item["dig_reason_label"] = "NO VIBE YET"
        elif row is None:
            item["dig_reason"] = "never_played"
            item["dig_reason_label"] = "NEVER PLAYED"
        else:
            try:
                last_heard = date.fromisoformat(
                    str(row["last_played_at"])[:10]
                ).strftime("%b %Y")
            except ValueError:
                last_heard = display_date(str(row["last_played_at"]))
            item["dig_reason"] = "dormant"
            item["dig_reason_label"] = f"LAST HEARD {last_heard.upper()}"
    (randomizer or random.SystemRandom()).shuffle(items)
    items.sort(
        key=lambda item: (
            (
                0
                if item["untagged"] and int(item["play_count"]) == 0
                else 1
                if item["untagged"]
                else 2
                if int(item["play_count"]) == 0
                else 3
            ),
            str(item["last_played_at"]),
            int(item["play_count"]),
        )
    )
    return items


def dig_summary(
    settings: AppSettings, *, showing: int
) -> dict[str, int]:
    """Return crate-wide Dig counts without changing the active filters."""
    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection,
            """
            WITH library AS (
              SELECT s.id AS song_id
              FROM songs AS s
              WHERE s.status!='released'
                AND EXISTS(
                  SELECT 1
                  FROM bounces AS b
                  JOIN mirror_files AS mf ON mf.bounce_id=b.id
                  WHERE b.song_id=s.id
                )
            ),
            played_songs AS (
              SELECT DISTINCT b.song_id
              FROM bounces AS b
              JOIN library AS l ON l.song_id=b.song_id
              JOIN playback_events AS pe ON pe.bounce_ulid=b.public_id
              WHERE pe.share_id=0
            ),
            first_plays AS (
              SELECT bounce_ulid, MIN(started_at) AS first_started_at
              FROM playback_events
              WHERE share_id=0
              GROUP BY bounce_ulid
            ),
            dug_today AS (
              SELECT DISTINCT fp.bounce_ulid
              FROM first_plays AS fp
              JOIN bounces AS b ON b.public_id=fp.bounce_ulid
              JOIN library AS l ON l.song_id=b.song_id
              WHERE DATE(fp.first_started_at)=DATE('now')
            )
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(
                     CASE WHEN ps.song_id IS NULL THEN 1 ELSE 0 END
                   ), 0) AS never_played,
                   (SELECT COUNT(*) FROM dug_today) AS dug_today
            FROM library AS l
            LEFT JOIN played_songs AS ps ON ps.song_id=l.song_id
            """,
        )
    return {
        "total": int(row["total"] if row else 0),
        "never_played": int(row["never_played"] if row else 0),
        "showing": max(0, int(showing)),
        "dug_today": int(row["dug_today"] if row else 0),
    }


def untagged_dimension_counts(
    settings: AppSettings,
    tracks: list[dict[str, Any]] | None = None,
    dimensions: tuple[str, ...] = ("vibe", "instr", "collab", "use"),
) -> dict[str, int]:
    if tracks is None:
        with reading(settings.db_path) as connection:
            total_row = fetch_one(
                connection,
                f"""
                WITH {LIBRARY_RANKED_SCOPE_SQL}
                SELECT COUNT(*) AS count
                {LIBRARY_SONG_SCOPE_SQL}
                WHERE s.status!='released'
                """,
            )
        total = int(total_row["count"] if total_row else 0)
        result = {dim: total for dim in dimensions}
        dim_placeholders = ",".join("?" for _ in dimensions)
        with reading(settings.db_path) as connection:
            rows = fetch_all(
                connection,
                f"""
                WITH {LIBRARY_RANKED_SCOPE_SQL},
                scoped AS (
                  SELECT s.id AS song_id
                  {LIBRARY_SONG_SCOPE_SQL}
                  WHERE s.status!='released'
                ),
                tags AS (
                  SELECT st.song_id, st.dim
                  FROM song_tags AS st
                  JOIN scoped AS sc ON sc.song_id=st.song_id
                  UNION
                  SELECT rx.song_id, rx.dim
                  FROM reactions AS rx
                  JOIN scoped AS sc ON sc.song_id=rx.song_id
                  WHERE rx.kind='chip' AND rx.deleted_at IS NULL
                    AND rx.actor NOT LIKE '%:audit:%'
                )
                SELECT dim, COUNT(DISTINCT song_id) AS tagged
                FROM tags
                WHERE dim IN ({dim_placeholders})
                GROUP BY dim
                """,
                dimensions,
            )
        for row in rows:
            result[str(row["dim"])] -= int(row["tagged"])
        return result
    song_ids = list(dict.fromkeys(int(track["song_id"]) for track in tracks))
    result = {dim: len(song_ids) for dim in dimensions}
    if not song_ids:
        return result
    placeholders = ",".join("?" for _ in song_ids)
    dim_placeholders = ",".join("?" for _ in dimensions)
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            f"""
            WITH tags AS (
              SELECT song_id, dim FROM song_tags
              WHERE song_id IN ({placeholders})
              UNION
              SELECT song_id, dim FROM reactions
              WHERE kind='chip' AND deleted_at IS NULL
                AND actor NOT LIKE '%:audit:%'
                AND song_id IN ({placeholders})
            )
            SELECT dim, COUNT(DISTINCT song_id) AS tagged
            FROM tags
            WHERE dim IN ({dim_placeholders})
            GROUP BY dim
            """,
            (*song_ids, *song_ids, *dimensions),
        )
    for row in rows:
        result[str(row["dim"])] -= int(row["tagged"])
    return result


FACET_STATUS_VALUES = ("idea", "jam", "demo", "mixed", "finished")
MAX_LIBRARY_RESULTS = 10_000


@dataclass(frozen=True)
class LibraryFilter:
    query: str = ""
    status: str | None = None
    era: str | None = None
    key_value: str | None = None
    dim: str | None = None
    value: str | None = None
    tag_values: Mapping[str, Sequence[str]] = field(default_factory=dict)
    untagged_dims: Sequence[str] = ()
    unheard: bool = False
    hearted: bool = False
    keeper_min: int | None = None
    random_seed: str = ""
    skip_short_sketches: bool = False
    untagged_vibe: bool = False
    bpm_min: float | None = None
    bpm_max: float | None = None
    song_ulids: Sequence[str] = ()
    include_released: bool = False


@dataclass(frozen=True)
class LibraryFacetCounts:
    statuses: Mapping[str, int]
    eras: Mapping[str, int]
    keys: Mapping[str, int]
    canonical_keys: Mapping[str, int]
    hearted: int
    unheard: int


LIBRARY_RANKED_SCOPE_SQL = """
ranked AS (
  SELECT b.id AS bounce_id, b.public_id AS bounce_ulid, b.song_id,
         b.source_stem, b.bounce_date, b.version, b.mixrole,
         ROW_NUMBER() OVER (
           PARTITION BY b.song_id
           ORDER BY COALESCE(b.bounce_date, '') DESC,
                    COALESCE(b.version, 0) DESC, b.id DESC
         ) AS newest
  FROM bounces AS b
  JOIN mirror_files AS mf ON mf.bounce_id=b.id
)
"""


LIBRARY_SONG_SCOPE_SQL = """
FROM songs AS s
JOIN ranked AS r ON r.song_id=s.id AND r.newest=1
"""


LIBRARY_SQL = f"""
WITH durations AS (
  SELECT b.id AS bounce_id, MAX(f.duration_s) AS duration_s
  FROM bounces AS b
  LEFT JOIN files AS f
    ON f.bounce_id=b.id AND f.layer='curated' AND f.missing_since IS NULL
  GROUP BY b.id
),
ears AS (
  SELECT b.song_id, COUNT(DISTINCT pe.actor) AS ears
  FROM playback_events AS pe
  JOIN bounces AS b ON b.public_id=pe.bounce_ulid
  WHERE pe.share_id=0
  GROUP BY b.song_id
),
{LIBRARY_RANKED_SCOPE_SQL}
SELECT s.id AS song_id, s.public_id AS song_ulid, s.slug, s.title,
       s.status, s.keeper, s.key_canon, s.key_camelot, s.bpm,
       s.energy, s.first_date, s.last_date, s.released_url,
       er.name AS era_name, er.color AS era_color,
       r.bounce_id, r.bounce_ulid, r.bounce_date, r.version,
       r.source_stem, r.mixrole, d.duration_s,
       (SELECT COUNT(*) FROM bounces bx
        JOIN mirror_files mx ON mx.bounce_id=bx.id
        WHERE bx.song_id=s.id) AS version_count,
       COALESCE(e.ears, 0) AS ears,
       NOT EXISTS(
         SELECT 1 FROM listen_progress lp
         WHERE lp.share_id=0 AND lp.bounce_ulid=r.bounce_ulid
           AND lp.actor=? AND lp.state='heard'
       ) AS unheard
{LIBRARY_SONG_SCOPE_SQL}
LEFT JOIN durations AS d ON d.bounce_id=r.bounce_id
LEFT JOIN ears AS e ON e.song_id=s.id
LEFT JOIN eras AS er ON er.id=s.era_id
WHERE {{where}}
ORDER BY {{order_by}}
{{limit_clause}}
"""


LIBRARY_ORDER_BY = {
    "newest": (
        "COALESCE(r.bounce_date, s.last_date, s.first_date, '') DESC, "
        "s.title COLLATE NOCASE DESC, s.id DESC"
    ),
    "oldest": (
        "COALESCE(r.bounce_date, s.last_date, s.first_date, '') ASC, "
        "s.title COLLATE NOCASE ASC, s.id ASC"
    ),
    "longest": "COALESCE(d.duration_s, 0) DESC, s.title COLLATE NOCASE ASC",
    "shortest": "COALESCE(d.duration_s, 0) ASC, s.title COLLATE NOCASE ASC",
    "title": "s.title COLLATE NOCASE ASC, s.id ASC",
    "title-desc": "s.title COLLATE NOCASE DESC, s.id DESC",
    "era": "er.name COLLATE NOCASE ASC, s.title COLLATE NOCASE ASC",
    "era-desc": "er.name COLLATE NOCASE DESC, s.title COLLATE NOCASE DESC",
    "key": (
        "COALESCE(NULLIF(s.key_canon, ''), NULLIF(s.key_camelot, ''), "
        "'zzzz') COLLATE NOCASE ASC, s.title COLLATE NOCASE ASC"
    ),
    "key-desc": (
        "COALESCE(NULLIF(s.key_canon, ''), NULLIF(s.key_camelot, ''), "
        "'zzzz') COLLATE NOCASE DESC, s.title COLLATE NOCASE DESC"
    ),
    "bpm": "s.bpm IS NULL ASC, s.bpm ASC, s.title COLLATE NOCASE ASC",
    "bpm-desc": "s.bpm IS NULL ASC, s.bpm DESC, s.title COLLATE NOCASE ASC",
    "versions": "version_count ASC, s.title COLLATE NOCASE ASC",
    "versions-desc": "version_count DESC, s.title COLLATE NOCASE ASC",
    "ears": "ears ASC, s.title COLLATE NOCASE ASC",
    "ears-desc": "ears DESC, s.title COLLATE NOCASE ASC",
    "keeper": "s.keeper ASC, s.title COLLATE NOCASE ASC",
    "keeper-desc": "s.keeper DESC, s.title COLLATE NOCASE ASC",
}


STATUS_COUNTS_SQL = f"""
WITH {LIBRARY_RANKED_SCOPE_SQL}
SELECT s.status AS value, MIN(COUNT(*), ?) AS count
{LIBRARY_SONG_SCOPE_SQL}
WHERE s.status IN ({",".join("?" for _ in FACET_STATUS_VALUES)})
GROUP BY s.status
"""


LIBRARY_FACET_COUNTS_SQL = f"""
WITH {LIBRARY_RANKED_SCOPE_SQL},
scope AS (
  SELECT s.id AS song_id, s.status, s.key_canon, s.key_camelot,
         er.name AS era_name,
         EXISTS(
           SELECT 1 FROM reactions AS rx
           WHERE rx.actor=? AND rx.kind='heart' AND rx.deleted_at IS NULL
             AND rx.bounce_ulid=r.bounce_ulid
         ) AS hearted,
         NOT EXISTS(
           SELECT 1 FROM listen_progress AS lp
           WHERE lp.share_id=0 AND lp.bounce_ulid=r.bounce_ulid
             AND lp.actor=? AND lp.state='heard'
         ) AS unheard
  {LIBRARY_SONG_SCOPE_SQL}
  LEFT JOIN eras AS er ON er.id=s.era_id
),
key_values AS (
  SELECT key_canon AS value FROM scope
  WHERE status!='released' AND key_canon IS NOT NULL AND key_canon!=''
  UNION ALL
  SELECT key_camelot AS value FROM scope
  WHERE status!='released' AND key_camelot IS NOT NULL AND key_camelot!=''
)
SELECT 'status' AS dimension, status AS value,
       MIN(COUNT(*), ?) AS count
FROM scope
GROUP BY status
UNION ALL
SELECT 'era', era_name, COUNT(*)
FROM scope
WHERE status!='released' AND era_name IS NOT NULL
GROUP BY era_name
UNION ALL
SELECT 'key', value, COUNT(*)
FROM key_values
GROUP BY value
UNION ALL
SELECT 'key_canon', key_canon, COUNT(*)
FROM scope
WHERE status!='released' AND key_canon IS NOT NULL AND key_canon!=''
GROUP BY key_canon
UNION ALL
SELECT 'hearted', '', COALESCE(SUM(hearted), 0)
FROM scope
WHERE status!='released'
UNION ALL
SELECT 'unheard', '', COALESCE(SUM(unheard), 0)
FROM scope
WHERE status!='released'
"""


def library_songs(
    settings: AppSettings,
    filt: LibraryFilter,
    *,
    actor: str = "owner",
    sort: str = "newest",
    include_vibe_tags: bool = False,
    limit: int = 10_000,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    parameters: list[Any] = [actor]
    text = filt.query.strip()
    if text:
        # Trigram indexes store three-character runs, so anything shorter has
        # nothing to match and would return an empty library mid-keystroke.
        # Every search passes through those first two characters.
        if len(text) < TRIGRAM_MINIMUM:
            # Escape rather than strip: someone typing "%" is looking for a
            # literal percent sign, and stripping it would leave "%%", which
            # matches the entire catalogue.
            escaped = (
                text.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            like = f"%{escaped}%"
            clauses.append(
                "(s.title LIKE ? ESCAPE '\\' OR s.slug LIKE ? ESCAPE '\\')"
            )
            parameters.extend((like, like))
        else:
            clauses.append(
                "s.id IN (SELECT rowid FROM songs_search "
                "WHERE songs_search MATCH ?)"
            )
            parameters.append(quoted_fts_literal(text))
    if filt.bpm_min is not None:
        clauses.append("s.bpm IS NOT NULL AND s.bpm >= ?")
        parameters.append(float(filt.bpm_min))
    if filt.bpm_max is not None:
        clauses.append("s.bpm IS NOT NULL AND s.bpm <= ?")
        parameters.append(float(filt.bpm_max))
    if filt.keeper_min is not None:
        clauses.append("s.keeper >= ?")
        parameters.append(int(filt.keeper_min))
    if filt.status:
        clauses.append("s.status=?")
        parameters.append(filt.status)
    elif not filt.include_released:
        clauses.append("s.status!='released'")
    if filt.song_ulids:
        song_ulids = list(dict.fromkeys(str(item) for item in filt.song_ulids))
        clauses.append(
            f"s.public_id IN ({','.join('?' for _ in song_ulids)})"
        )
        parameters.extend(song_ulids)
    if filt.era:
        era_name = "undated" if filt.era.casefold() == "unknown" else filt.era
        clauses.append("LOWER(er.name)=?")
        parameters.append(era_name.casefold())
    if filt.key_value:
        clauses.append("(LOWER(s.key_canon)=? OR LOWER(s.key_camelot)=?)")
        key_value = filt.key_value.casefold()
        parameters.extend((key_value, key_value))
    if filt.skip_short_sketches:
        clauses.append(
            "NOT (COALESCE(d.duration_s, 0) < 90 AND "
            "(LOWER(s.status) IN ('idea','jam') "
            "OR LOWER(COALESCE(r.source_stem, '')) LIKE '%sketch%'))"
        )
    selected_tags = {
        name: list(dict.fromkeys(values))
        for name, values in filt.tag_values.items()
        if name in {"vibe", "instr", "collab", "use"} and values
    }
    if filt.dim in {"vibe", "instr", "collab", "use"} and filt.value:
        selected_tags.setdefault(filt.dim, [])
        if filt.value not in selected_tags[filt.dim]:
            selected_tags[filt.dim].append(filt.value)
    for tag_dim, values in selected_tags.items():
        placeholders = ",".join("?" for _ in values)
        clauses.append(
            "(EXISTS(SELECT 1 FROM song_tags st "
            f"WHERE st.song_id=s.id AND st.dim=? AND st.value IN ({placeholders})) "
            "OR EXISTS(SELECT 1 FROM reactions rx "
            "WHERE rx.song_id=s.id AND rx.kind='chip' "
            "AND rx.deleted_at IS NULL "
            "AND rx.actor NOT LIKE '%:audit:%' "
            f"AND rx.dim=? AND rx.value IN ({placeholders})))"
        )
        parameters.extend((tag_dim, *values, tag_dim, *values))
    selected_untagged = {
        name
        for name in filt.untagged_dims
        if name in {"vibe", "instr", "collab", "use"}
    }
    if filt.untagged_vibe:
        selected_untagged.add("vibe")
    for tag_dim in selected_untagged:
        clauses.append(
            "NOT EXISTS(SELECT 1 FROM song_tags st "
            "WHERE st.song_id=s.id AND st.dim=?) "
            "AND NOT EXISTS(SELECT 1 FROM reactions rx "
            "WHERE rx.song_id=s.id AND rx.kind='chip' "
            "AND rx.dim=? AND rx.deleted_at IS NULL "
            "AND rx.actor NOT LIKE '%:audit:%')"
        )
        parameters.extend((tag_dim, tag_dim))
    if filt.unheard:
        clauses.append(
            "NOT EXISTS(SELECT 1 FROM listen_progress lp "
            "WHERE lp.share_id=0 AND lp.bounce_ulid=r.bounce_ulid "
            "AND lp.actor=? AND lp.state='heard')"
        )
        parameters.append(actor)
    if filt.hearted:
        clauses.append(
            "EXISTS(SELECT 1 FROM reactions rx "
            "WHERE rx.actor=? AND rx.kind='heart' AND rx.deleted_at IS NULL "
            "AND rx.bounce_ulid=r.bounce_ulid)"
        )
        parameters.append(actor)
    bounded_limit = min(max(limit, 1), MAX_LIBRARY_RESULTS)
    random_sort = sort == "random"
    if not random_sort:
        parameters.append(bounded_limit)
    try:
        with reading(settings.db_path) as connection:
            rows = fetch_all(
                connection,
                LIBRARY_SQL.format(
                    where=" AND ".join(clauses),
                    order_by=LIBRARY_ORDER_BY.get(
                        sort, LIBRARY_ORDER_BY["newest"]
                    ),
                    limit_clause="" if random_sort else "LIMIT ?",
                ),
                tuple(parameters),
            )
    except apsw.Error as exc:
        raise SearchError("search could not be completed") from exc
    items = [_decorate_track(row) for row in rows]
    if random_sort:
        random.Random(filt.random_seed or "cr8").shuffle(items)
        items = items[:bounded_limit]
    if items:
        song_ids = [int(item["song_id"]) for item in items]
        placeholders = ",".join("?" for _ in song_ids)
        with reading(settings.db_path) as connection:
            vibe_rows = (
                fetch_all(
                    connection,
                    """
                    WITH visible_vibes AS (
                      SELECT song_id, value
                      FROM song_tags
                      WHERE dim='vibe' AND song_id IN ({0})
                      UNION
                      SELECT song_id, value
                      FROM reactions
                      WHERE kind='chip' AND dim='vibe' AND deleted_at IS NULL
                        AND actor NOT LIKE '%:audit:%'
                        AND song_id IN ({0})
                    )
                    SELECT song_id, GROUP_CONCAT(value, CHAR(31)) AS values_csv
                    FROM (
                      SELECT song_id, value
                      FROM visible_vibes
                      ORDER BY song_id, value COLLATE NOCASE
                    )
                    GROUP BY song_id
                    """.format(placeholders),
                    (*song_ids, *song_ids),
                )
                if include_vibe_tags
                else []
            )
            version_rows = fetch_all(
                connection,
                """
                SELECT b.song_id, b.public_id AS bounce_ulid,
                       b.bounce_date, b.version, b.mixrole,
                       MAX(f.duration_s) AS duration_s
                FROM bounces AS b
                JOIN mirror_files AS mf ON mf.bounce_id=b.id
                LEFT JOIN files AS f
                  ON f.bounce_id=b.id AND f.layer='curated'
                    AND f.missing_since IS NULL
                WHERE b.song_id IN ({})
                GROUP BY b.id
                ORDER BY b.song_id, COALESCE(b.bounce_date,'') DESC,
                         COALESCE(b.version,0) DESC, b.id DESC
                """.format(placeholders),
                tuple(song_ids),
            )
        vibes_by_song = {
            int(row["song_id"]): str(row["values_csv"] or "").split("\x1f")
            for row in vibe_rows
        }
        grouped: dict[int, list[dict[str, Any]]] = {}
        for version in version_rows:
            grouped.setdefault(int(version["song_id"]), []).append(
                {
                    **dict(version),
                    "version_label": (
                        f"v{int(version['version'])}"
                        if version["version"] is not None
                        else "mix"
                    ),
                    "date_label": display_date(
                        str(version["bounce_date"])
                        if version["bounce_date"]
                        else None
                    ),
                    "duration_label": duration_label(version["duration_s"]),
                }
            )
        for item in items:
            if include_vibe_tags:
                item["vibe_tags"] = vibes_by_song.get(int(item["song_id"]), [])
            item["versions"] = grouped.get(int(item["song_id"]), [])
    return items


def library_facet_counts(
    settings: AppSettings, *, actor: str = "owner"
) -> LibraryFacetCounts:
    values: dict[str, dict[str, int]] = {
        "status": {},
        "era": {},
        "key": {},
        "key_canon": {},
    }
    scalars = {"hearted": 0, "unheard": 0}
    try:
        with reading(settings.db_path) as connection:
            rows = fetch_all(
                connection,
                LIBRARY_FACET_COUNTS_SQL,
                (actor, actor, MAX_LIBRARY_RESULTS),
            )
    except apsw.Error as exc:
        raise SearchError("search could not be completed") from exc
    for row in rows:
        dimension = str(row["dimension"])
        count = int(row["count"] or 0)
        if dimension in scalars:
            scalars[dimension] = count
        else:
            values[dimension][str(row["value"])] = count
    return LibraryFacetCounts(
        statuses=values["status"],
        eras=values["era"],
        keys=values["key"],
        canonical_keys=values["key_canon"],
        hearted=scalars["hearted"],
        unheard=scalars["unheard"],
    )


def status_counts(
    settings: AppSettings, actor: str = "owner"
) -> dict[str, int]:
    """Count status facets over the exact song scope used by the library."""
    counts = {value: 0 for value in FACET_STATUS_VALUES}
    facets = library_facet_counts(settings, actor=actor)
    for value in FACET_STATUS_VALUES:
        counts[value] = int(facets.statuses.get(value, 0))
    return counts


def unheard_count(settings: AppSettings, *, actor: str = "owner") -> int:
    return library_facet_counts(settings, actor=actor).unheard


def untagged_vibe_count(settings: AppSettings) -> int:
    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM songs AS s
            WHERE s.status!='released'
              AND NOT EXISTS(
                SELECT 1 FROM song_tags st
                WHERE st.song_id=s.id AND st.dim='vibe'
              )
              AND NOT EXISTS(
                SELECT 1 FROM reactions rx
                WHERE rx.song_id=s.id AND rx.kind='chip'
                  AND rx.dim='vibe' AND rx.deleted_at IS NULL
                  AND rx.actor NOT LIKE '%:audit:%'
              )
              AND EXISTS(
                SELECT 1 FROM bounces b
                JOIN mirror_files mf ON mf.bounce_id=b.id
                WHERE b.song_id=s.id
              )
            """,
        )
    return int(row["count"] if row else 0)


def track_is_unheard(
    settings: AppSettings, bounce_ulid: str, *, actor: str = "owner"
) -> bool:
    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection,
            """
            SELECT NOT EXISTS(
              SELECT 1 FROM listen_progress
              WHERE share_id=0 AND bounce_ulid=?
                AND actor=? AND state='heard'
            ) AS unheard
            """,
            (bounce_ulid, actor),
        )
    return bool(row["unheard"] if row else True)


def song_detail(
    settings: AppSettings, song_ulid: str
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    with reading(settings.db_path) as connection:
        song = fetch_one(
            connection,
            """
            SELECT s.*,
                   e.name AS era_name, e.color AS era_color,
                   (SELECT COUNT(*) FROM bounces b
                    JOIN mirror_files mf ON mf.bounce_id=b.id
                    WHERE b.song_id=s.id) AS version_count
            FROM songs s
            LEFT JOIN eras AS e ON e.id=s.era_id
            WHERE s.public_id=?
            """,
            (song_ulid,),
        )
        if song is None:
            return None
        rows = fetch_all(
            connection,
            TRACK_SQL.replace(
                "WHERE b.public_id=?", "WHERE b.song_id=?"
            )
            + " ORDER BY COALESCE(b.bounce_date,'') DESC, "
            "COALESCE(b.version,0) DESC, b.id DESC",
            (int(song["id"]),),
        )
        tags = fetch_all(
            connection,
            """
            SELECT dim, value, source, author
            FROM song_tags
            WHERE song_id=?
            ORDER BY dim, value COLLATE NOCASE
            """,
            (int(song["id"]),),
        )
    versions = [_decorate_track(row) for row in rows]
    detail = dict(song)
    name = str(song["era_name"] or "undated")
    detail["era"] = name
    detail["era_css"] = era_css(name)
    detail["latest"] = versions[0] if versions else None
    facts = [
        {
            "dim": dim,
            "value": str(value),
            "source": "catalog",
            "provenance": "catalog",
        }
        for dim, value in (
            ("key", song["key_canon"]),
            ("camelot", song["key_camelot"]),
            (
                "bpm",
                (
                    str(round(float(song["bpm"])))
                    if song["bpm"] is not None
                    else None
                ),
            ),
            ("era", name),
            (
                "energy",
                str(song["energy"]) if song["energy"] is not None else None,
            ),
        )
        if value not in (None, "")
    ]
    facts.extend(
        {
            "dim": str(tag["dim"]),
            "value": str(tag["value"]),
            "source": str(tag["source"]),
            "author": str(tag["author"] or ""),
            "provenance": (
                str(tag["source"])
                if str(tag["source"]) in {"human", "derived", "proposed"}
                else "derived"
            ),
        }
        for tag in tags
        if tag["dim"] in {"instr", "collab"}
    )
    detail["tag_panel"] = {
        "facts": facts,
        "judgment": [
            {
                "dim": "status",
                "value": str(song["status"]),
                "source": (
                    "human" if bool(song["human_touched"]) else "needs judgment"
                ),
                "provenance": (
                    "human" if bool(song["human_touched"]) else "judgment"
                ),
            },
            *[
                {
                    "dim": "vibe",
                    "value": str(tag["value"]),
                    "source": str(tag["source"]),
                    "author": str(tag["author"] or ""),
                    "provenance": (
                        "human"
                        if str(tag["source"]) == "human"
                        else "judgment"
                    ),
                }
                for tag in tags
                if tag["dim"] == "vibe"
            ],
        ],
    }
    return detail, versions


def song_neighbours(
    settings: AppSettings, song_id: int
) -> list[dict[str, Any]]:
    with reading(settings.db_path) as connection:
        return fingerprint_neighbours(connection, song_id)


def filter_vocabulary_counts(
    settings: AppSettings,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "vibe": [],
        "instr": [],
        "collab": [],
        "use": [],
    }
    with reading(settings.db_path) as connection:
        for dim in ("vibe", "instr", "collab", "use"):
            rows = fetch_all(
                connection,
                """
                SELECT value, COUNT(DISTINCT song_id) AS frequency
                FROM (
                  SELECT song_id, value FROM song_tags WHERE dim=?
                  UNION ALL
                  SELECT song_id, value FROM reactions
                  WHERE kind='chip' AND dim=? AND deleted_at IS NULL
                    AND actor NOT LIKE '%:audit:%'
                )
                GROUP BY value
                ORDER BY frequency DESC, value COLLATE NOCASE
                """,
                (dim, dim),
            )
            result[dim] = [
                {
                    "value": str(row["value"]),
                    "count": int(row["frequency"]),
                }
                for row in rows
            ]
    return result


def filter_vocabulary(settings: AppSettings) -> dict[str, list[str]]:
    result = {
        "status": ["idea", "jam", "demo", "mixed", "finished", "released"]
    }
    result.update(
        {
            dim: [str(item["value"]) for item in items]
            for dim, items in filter_vocabulary_counts(settings).items()
        }
    )
    return result


def chip_vocabulary(
    settings: AppSettings, *, bounce_ulid: str | None = None
) -> list[str]:
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT value, COUNT(DISTINCT song_id) AS frequency
            FROM (
              SELECT song_id, value FROM song_tags WHERE dim='vibe'
              UNION ALL
              SELECT song_id, value FROM reactions
              WHERE kind='chip' AND dim='vibe' AND deleted_at IS NULL
                AND actor NOT LIKE '%:audit:%'
            )
            GROUP BY value
            ORDER BY frequency DESC, value COLLATE NOCASE
            LIMIT 24
            """,
        )
    return [str(row["value"]) for row in rows]


def active_reactions(
    settings: AppSettings, *, bounce_ulid: str, actor: str
) -> dict[str, Any]:
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT id, kind, dim, value
            FROM reactions
            WHERE bounce_ulid=? AND actor=? AND deleted_at IS NULL
            ORDER BY id
            """,
            (bounce_ulid, actor),
        )
    return {
        "heart": any(row["kind"] == "heart" for row in rows),
        "chips": {
            str(row["value"])
            for row in rows
            if row["kind"] == "chip" and row["value"]
        },
        "notes": [
            str(row["value"])
            for row in rows
            if row["kind"] == "note" and row["value"]
        ],
    }


def notes_for_track(
    settings: AppSettings, *, bounce_ulid: str
) -> list[Row]:
    with reading(settings.db_path) as connection:
        return fetch_all(
            connection,
            """
            SELECT id, actor, value, timecode_s, created_at
            FROM reactions
            WHERE bounce_ulid=? AND kind='note' AND deleted_at IS NULL
            ORDER BY id DESC
            """,
            (bounce_ulid,),
        )


def progress_for_actor(
    settings: AppSettings, *, share_id: int, actor: str
) -> dict[str, Row]:
    with reading(settings.db_path) as connection:
        rows = fetch_all(
            connection,
            """
            SELECT * FROM listen_progress
            WHERE share_id=? AND actor=?
            """,
            (share_id, actor),
        )
    return {str(row["bounce_ulid"]): row for row in rows}


def replaygain(settings: AppSettings, track: dict[str, Any]) -> dict[str, float | None]:
    path = settings.mirror_root / str(track["mirror_relpath"])
    gain: float | None = None
    peak: float | None = None
    try:
        tags = ID3(path)
    except (MutagenError, OSError):
        return {"track_gain_db": None, "track_peak": None}
    for frame in tags.getall("TXXX"):
        description = str(frame.desc).casefold()
        text = str(frame.text[0]) if frame.text else ""
        try:
            if description == "replaygain_track_gain":
                gain = float(text.casefold().replace("db", "").strip())
            elif description == "replaygain_track_peak":
                peak = float(text.strip())
        except ValueError:
            continue
    return {"track_gain_db": gain, "track_peak": peak}


def activity_rows(settings: AppSettings, *, limit: int = 80) -> list[Row]:
    """Everything that has happened, not just everything that was reacted to.

    This read only reactions, so a feed called Activity showed hearts and tags
    and stayed silent while somebody uploaded eleven tracks or put one on your
    plate. Uploads and assignments are activity; they belong here.
    """
    capped = min(max(limit, 1), 200)
    with reading(settings.db_path) as connection:
        return fetch_all(
            connection,
            """
            SELECT * FROM (
              SELECT r.created_at AS created_at, r.actor AS actor,
                     r.kind AS kind, r.dim AS dim, r.value AS value,
                     r.bounce_ulid AS bounce_ulid, r.timecode_s AS timecode_s,
                     s.title AS title, s.public_id AS song_ulid
              FROM reactions AS r
              LEFT JOIN songs AS s ON s.id=r.song_id
              WHERE r.deleted_at IS NULL

              UNION ALL

              SELECT u.created_at, u.uploaded_by, 'upload', u.source, u.filename,
                     b.public_id, NULL, COALESCE(s.title, u.filename),
                     s.public_id
              FROM uploads AS u
              LEFT JOIN files AS f ON f.relpath=u.relpath
              LEFT JOIN bounces AS b ON b.id=f.bounce_id
              LEFT JOIN songs AS s ON s.id=b.song_id

              UNION ALL

              SELECT a.created_at, a.assigned_by, 'sent', a.assigned_to, a.note,
                     a.bounce_ulid, NULL, s.title, s.public_id
              FROM listen_assignments AS a
              LEFT JOIN bounces AS b2 ON b2.public_id=a.bounce_ulid
              LEFT JOIN songs AS s ON s.id=b2.song_id
            )
            ORDER BY created_at DESC LIMIT ?
            """,
            (capped,),
        )
