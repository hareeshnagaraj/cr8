"""Filename-to-song entity resolution."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Mapping

from .config import Config
from .db import transaction, utc_now
from .keys import canonical_pitch_mode, load_keymap, normalize as normalize_key
from .parse import ParsedName, is_project_internal, parse_name
from .paths import is_drop
from .public_ids import new_ulid


@dataclass(frozen=True)
class ResolvedFile:
    id: int
    relpath: str
    ext: str
    mtime: float
    duration_s: float | None
    prior_bounce_id: int | None
    parsed: ParsedName

    @property
    def parent(self) -> str:
        return str(Path(self.relpath).parent)

    @property
    def stem(self) -> str:
        return Path(self.relpath).stem


@dataclass(frozen=True)
class ResolveSummary:
    parsed: int
    residue: int
    na: int
    songs: int
    bounces: int


@dataclass(frozen=True)
class DateBackfillExample:
    bounce_id: int
    source_stem: str
    bounce_date: str


@dataclass(frozen=True)
class DateBackfillSummary:
    bounces: int
    songs: int
    examples: tuple[DateBackfillExample, ...]
    dry_run: bool


def refresh_song_date_rollups(
    connection: sqlite3.Connection, song_ids: Iterable[int]
) -> int:
    unique_song_ids = sorted(set(song_ids))
    connection.executemany(
        """
        UPDATE songs
        SET first_date=(
              SELECT MIN(NULLIF(TRIM(bounce_date), ''))
              FROM bounces WHERE song_id=songs.id
            ),
            last_date=(
              SELECT MAX(NULLIF(TRIM(bounce_date), ''))
              FROM bounces WHERE song_id=songs.id
            )
        WHERE id=?
        """,
        [(song_id,) for song_id in unique_song_ids],
    )
    return len(unique_song_ids)


def backfill_dates(
    connection: sqlite3.Connection, *, dry_run: bool = False
) -> DateBackfillSummary:
    rows = connection.execute(
        """
        SELECT b.id AS bounce_id, b.song_id, b.source_stem,
               MAX(f.mtime) AS mtime
        FROM bounces AS b
        JOIN files AS f ON f.bounce_id=b.id
        WHERE NULLIF(TRIM(b.bounce_date), '') IS NULL
          AND f.mtime IS NOT NULL
        GROUP BY b.id, b.song_id, b.source_stem
        ORDER BY b.id
        """
    ).fetchall()
    candidates = [
        (
            int(row["bounce_id"]),
            int(row["song_id"]),
            str(row["source_stem"]),
            datetime.fromtimestamp(float(row["mtime"])).date().isoformat(),
        )
        for row in rows
    ]
    song_ids = {candidate[1] for candidate in candidates}
    examples = tuple(
        DateBackfillExample(
            bounce_id=bounce_id,
            source_stem=source_stem,
            bounce_date=bounce_date,
        )
        for bounce_id, _song_id, source_stem, bounce_date in candidates[:5]
    )
    if dry_run:
        return DateBackfillSummary(
            bounces=len(candidates),
            songs=len(song_ids),
            examples=examples,
            dry_run=True,
        )

    with transaction(connection):
        connection.executemany(
            """
            UPDATE bounces
            SET bounce_date=?, date_source='mtime'
            WHERE id=? AND NULLIF(TRIM(bounce_date), '') IS NULL
            """,
            [(bounce_date, bounce_id) for bounce_id, _, _, bounce_date in candidates],
        )
        songs = refresh_song_date_rollups(connection, song_ids)
    return DateBackfillSummary(
        bounces=len(candidates),
        songs=songs,
        examples=examples,
        dry_run=False,
    )


def _chunks[T](items: list[T], size: int) -> Iterable[list[T]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def slugify(value: str | Iterable[str]) -> str:
    if not isinstance(value, str):
        value = " ".join(value)
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def display_title(tokens: Iterable[str]) -> str:
    text = " ".join(tokens).strip()
    if not text:
        return text
    return " ".join(word[:1].upper() + word[1:] for word in text.split())


def levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def enqueue_review(
    connection: sqlite3.Connection,
    kind: str,
    *,
    file_id: int | None = None,
    song_id: int | None = None,
    payload: object | None = None,
) -> int:
    payload_text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    existing = connection.execute(
        """
        SELECT id FROM review_queue
        WHERE kind=? AND file_id IS ? AND song_id IS ? AND payload=?
        ORDER BY id LIMIT 1
        """,
        (kind, file_id, song_id, payload_text),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])
    cursor = connection.execute(
        """
        INSERT INTO review_queue(kind, file_id, song_id, payload, created_at)
        VALUES(?, ?, ?, ?, ?)
        """,
        (kind, file_id, song_id, payload_text, utc_now()),
    )
    return int(cursor.lastrowid)


def _parse_files(
    connection: sqlite3.Connection, config: Config
) -> tuple[list[ResolvedFile], int, int, int]:
    keymap = load_keymap(config.keymap_path)
    rows = connection.execute(
        """
        SELECT id, relpath, layer, ext, mtime, duration_s, bounce_id, parse_status
        FROM files WHERE missing_since IS NULL ORDER BY relpath
        """
    ).fetchall()
    parsed_files: list[ResolvedFile] = []
    parsed_count = 0
    residue_count = 0
    na_count = 0
    changes: list[tuple[str, int]] = []
    review_rows: list[tuple[int, str]] = []

    for row in rows:
        relpath = str(row["relpath"])
        stem = Path(relpath).stem
        if row["layer"] == "project" and is_project_internal(stem, relpath):
            if row["parse_status"] != "assigned":
                changes.append(("na", int(row["id"])))
            na_count += 1
            continue
        if row["parse_status"] == "assigned":
            continue
        parsed = parse_name(
            stem,
            mtime=float(row["mtime"] or 0),
            keymap=keymap,
            known_collabs=tuple(config.vocab.known_collabs),
        )
        if not parsed.title_tokens or not slugify(parsed.title_tokens):
            # Somebody uploaded this on purpose. Refusing it because the
            # filename does not follow a convention they have never seen would
            # make the feature feel broken, so take the filename as the title
            # and put it in front of a human via the review queue instead.
            fallback = _title_from_filename(stem) if is_drop(relpath) else None
            if fallback is None:
                changes.append(("residue", int(row["id"])))
                residue_count += 1
                if row["layer"] == "curated":
                    review_rows.append((int(row["id"]), relpath))
                continue
            parsed = replace(parsed, title_tokens=fallback)
            review_rows.append((int(row["id"]), relpath))
        changes.append(("parsed", int(row["id"])))
        parsed_count += 1
        if row["layer"] == "curated":
            parsed_files.append(
                ResolvedFile(
                    id=int(row["id"]),
                    relpath=relpath,
                    ext=str(row["ext"]).casefold(),
                    mtime=float(row["mtime"] or 0),
                    duration_s=(
                        float(row["duration_s"])
                        if row["duration_s"] is not None
                        else None
                    ),
                    prior_bounce_id=(
                        int(row["bounce_id"]) if row["bounce_id"] is not None else None
                    ),
                    parsed=parsed,
                )
            )

    for batch in _chunks(changes, 500):
        with transaction(connection):
            connection.executemany(
                "UPDATE files SET parse_status=? WHERE id=? AND parse_status <> 'assigned'",
                batch,
            )
    for batch in _chunks(review_rows, 250):
        with transaction(connection):
            for file_id, relpath in batch:
                enqueue_review(
                    connection,
                    "unparsed_name",
                    file_id=file_id,
                    payload={"relpath": relpath},
                )
    return parsed_files, parsed_count, residue_count, na_count


class _UnionFind:
    def __init__(self, values: Iterable[int]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _group_twins(
    files: list[ResolvedFile],
) -> tuple[list[list[ResolvedFile]], list[dict[str, object]]]:
    by_stem: dict[str, list[ResolvedFile]] = defaultdict(list)
    for item in files:
        by_stem[item.stem.casefold()].append(item)
    union = _UnionFind(item.id for item in files)
    compressed = {".mp3", ".m4a"}
    mismatches: list[dict[str, object]] = []

    for stem_files in by_stem.values():
        for index, left in enumerate(stem_files):
            for right in stem_files[index + 1 :]:
                ext_pair = {left.ext, right.ext}
                if ".wav" not in ext_pair or not ext_pair.intersection(compressed):
                    continue
                if left.parent.casefold() == right.parent.casefold():
                    union.union(left.id, right.id)
                    continue
                if left.duration_s is None or right.duration_s is None:
                    continue
                delta = abs(left.duration_s - right.duration_s)
                if delta <= 0.5:
                    union.union(left.id, right.id)
                else:
                    pair = sorted((left.relpath, right.relpath))
                    mismatches.append(
                        {
                            "files": pair,
                            "duration_delta_s": round(delta, 3),
                        }
                    )

    groups: dict[int, list[ResolvedFile]] = defaultdict(list)
    for item in files:
        groups[union.find(item.id)].append(item)
    grouped = sorted(
        (sorted(group, key=lambda item: item.relpath) for group in groups.values()),
        key=lambda group: group[0].relpath,
    )
    unique_mismatches = {
        json.dumps(value, sort_keys=True, separators=(",", ":")): value
        for value in mismatches
    }
    return grouped, list(unique_mismatches.values())


def _song_for_slug(
    connection: sqlite3.Connection, slug: str, title: str
) -> tuple[int, bool]:
    alias = connection.execute(
        "SELECT song_id FROM song_aliases WHERE alias_slug=?", (slug,)
    ).fetchone()
    if alias is not None:
        return int(alias["song_id"]), False
    row = connection.execute(
        "SELECT id FROM songs WHERE slug=? ORDER BY (disambig='') DESC, id LIMIT 1",
        (slug,),
    ).fetchone()
    if row is not None:
        return int(row["id"]), False
    cursor = connection.execute(
        "INSERT INTO songs(slug, title, public_id) VALUES(?, ?, ?)",
        (slug, title, new_ulid()),
    )
    return int(cursor.lastrowid), True


def _choose_source_stem(
    connection: sqlite3.Connection,
    song_id: int,
    group: list[ResolvedFile],
) -> tuple[int | None, str]:
    prior_ids = sorted(
        {item.prior_bounce_id for item in group if item.prior_bounce_id is not None}
    )
    for prior_id in prior_ids:
        row = connection.execute(
            "SELECT id, source_stem FROM bounces WHERE id=? AND song_id=?",
            (prior_id, song_id),
        ).fetchone()
        if row is not None:
            return int(row["id"]), str(row["source_stem"])

    stem = max(group, key=lambda item: item.mtime).stem
    row = connection.execute(
        "SELECT id FROM bounces WHERE song_id=? AND source_stem=?", (song_id, stem)
    ).fetchone()
    if row is None:
        return None, stem
    parent = max(group, key=lambda item: item.mtime).parent
    base = f"{stem} [{parent}]"
    candidate = base
    suffix = 2
    while connection.execute(
        "SELECT 1 FROM bounces WHERE song_id=? AND source_stem=?",
        (song_id, candidate),
    ).fetchone():
        candidate = f"{base} #{suffix}"
        suffix += 1
    return None, candidate


def _date_span_days(values: Iterable[str | None]) -> int:
    dates = [date.fromisoformat(value) for value in values if value]
    return (max(dates) - min(dates)).days if len(dates) >= 2 else 0


def _roll_up_songs(
    connection: sqlite3.Connection,
    metadata: dict[int, list[tuple[ResolvedFile, int]]],
    keymap: Mapping[str, str],
) -> None:
    for song_id, entries in metadata.items():
        bounces = [
            (
                item.parsed.date,
                item.parsed.version or 0,
                item.mtime,
                item,
                bounce_id,
            )
            for item, bounce_id in entries
        ]
        newest = max(bounces, key=lambda value: (value[0] or "", value[1], value[2]))
        title = display_title(newest[3].parsed.title_tokens)
        dates = [value[0] for value in bounces if value[0]]
        key_values = [
            normalize_key(item.parsed.key_raw, keymap)
            for item, _ in entries
            if item.parsed.key_raw
        ]
        canonical_keys = [canon for canon, _ in key_values if canon]
        key_counts = Counter(canonical_keys)
        key_canon = key_counts.most_common(1)[0][0] if key_counts else None
        key_camelot = (
            next(
                camelot
                for canon, camelot in key_values
                if canon == key_canon and camelot is not None
            )
            if key_canon
            else None
        )
        bpm_values = [item.parsed.bpm for item, _ in entries if item.parsed.bpm]
        bpm = Counter(bpm_values).most_common(1)[0][0] if bpm_values else None
        current = connection.execute(
            """
            SELECT title, human_touched, key_canon, key_camelot, key_source,
                   bpm, bpm_source
            FROM songs WHERE id=?
            """,
            (song_id,),
        ).fetchone()
        if current is None:
            continue
        rolled_title = str(current["title"]) if current["human_touched"] else title
        if current["key_source"] == "human":
            rolled_key = (
                current["key_canon"],
                current["key_camelot"],
                current["key_source"],
            )
        elif key_canon is not None:
            rolled_key = (key_canon, key_camelot, "filename")
        elif current["key_source"] in {"mik", "detected"}:
            rolled_key = (
                current["key_canon"],
                current["key_camelot"],
                current["key_source"],
            )
        else:
            rolled_key = (None, None, None)
        if current["bpm_source"] == "human":
            rolled_bpm = (current["bpm"], current["bpm_source"])
        elif bpm is not None:
            rolled_bpm = (bpm, "filename")
        elif current["bpm_source"] in {"mik", "detected"}:
            rolled_bpm = (current["bpm"], current["bpm_source"])
        else:
            rolled_bpm = (None, None)

        connection.execute(
            """
            UPDATE songs
            SET title=?, key_canon=?, key_camelot=?, key_source=?,
                bpm=?, bpm_source=?
            WHERE id=?
            """,
            (
                rolled_title,
                *rolled_key,
                *rolled_bpm,
                song_id,
            ),
        )
        comparison_keys = {
            canonical_pitch_mode(value) for value in canonical_keys if value
        }
        if len(comparison_keys) > 1:
            enqueue_review(
                connection,
                "key_conflict",
                song_id=song_id,
                payload={"keys": sorted(set(canonical_keys))},
            )
            if _date_span_days(dates) > 548:
                enqueue_review(
                    connection,
                    "possible_distinct",
                    song_id=song_id,
                    payload={
                        "keys": sorted(set(canonical_keys)),
                        "first_date": min(dates),
                        "last_date": max(dates),
                    },
                )

        machine_collabs = {
            item.parsed.collab for item, _ in entries if item.parsed.collab
        }
        connection.execute(
            "DELETE FROM song_tags WHERE song_id=? AND dim='collab' AND source<>'human'",
            (song_id,),
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO song_tags(song_id, dim, value, source, created_at)
            VALUES(?, 'collab', ?, 'filename', ?)
            """,
            [(song_id, value, utc_now()) for value in sorted(machine_collabs)],
        )

    refresh_song_date_rollups(connection, metadata)


