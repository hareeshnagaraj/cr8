"""Human edits and the plain-prompt review queue."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
from datetime import date
from typing import Callable, Iterable

from .config import Config
from .db import transaction, utc_now
from .keys import load_keymap, normalize as normalize_key
from .public_ids import new_ulid
from .resolve import display_title, slugify
from .paths import source_path


def find_song(connection: sqlite3.Connection, target: str) -> sqlite3.Row:
    if target.isdigit():
        row = connection.execute("SELECT * FROM songs WHERE id=?", (int(target),)).fetchone()
    else:
        row = connection.execute(
            """
            SELECT s.* FROM songs s
            LEFT JOIN song_aliases a ON a.song_id=s.id
            WHERE s.slug=? OR a.alias_slug=?
            ORDER BY (s.slug=?) DESC, (s.disambig='') DESC, s.id LIMIT 1
            """,
            (target.casefold(), target.casefold(), target.casefold()),
        ).fetchone()
    if row is None:
        raise ValueError(f"song not found: {target}")
    return row


def _allowed_tag_values(
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


def set_song(
    connection: sqlite3.Connection,
    config: Config,
    target: str,
    changes: Iterable[str],
    *,
    allow_new: bool = False,
    author: str | None = None,
) -> int:
    song = find_song(connection, target)
    scalar: dict[str, object] = {}
    tag_changes: list[tuple[str, str, bool]] = []
    keymap = load_keymap(config.keymap_path)
    for expression in changes:
        if "=" not in expression:
            raise ValueError(f"expected key=value: {expression}")
        raw_key, raw_value = expression.split("=", 1)
        add = raw_key.startswith("+")
        remove = raw_key.startswith("-")
        key = raw_key.lstrip("+-").casefold()
        value = raw_value.strip()
        if key in {"vibe", "instr", "collab"}:
            if not add and not remove:
                raise ValueError(f"use +{key}= or -{key}= for multi-valued tags")
            normalized = value.casefold()
            if add and not allow_new and normalized not in _allowed_tag_values(
                connection, config, key
            ):
                raise ValueError(f"unknown {key}: {value}; pass --allow-new")
            tag_changes.append((key, normalized, add))
            continue
        if add or remove:
            raise ValueError(f"{key} is not multi-valued")
        if key == "status":
            if value not in config.vocab.status:
                raise ValueError(f"invalid status: {value}")
            scalar["status"] = value
        elif key == "keeper":
            keeper = int(value)
            if not 0 <= keeper <= 5:
                raise ValueError("keeper must be 0..5")
            scalar["keeper"] = keeper
        elif key == "key":
            canon, camelot = normalize_key(value, keymap)
            if canon is None:
                raise ValueError(f"invalid key: {value}")
            scalar.update(
                key_canon=canon, key_camelot=camelot, key_source="human"
            )
        elif key == "bpm":
            scalar["bpm"] = float(value)
            scalar["bpm_source"] = "human"
        elif key == "released_url":
            scalar["released_url"] = value or None
        elif key in {"title", "notes"}:
            scalar[key] = value
        else:
            raise ValueError(f"unknown field: {key}")

    with transaction(connection):
        if scalar:
            assignments = ", ".join(f"{key}=?" for key in scalar)
            connection.execute(
                f"UPDATE songs SET {assignments}, human_touched=1 WHERE id=?",
                (*scalar.values(), song["id"]),
            )
        elif tag_changes:
            connection.execute(
                "UPDATE songs SET human_touched=1 WHERE id=?", (song["id"],)
            )
        for dim, value, add in tag_changes:
            if add:
                connection.execute(
                    """
                    INSERT INTO song_tags(song_id, dim, value, source, author, created_at)
                    VALUES(?, ?, ?, 'human', ?, ?)
                    ON CONFLICT(song_id, dim, value) DO UPDATE SET
                      source='human', author=excluded.author, created_at=excluded.created_at
                    """,
                    (song["id"], dim, value, author, utc_now()),
                )
            else:
                connection.execute(
                    "DELETE FROM song_tags WHERE song_id=? AND dim=? AND value=?",
                    (song["id"], dim, value),
                )
    return int(song["id"])


def _resolve_item(
    connection: sqlite3.Connection,
    item_id: int,
    resolution: object,
    *,
    ignored: bool = False,
) -> None:
    connection.execute(
        """
        UPDATE review_queue SET status=?, resolved_at=?, resolution=? WHERE id=?
        """,
        (
            "ignored" if ignored else "resolved",
            utc_now(),
            json.dumps(resolution, sort_keys=True),
            item_id,
        ),
    )


def _play(config: Config, connection: sqlite3.Connection, file_id: int | None) -> None:
    if file_id is None:
        return
    row = connection.execute("SELECT relpath FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        return
    subprocess.run(
        ["afplay", "-t", "10", str(source_path(config, str(row["relpath"])))],
        check=False,
    )


def _play_song(config: Config, connection: sqlite3.Connection, song_id: int) -> None:
    row = connection.execute(
        """
        SELECT f.id
        FROM bounces b JOIN files f ON f.bounce_id=b.id
        WHERE b.song_id=? AND f.missing_since IS NULL
        ORDER BY COALESCE(b.bounce_date, '') DESC, COALESCE(b.version, 0) DESC,
                 COALESCE(f.mtime, 0) DESC
        LIMIT 1
        """,
        (song_id,),
    ).fetchone()
    if row is not None:
        _play(config, connection, int(row["id"]))


def _merge_songs(
    connection: sqlite3.Connection, survivor_id: int, loser_id: int
) -> None:
    if survivor_id == loser_id:
        raise ValueError("survivor and loser must be different songs")
    survivor = connection.execute(
        "SELECT id FROM songs WHERE id=?", (survivor_id,)
    ).fetchone()
    loser = connection.execute("SELECT * FROM songs WHERE id=?", (loser_id,)).fetchone()
    if survivor is None or loser is None:
        raise ValueError("merge song no longer exists")
    for bounce in connection.execute(
        "SELECT id, source_stem FROM bounces WHERE song_id=?", (loser_id,)
    ).fetchall():
        existing = connection.execute(
            "SELECT id FROM bounces WHERE song_id=? AND source_stem=?",
            (survivor_id, bounce["source_stem"]),
        ).fetchone()
        if existing is None:
            connection.execute(
                "UPDATE bounces SET song_id=? WHERE id=?",
                (survivor_id, bounce["id"]),
            )
        else:
            connection.execute(
                "UPDATE files SET bounce_id=? WHERE bounce_id=?",
                (existing["id"], bounce["id"]),
            )
            connection.execute(
                "DELETE FROM mirror_files WHERE bounce_id=?", (bounce["id"],)
            )
            connection.execute("DELETE FROM bounces WHERE id=?", (bounce["id"],))
    connection.execute(
        """
        INSERT OR IGNORE INTO song_tags(song_id, dim, value, source, author, created_at)
        SELECT ?, dim, value, source, author, created_at FROM song_tags WHERE song_id=?
        """,
        (survivor_id, loser_id),
    )
    connection.execute("DELETE FROM song_tags WHERE song_id=?", (loser_id,))
    connection.execute(
        """
        INSERT OR IGNORE INTO song_projects(song_id, project_id, method)
        SELECT ?, project_id, method FROM song_projects WHERE song_id=?
        """,
        (survivor_id, loser_id),
    )
    connection.execute("DELETE FROM song_projects WHERE song_id=?", (loser_id,))
    connection.execute(
        "UPDATE feedback SET song_id=? WHERE song_id=?", (survivor_id, loser_id)
    )
    connection.execute(
        "DELETE FROM rating_sync WHERE song_id=? AND nd_user IN "
        "(SELECT nd_user FROM rating_sync WHERE song_id=?)",
        (loser_id, survivor_id),
    )
    connection.execute(
        "UPDATE rating_sync SET song_id=? WHERE song_id=?", (survivor_id, loser_id)
    )
    connection.execute(
        "UPDATE review_queue SET song_id=? WHERE song_id=?",
        (survivor_id, loser_id),
    )
    connection.execute(
        "UPDATE song_aliases SET song_id=? WHERE song_id=?", (survivor_id, loser_id)
    )
    connection.execute(
        """
        INSERT INTO song_aliases(alias_slug, song_id) VALUES(?, ?)
        ON CONFLICT(alias_slug) DO UPDATE SET song_id=excluded.song_id
        """,
        (loser["slug"], survivor_id),
    )
    connection.execute(
        "UPDATE songs SET human_touched=1 WHERE id=?", (survivor_id,)
    )
    connection.execute("DELETE FROM songs WHERE id=?", (loser_id,))


def _split_newer_group(
    connection: sqlite3.Connection, song_id: int, disambig: str
) -> int:
    if not disambig:
        raise ValueError("disambig cannot be blank")
    song = connection.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
    if song is None:
        raise ValueError("song no longer exists")
    dated = connection.execute(
        """
        SELECT id, bounce_date FROM bounces
        WHERE song_id=? AND bounce_date IS NOT NULL
        ORDER BY bounce_date, id
        """,
        (song_id,),
    ).fetchall()
    if len(dated) < 2:
        raise ValueError("need at least two dated bounces to split")
    split_index = len(dated) // 2
    largest_gap = -1
    for index in range(1, len(dated)):
        gap = (
            date.fromisoformat(str(dated[index]["bounce_date"]))
            - date.fromisoformat(str(dated[index - 1]["bounce_date"]))
        ).days
        if gap > largest_gap:
            largest_gap = gap
            split_index = index
    cursor = connection.execute(
        """
        INSERT INTO songs(
          public_id, slug, disambig, title, status, keeper, key_canon, key_camelot,
          key_source, bpm, bpm_source, energy, era_id, first_date, last_date,
          notes, human_touched
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            new_ulid(),
            song["slug"],
            disambig,
            song["title"],
            song["status"],
            song["keeper"],
            song["key_canon"],
            song["key_camelot"],
            song["key_source"],
            song["bpm"],
            song["bpm_source"],
            song["energy"],
            song["era_id"],
            dated[split_index]["bounce_date"],
            dated[-1]["bounce_date"],
            song["notes"],
        ),
    )
    new_song_id = int(cursor.lastrowid)
    newer_ids = [int(row["id"]) for row in dated[split_index:]]
    connection.executemany(
        "UPDATE bounces SET song_id=? WHERE id=?",
        [(new_song_id, bounce_id) for bounce_id in newer_ids],
    )
    connection.execute(
        "UPDATE songs SET last_date=?, human_touched=1 WHERE id=?",
        (dated[split_index - 1]["bounce_date"], song_id),
    )
    return new_song_id


