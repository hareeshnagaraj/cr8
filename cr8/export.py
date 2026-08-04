"""Portable full-catalog export."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .config import Config
from .csvio import export_csv
from .paths import source_path


@dataclass(frozen=True)
class ExportSummary:
    songs: int
    collections: int
    output_dir: Path


def _safe_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "collection"


def _song_payload(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    songs: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT id, public_id, slug, disambig, title, status, keeper,
               key_canon, key_camelot, bpm, energy, first_date, last_date,
               notes, released_url
        FROM songs
        ORDER BY slug, disambig
        """
    ):
        song = dict(row)
        song["tags"] = [
            {
                "dimension": str(tag["dim"]),
                "value": str(tag["value"]),
                "source": str(tag["source"]),
                "author": tag["author"],
            }
            for tag in connection.execute(
                """
                SELECT dim, value, source, author
                FROM song_tags WHERE song_id=?
                ORDER BY dim, value
                """,
                (int(row["id"]),),
            )
        ]
        songs.append(song)
    return songs


def _collection_path(
    connection: sqlite3.Connection,
    config: Config,
    bounce_ulid: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT f.relpath
        FROM bounces AS b
        JOIN files AS f ON f.bounce_id=b.id
        WHERE b.public_id=? AND f.missing_since IS NULL
        ORDER BY CASE f.layer WHEN 'curated' THEN 0 ELSE 1 END,
                 CASE f.ext WHEN '.wav' THEN 0 ELSE 1 END,
                 f.id
        LIMIT 1
        """,
        (bounce_ulid,),
    ).fetchone()
    if row is None:
        return None
    return str(source_path(config, str(row["relpath"])))


def export_portable(
    connection: sqlite3.Connection,
    config: Config,
    output_dir: str | Path,
) -> ExportSummary:
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    song_count = export_csv(
        connection,
        config,
        destination / "songs.csv",
    )
    payload = {
        "format": "crate-portable-v1",
        "songs": _song_payload(connection),
    }
    (destination / "songs.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    collection_count = 0
    collections_dir = destination / "collections"
    collections_dir.mkdir(exist_ok=True)
    for collection in connection.execute(
        "SELECT id, ulid, name FROM collections ORDER BY name COLLATE NOCASE"
    ):
        lines = ["#EXTM3U"]
        for item in connection.execute(
            """
            SELECT ci.bounce_ulid, s.title
            FROM collection_items AS ci
            JOIN bounces AS b ON b.public_id=ci.bounce_ulid
            JOIN songs AS s ON s.id=b.song_id
            WHERE ci.collection_id=?
            ORDER BY ci.position
            """,
            (int(collection["id"]),),
        ):
            path = _collection_path(
                connection,
                config,
                str(item["bounce_ulid"]),
            )
            if path is None:
                continue
            lines.extend((f"#EXTINF:-1,{item['title']}", path))
        name = (
            f"{_safe_name(str(collection['name']))}-"
            f"{str(collection['ulid'])[-6:]}.m3u"
        )
        (collections_dir / name).write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        collection_count += 1
    return ExportSummary(song_count, collection_count, destination)