def _near_slug_reviews(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute("SELECT id, slug FROM songs ORDER BY slug, id").fetchall()
    payloads: list[dict[str, object]] = []
    for index, left in enumerate(rows):
        left_slug = str(left["slug"])
        if len(left_slug) < 6:
            continue
        for right in rows[index + 1 :]:
            right_slug = str(right["slug"])
            if len(right_slug) < 6:
                continue
            if (
                left_slug.startswith(right_slug)
                or right_slug.startswith(left_slug)
                or levenshtein(left_slug, right_slug) <= 2
            ):
                pair = sorted(
                    (
                        {"id": int(left["id"]), "slug": left_slug},
                        {"id": int(right["id"]), "slug": right_slug},
                    ),
                    key=lambda item: item["id"],
                )
                payloads.append({"songs": pair})
    return payloads


def _project_link_candidates(
    connection: sqlite3.Connection,
) -> tuple[list[tuple[int, int]], list[tuple[int, dict[str, object]]]]:
    projects = connection.execute(
        "SELECT id, relpath, name_slug FROM projects WHERE name_slug IS NOT NULL"
    ).fetchall()
    songs = connection.execute("SELECT id, slug FROM songs").fetchall()
    exact: list[tuple[int, int]] = []
    reviews: list[tuple[int, dict[str, object]]] = []
    for project in projects:
        project_slug = str(project["name_slug"] or "")
        if not project_slug:
            continue
        for song in songs:
            song_slug = str(song["slug"])
            if project_slug == song_slug:
                exact.append((int(song["id"]), int(project["id"])))
            elif levenshtein(project_slug, song_slug) <= 2:
                reviews.append(
                    (
                        int(song["id"]),
                        {
                            "project_id": int(project["id"]),
                            "project_relpath": str(project["relpath"]),
                            "project_slug": project_slug,
                            "song_slug": song_slug,
                        },
                    )
                )
    return exact, reviews


def resolve_catalog(connection: sqlite3.Connection, config: Config) -> ResolveSummary:
    files, parsed_count, residue_count, na_count = _parse_files(connection, config)
    keymap = load_keymap(config.keymap_path)
    groups, mismatches = _group_twins(files)
    with transaction(connection):
        connection.execute(
            """
            UPDATE files SET bounce_id=NULL
            WHERE layer='curated' AND parse_status='parsed' AND missing_since IS NULL
            """
        )
        for payload in mismatches:
            enqueue_review(connection, "twin_mismatch", payload=payload)

    metadata: dict[int, list[tuple[ResolvedFile, int]]] = defaultdict(list)
    for group_batch in _chunks(groups, 250):
        with transaction(connection):
            for group in group_batch:
                representative = max(group, key=lambda item: item.mtime)
                slug = slugify(representative.parsed.title_tokens)
                title = display_title(representative.parsed.title_tokens)
                song_id, _ = _song_for_slug(connection, slug, title)
                prior_id, source_stem = _choose_source_stem(connection, song_id, group)
                if prior_id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO bounces(
                          public_id, song_id, source_stem, bounce_date, date_source,
                          date_suspect, version, mixrole, collab_raw
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_ulid(),
                            song_id,
                            source_stem,
                            representative.parsed.date,
                            representative.parsed.date_source,
                            int(representative.parsed.date_suspect),
                            representative.parsed.version,
                            representative.parsed.mixrole,
                            representative.parsed.collab,
                        ),
                    )
                    bounce_id = int(cursor.lastrowid)
                else:
                    bounce_id = prior_id
                    connection.execute(
                        """
                        UPDATE bounces
                        SET bounce_date=?, date_source=?, date_suspect=?, version=?,
                            mixrole=?, collab_raw=?
                        WHERE id=?
                        """,
                        (
                            representative.parsed.date,
                            representative.parsed.date_source,
                            int(representative.parsed.date_suspect),
                            representative.parsed.version,
                            representative.parsed.mixrole,
                            representative.parsed.collab,
                            bounce_id,
                        ),
                    )
                connection.executemany(
                    "UPDATE files SET bounce_id=? WHERE id=?",
                    [(bounce_id, item.id) for item in group],
                )
                for item in group:
                    metadata[song_id].append((item, bounce_id))
                    if item.parsed.date_suspect:
                        enqueue_review(
                            connection,
                            "date_suspect",
                            file_id=item.id,
                            song_id=song_id,
                            payload={
                                "relpath": item.relpath,
                                "bounce_date": item.parsed.date,
                                "mtime": item.mtime,
                            },
                        )

    metadata_items = list(metadata.items())
    for metadata_batch in _chunks(metadata_items, 100):
        with transaction(connection):
            _roll_up_songs(connection, dict(metadata_batch), keymap)

    near_slug_payloads = _near_slug_reviews(connection)
    exact_links, project_reviews = _project_link_candidates(connection)
    for payload_batch in _chunks(near_slug_payloads, 250):
        with transaction(connection):
            for payload in payload_batch:
                enqueue_review(connection, "merge_suggestion", payload=payload)
    for exact_batch in _chunks(exact_links, 250):
        with transaction(connection):
            connection.executemany(
                """
                INSERT OR IGNORE INTO song_projects(song_id, project_id, method)
                VALUES(?, ?, 'slug_exact')
                """,
                exact_batch,
            )
    for review_batch in _chunks(project_reviews, 250):
        with transaction(connection):
            for song_id, payload in review_batch:
                enqueue_review(
                    connection, "project_link", song_id=song_id, payload=payload
                )

    with transaction(connection):
        connection.execute(
            """
            DELETE FROM bounces
            WHERE id NOT IN (SELECT DISTINCT bounce_id FROM files WHERE bounce_id IS NOT NULL)
              AND id NOT IN (SELECT bounce_id FROM mirror_files)
            """
        )
        connection.execute(
            """
            DELETE FROM songs
            WHERE human_touched=0
              AND id NOT IN (SELECT DISTINCT song_id FROM bounces)
              AND id NOT IN (SELECT song_id FROM song_aliases)
              AND id NOT IN (SELECT song_id FROM song_tags)
            """
        )

    songs = int(connection.execute("SELECT COUNT(*) FROM songs").fetchone()[0])
    bounces = int(connection.execute("SELECT COUNT(*) FROM bounces").fetchone()[0])
    return ResolveSummary(
        parsed=parsed_count,
        residue=residue_count,
        na=na_count,
        songs=songs,
        bounces=bounces,
    )


def _title_from_filename(stem: str) -> list[str] | None:
    """Readable words out of a filename nobody wrote for us.

    "henry_idea 3 FINAL-v2" becomes ["henry", "idea", "3", "final", "v2"]. If
    nothing survives, there is genuinely nothing to call the song and the file
    stays residue.
    """
    words = [
        word
        for word in re.split(r"[^0-9A-Za-z]+", stem)
        if word and not word.isspace()
    ]
    if not words:
        return None
    return [word.casefold() for word in words]