def _payload(item: sqlite3.Row) -> dict[str, object]:
    try:
        value = json.loads(item["payload"])
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _review_specific(
    connection: sqlite3.Connection,
    config: Config,
    item: sqlite3.Row,
    ask: Callable[[str], str],
) -> tuple[bool, bool]:
    """Handle a non-unparsed item; return (handled, quit)."""
    kind = str(item["kind"])
    payload = _payload(item)
    if kind == "merge_suggestion":
        songs = payload.get("songs")
        pair = songs if isinstance(songs, list) else []
        ids = [
            int(value["id"])
            for value in pair
            if isinstance(value, dict) and isinstance(value.get("id"), int)
        ]
        action = ask("[a]play A [b]play B [m]erge [k]eep separate [s]kip [q]uit: ").casefold()
        if action in {"a", "b"} and len(ids) == 2:
            _play_song(config, connection, ids[0 if action == "a" else 1])
            return False, False
        if action == "m" and len(ids) == 2:
            chosen = ask(f"survivor id [{ids[0]}]: ").strip()
            survivor = int(chosen) if chosen else ids[0]
            if survivor not in ids:
                raise ValueError("survivor must be one of the suggested song ids")
            loser = ids[1] if survivor == ids[0] else ids[0]
            _merge_songs(connection, survivor, loser)
            _resolve_item(
                connection,
                int(item["id"]),
                {"action": "merge", "survivor": survivor, "loser": loser},
            )
            return True, False
        if action == "k":
            _resolve_item(connection, int(item["id"]), {"action": "keep_separate"})
            return True, False
        return False, action == "q"

    if kind == "possible_distinct":
        action = ask("[s]plit newer group [k]eep together [i]gnore [q]uit: ").casefold()
        if action == "s" and item["song_id"] is not None:
            disambig = ask("disambig for newer group: ").strip()
            new_id = _split_newer_group(connection, int(item["song_id"]), disambig)
            _resolve_item(
                connection,
                int(item["id"]),
                {"action": "split", "new_song_id": new_id, "disambig": disambig},
            )
            return True, False
        if action == "k":
            connection.execute(
                "UPDATE songs SET human_touched=1 WHERE id=?", (item["song_id"],)
            )
            _resolve_item(connection, int(item["id"]), {"action": "keep_together"})
            return True, False
        if action == "i":
            _resolve_item(connection, int(item["id"]), {"action": "ignored"}, ignored=True)
            return True, False
        return False, action == "q"

    if kind == "twin_mismatch":
        action = ask("[t]reat as twins [k]eep separate [i]gnore [q]uit: ").casefold()
        if action == "t":
            relpaths = payload.get("files")
            rows = (
                connection.execute(
                    "SELECT id, bounce_id FROM files WHERE relpath IN (?, ?)",
                    tuple(relpaths),
                ).fetchall()
                if isinstance(relpaths, list) and len(relpaths) == 2
                else []
            )
            bounce_ids = sorted(
                {int(row["bounce_id"]) for row in rows if row["bounce_id"] is not None}
            )
            if len(bounce_ids) == 2:
                connection.execute(
                    "UPDATE files SET bounce_id=? WHERE bounce_id=?",
                    (bounce_ids[0], bounce_ids[1]),
                )
                connection.execute("DELETE FROM bounces WHERE id=?", (bounce_ids[1],))
            _resolve_item(connection, int(item["id"]), {"action": "twin"})
            return True, False
        if action == "k":
            _resolve_item(connection, int(item["id"]), {"action": "keep_separate"})
            return True, False
        if action == "i":
            _resolve_item(connection, int(item["id"]), {"action": "ignored"}, ignored=True)
            return True, False
        return False, action == "q"

    if kind == "key_conflict":
        action = ask("[p]ick key [i]gnore [q]uit: ").casefold()
        if action == "p" and item["song_id"] is not None:
            raw = ask("key (or none): ").strip()
            canon, camelot = normalize_key(raw, load_keymap(config.keymap_path))
            if canon is None:
                raise ValueError(f"invalid key: {raw}")
            connection.execute(
                """
                UPDATE songs SET key_canon=?, key_camelot=?, key_source='human',
                  human_touched=1 WHERE id=?
                """,
                (canon, camelot, item["song_id"]),
            )
            _resolve_item(connection, int(item["id"]), {"action": "pick", "key": canon})
            return True, False
        if action == "i":
            _resolve_item(connection, int(item["id"]), {"action": "ignored"}, ignored=True)
            return True, False
        return False, action == "q"

    if kind == "date_suspect":
        action = ask("[a]ccept [e]dit date [i]gnore [q]uit: ").casefold()
        if action in {"a", "e"} and item["file_id"] is not None:
            value = (
                ask("date YYYY-MM-DD: ").strip()
                if action == "e"
                else str(payload.get("bounce_date") or "")
            )
            connection.execute(
                """
                UPDATE bounces SET bounce_date=?, date_source='human', date_suspect=0
                WHERE id=(SELECT bounce_id FROM files WHERE id=?)
                """,
                (value or None, item["file_id"]),
            )
            if item["song_id"] is not None:
                connection.execute(
                    "UPDATE songs SET human_touched=1 WHERE id=?", (item["song_id"],)
                )
            _resolve_item(connection, int(item["id"]), {"action": action, "date": value})
            return True, False
        if action == "i":
            _resolve_item(connection, int(item["id"]), {"action": "ignored"}, ignored=True)
            return True, False
        return False, action == "q"

    if kind == "project_link":
        action = ask("[l]ink project [i]gnore [q]uit: ").casefold()
        project_id = payload.get("project_id")
        if action == "l" and item["song_id"] is not None and isinstance(project_id, int):
            connection.execute(
                """
                INSERT INTO song_projects(song_id, project_id, method)
                VALUES(?, ?, 'human')
                ON CONFLICT(song_id, project_id) DO UPDATE SET method='human'
                """,
                (item["song_id"], project_id),
            )
            connection.execute(
                "UPDATE songs SET human_touched=1 WHERE id=?", (item["song_id"],)
            )
            _resolve_item(connection, int(item["id"]), {"action": "link"})
            return True, False
        if action == "i":
            _resolve_item(connection, int(item["id"]), {"action": "ignored"}, ignored=True)
            return True, False
        return False, action == "q"

    if kind == "stray_location":
        action = ask("[h]record add-dir config hint [i]gnore [q]uit: ").casefold()
        if action == "h":
            _resolve_item(
                connection,
                int(item["id"]),
                {"action": "config_hint", "directory": payload.get("directory")},
            )
            return True, False
        if action == "i":
            _resolve_item(connection, int(item["id"]), {"action": "ignored"}, ignored=True)
            return True, False
        return False, action == "q"

    action = ask("[r]esolve [i]gnore [s]kip [q]uit: ").casefold()
    if action == "r":
        detail = ask("resolution note: ").strip()
        _resolve_item(
            connection,
            int(item["id"]),
            {"action": "resolved", "note": detail},
        )
        return True, False
    if action == "i":
        _resolve_item(connection, int(item["id"]), {"action": "ignored"}, ignored=True)
        return True, False
    return False, action == "q"


