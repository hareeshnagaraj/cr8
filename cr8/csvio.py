"""CSV export and import with closed-vocabulary validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable

from .config import Config
from .db import transaction, utc_now
from .keys import load_keymap, normalize as normalize_key
from .paths import source_path


CSV_FIELDS = (
    "id",
    "slug",
    "title",
    "status",
    "keeper",
    "key_canon",
    "bpm",
    "first_date",
    "last_date",
    "n_bounces",
    "audition_path",
    "vibe",
    "instr",
    "collab",
    "notes",
)


@dataclass(frozen=True)
class ImportSummary:
    rows: int
    songs_changed: int
    fields_changed: int
    dry_run: bool


def _song_tags(connection: sqlite3.Connection, song_id: int, dim: str) -> list[str]:
    return [
        str(row["value"])
        for row in connection.execute(
            "SELECT value FROM song_tags WHERE song_id=? AND dim=? ORDER BY value",
            (song_id, dim),
        )
    ]


def export_csv(
    connection: sqlite3.Connection,
    config: Config,
    out_path: str | Path,
    *,
    filter_value: str | None = None,
) -> int:
    where = ""
    params: list[object] = []
    if filter_value:
        if "=" in filter_value:
            field, value = filter_value.split("=", 1)
            if field not in {"status", "slug"}:
                raise ValueError("filter field must be status or slug")
            where = f"WHERE s.{field}=?"
            params.append(value)
        else:
            where = "WHERE s.slug LIKE ? OR s.title LIKE ?"
            params.extend((f"%{filter_value}%", f"%{filter_value}%"))
    rows = connection.execute(
        f"""
        SELECT s.*,
          (SELECT COUNT(*) FROM bounces b WHERE b.song_id=s.id) AS n_bounces
        FROM songs s {where} ORDER BY s.slug, s.disambig
        """,
        params,
    ).fetchall()
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            latest = connection.execute(
                """
                SELECT f.relpath
                FROM bounces b JOIN files f ON f.bounce_id=b.id
                WHERE b.song_id=? AND b.mixrole='main' AND f.missing_since IS NULL
                ORDER BY COALESCE(b.bounce_date, '') DESC,
                         COALESCE(b.version, 0) DESC, COALESCE(f.mtime, 0) DESC,
                         CASE f.ext WHEN '.wav' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (row["id"],),
            ).fetchone()
            audition_path = (
                str(source_path(config, str(latest["relpath"]))) if latest else ""
            )
            writer.writerow(
                {
                    "id": row["id"],
                    "slug": row["slug"],
                    "title": row["title"],
                    "status": row["status"],
                    "keeper": row["keeper"],
                    "key_canon": row["key_canon"] or "",
                    "bpm": row["bpm"] if row["bpm"] is not None else "",
                    "first_date": row["first_date"] or "",
                    "last_date": row["last_date"] or "",
                    "n_bounces": row["n_bounces"],
                    "audition_path": audition_path,
                    "vibe": "; ".join(_song_tags(connection, row["id"], "vibe")),
                    "instr": "; ".join(_song_tags(connection, row["id"], "instr")),
                    "collab": "; ".join(_song_tags(connection, row["id"], "collab")),
                    "notes": row["notes"] or "",
                }
            )
    return len(rows)


def _split_values(value: str) -> set[str]:
    return {item.strip().casefold() for item in value.split(";") if item.strip()}


def _known_values(
    connection: sqlite3.Connection, config: Config, dim: str
) -> set[str]:
    values = {
        str(row["value"]).casefold()
        for row in connection.execute(
            "SELECT DISTINCT value FROM song_tags WHERE dim=?", (dim,)
        )
    }
    if dim == "collab":
        values.update(config.vocab.known_collabs)
        values.add("solo")
    return values