def _assign_unparsed(
    connection: sqlite3.Connection,
    config: Config,
    item: sqlite3.Row,
    ask: Callable[[str], str],
) -> bool:
    title = ask("title (blank=skip): ").strip()
    if not title:
        return False
    key_raw = ask("key (blank=none): ").strip()
    date_value = ask("date YYYY-MM-DD (blank=none): ").strip() or None
    collab = ask("collab (blank=none): ").strip().casefold() or None
    keymap = load_keymap(config.keymap_path)
    canon, camelot = normalize_key(key_raw, keymap) if key_raw else (None, None)
    if key_raw and canon is None:
        raise ValueError(f"invalid key: {key_raw}")
    file_row = connection.execute(
        "SELECT relpath FROM files WHERE id=?", (item["file_id"],)
    ).fetchone()
    if file_row is None:
        raise ValueError("review file no longer exists")
    slug = slugify(title)
    song = connection.execute(
        "SELECT id FROM songs WHERE slug=? AND disambig=''", (slug,)
    ).fetchone()
    if song is None:
        cursor = connection.execute(
            """
            INSERT INTO songs(
              public_id, slug, title, key_canon, key_camelot, key_source,
              human_touched
            ) VALUES(?, ?, ?, ?, ?, ?, 1)
            """,
            (
                new_ulid(),
                slug,
                display_title([title]),
                canon,
                camelot,
                "human" if canon else None,
            ),
        )
        song_id = int(cursor.lastrowid)
    else:
        song_id = int(song["id"])
        connection.execute(
            "UPDATE songs SET human_touched=1 WHERE id=?", (song_id,)
        )
    stem = Path(str(file_row["relpath"])).stem
    cursor = connection.execute(
        """
        INSERT INTO bounces(
          public_id, song_id, source_stem, bounce_date, date_source, collab_raw
        )
        VALUES(?, ?, ?, ?, 'human', ?)
        ON CONFLICT(song_id, source_stem) DO UPDATE SET
          bounce_date=excluded.bounce_date, date_source='human',
          collab_raw=excluded.collab_raw
        RETURNING id
        """,
        (new_ulid(), song_id, stem, date_value, collab),
    )
    bounce_id = int(cursor.fetchone()["id"])
    connection.execute(
        "UPDATE files SET bounce_id=?, parse_status='assigned' WHERE id=?",
        (bounce_id, item["file_id"]),
    )
    if collab:
        connection.execute(
            """
            INSERT OR IGNORE INTO song_tags(
              song_id, dim, value, source, author, created_at
            ) VALUES(?, 'collab', ?, 'human', ?, ?)
            """,
            (song_id, collab, os.environ.get("USER"), utc_now()),
        )
    _resolve_item(connection, int(item["id"]), {"action": "assigned", "song_id": song_id})
    return True


def review_loop(
    connection: sqlite3.Connection,
    config: Config,
    *,
    ask: Callable[[str], str] = input,
    tell: Callable[[str], object] = print,
) -> int:
    handled = 0
    active_kind: str | None = None
    while True:
        if active_kind is None:
            oldest = connection.execute(
                """
                SELECT kind FROM review_queue WHERE status='open'
                ORDER BY created_at, id LIMIT 1
                """
            ).fetchone()
            if oldest is None:
                tell("review queue is empty")
                break
            active_kind = str(oldest["kind"])
        item = connection.execute(
            """
            SELECT * FROM review_queue WHERE status='open' AND kind=?
            ORDER BY created_at, id LIMIT 1
            """,
            (active_kind,),
        ).fetchone()
        if item is None:
            active_kind = None
            continue
        tell(f"\n#{item['id']} {item['kind']}")
        try:
            tell(json.dumps(json.loads(item["payload"]), indent=2, sort_keys=True))
        except (TypeError, json.JSONDecodeError):
            tell(str(item["payload"]))

        kind = str(item["kind"])
        if kind == "unparsed_name":
            action = ask("[a]ssign [p]lay [i]gnore [s]kip [q]uit: ").casefold()
            if action == "p":
                _play(config, connection, item["file_id"])
                continue
            if action == "a":
                with transaction(connection):
                    if _assign_unparsed(connection, config, item, ask):
                        handled += 1
                continue
        else:
            with transaction(connection):
                item_handled, should_quit = _review_specific(
                    connection, config, item, ask
                )
            if item_handled:
                handled += 1
                continue
            if should_quit:
                break
            # Playback or skip leaves the item open. Playback loops; skip stops the group.
            active_kind = None
            if kind == "merge_suggestion":
                continue
            break
        if action == "i":
            with transaction(connection):
                _resolve_item(connection, int(item["id"]), {"action": "ignored"}, ignored=True)
            handled += 1
        elif action == "q":
            break
        else:
            break
    return handled