def import_csv(
    connection: sqlite3.Connection,
    config: Config,
    csv_path: str | Path,
    *,
    allow_new: bool = False,
    dry_run: bool = False,
    author: str | None = None,
) -> ImportSummary:
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        return ImportSummary(0, 0, 0, dry_run)
    required = {"id", "title", "status", "keeper", "key_canon", "bpm", "notes"}
    if not required.issubset(rows[0]):
        raise ValueError(f"CSV missing columns: {', '.join(sorted(required - rows[0].keys()))}")

    known = {
        dim: _known_values(connection, config, dim)
        for dim in ("vibe", "instr", "collab")
    }
    prepared: list[tuple[sqlite3.Row, dict[str, object], dict[str, set[str]]]] = []
    keymap = load_keymap(config.keymap_path)
    for csv_row in rows:
        try:
            song_id = int(csv_row["id"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid song id: {csv_row.get('id')!r}") from exc
        current = connection.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        if current is None:
            raise ValueError(f"unknown song id: {song_id}")
        status = csv_row["status"].strip()
        if status not in config.vocab.status:
            raise ValueError(f"invalid status for song {song_id}: {status}")
        try:
            keeper = int(csv_row["keeper"])
        except ValueError as exc:
            raise ValueError(f"invalid keeper for song {song_id}") from exc
        if not 0 <= keeper <= 5:
            raise ValueError(f"keeper must be 0..5 for song {song_id}")
        key_raw = csv_row["key_canon"].strip()
        canon, camelot = normalize_key(key_raw, keymap) if key_raw else (None, None)
        if key_raw and canon is None:
            raise ValueError(f"invalid key for song {song_id}: {key_raw}")
        bpm_text = csv_row["bpm"].strip()
        try:
            bpm = float(bpm_text) if bpm_text else None
        except ValueError as exc:
            raise ValueError(f"invalid bpm for song {song_id}") from exc
        tag_values = {
            dim: _split_values(csv_row.get(dim, "")) for dim in ("vibe", "instr", "collab")
        }
        if not allow_new:
            for dim, values in tag_values.items():
                unknown = values - known[dim]
                if unknown:
                    raise ValueError(
                        f"unknown {dim} values for song {song_id}: {', '.join(sorted(unknown))}"
                    )
        fields: dict[str, object] = {
            "title": csv_row["title"].strip(),
            "status": status,
            "keeper": keeper,
            "key_canon": canon,
            "key_camelot": camelot,
            "key_source": "human" if key_raw else None,
            "bpm": bpm,
            "bpm_source": "human" if bpm is not None else None,
            "notes": csv_row["notes"],
        }
        prepared.append((current, fields, tag_values))

    songs_changed = 0
    fields_changed = 0
    operations: list[tuple[sqlite3.Row, dict[str, object], dict[str, set[str]]]] = []
    for current, fields, tag_values in prepared:
        changed = sum(current[key] != value for key, value in fields.items())
        for dim, values in tag_values.items():
            changed += int(set(_song_tags(connection, current["id"], dim)) != values)
        if changed:
            songs_changed += 1
            fields_changed += changed
            operations.append((current, fields, tag_values))
    if dry_run:
        return ImportSummary(len(rows), songs_changed, fields_changed, True)

    with transaction(connection):
        for current, fields, tag_values in operations:
            connection.execute(
                """
                UPDATE songs SET title=?, status=?, keeper=?, key_canon=?,
                  key_camelot=?, key_source=?, bpm=?, bpm_source=?, notes=?,
                  human_touched=1 WHERE id=?
                """,
                (*fields.values(), current["id"]),
            )
            for dim, values in tag_values.items():
                connection.execute(
                    "DELETE FROM song_tags WHERE song_id=? AND dim=? AND source='human'",
                    (current["id"], dim),
                )
                connection.executemany(
                    """
                    INSERT INTO song_tags(song_id, dim, value, source, author, created_at)
                    VALUES(?, ?, ?, 'human', ?, ?)
                    ON CONFLICT(song_id, dim, value) DO UPDATE SET
                      source='human', author=excluded.author, created_at=excluded.created_at
                    """,
                    [
                        (current["id"], dim, value, author, utc_now())
                        for value in sorted(values)
                    ],
                )
    return ImportSummary(len(rows), songs_changed, fields_changed, False)
