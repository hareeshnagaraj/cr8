"""Rotating SHA-256 integrity scrub for immutable source audio."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sqlite3

from .audio import sha256_file
from .config import Config
from .db import transaction, utc_now
from .tooling import find_tool, run_tool
from .paths import source_path


@dataclass(frozen=True)
class ScrubSummary:
    bucket: int
    checked: int
    anchored: int
    mismatches: tuple[str, ...]
    report_path: Path

    @property
    def exit_code(self) -> int:
        return 1 if self.mismatches else 0


def scrub(
    connection: sqlite3.Connection,
    config: Config,
    *,
    bucket: int | None = None,
    today: date | None = None,
    notify: bool = True,
) -> ScrubSummary:
    if bucket is None:
        row = connection.execute(
            "SELECT value FROM build_state WHERE key='scrub_next_bucket'"
        ).fetchone()
        selected = int(row["value"]) % 8 if row is not None else 0
    else:
        if not 0 <= bucket <= 7:
            raise ValueError("scrub bucket must be between 0 and 7")
        selected = bucket
    rows = connection.execute(
        """
        SELECT id, relpath, sha256
        FROM files
        WHERE layer IN ('curated','project') AND missing_since IS NULL
          AND (id % 8)=?
        ORDER BY id
        """,
        (selected,),
    ).fetchall()
    anchors: list[tuple[str, int]] = []
    mismatches: list[str] = []
    checked = 0
    for row in rows:
        path = source_path(config, str(row["relpath"]))
        try:
            actual = sha256_file(path)
        except OSError as exc:
            mismatches.append(f"{row['relpath']}: unreadable ({exc})")
            continue
        checked += 1
        expected = str(row["sha256"]) if row["sha256"] else None
        if expected is None:
            anchors.append((actual, int(row["id"])))
        elif actual != expected:
            mismatches.append(
                f"{row['relpath']}: sha256 changed ({expected} -> {actual})"
            )
    with transaction(connection):
        connection.executemany(
            "UPDATE files SET sha256=? WHERE id=? AND sha256 IS NULL", anchors
        )
        connection.execute(
            """
            INSERT INTO build_state(key, value) VALUES('scrub_next_bucket', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str((selected + 1) % 8),),
        )
        connection.execute(
            """
            INSERT INTO build_state(key, value) VALUES('last_scrub_at', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (utc_now(),),
        )
    report_dir = config.state_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"scrub-{(today or date.today()).isoformat()}.md"
    lines = [
        f"# cr8 scrub — {(today or date.today()).isoformat()}",
        "",
        f"- Bucket: {selected}/7",
        f"- Checked: {checked}",
        f"- New anchors: {len(anchors)}",
        f"- Critical mismatches: {len(mismatches)}",
        "",
    ]
    if mismatches:
        lines.extend(["## CRITICAL", "", *(f"- {item}" for item in mismatches), ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    if mismatches and notify:
        osascript = find_tool("osascript", state_dir=config.state_dir)
        if osascript is not None:
            run_tool(
                osascript,
                (
                    "-e",
                    f'display notification "cr8 found {len(mismatches)} source '
                    'hash mismatch(es). See the scrub report." '
                    'with title "cr8 CRITICAL"',
                ),
                timeout=15,
            )
    return ScrubSummary(
        bucket=selected,
        checked=checked,
        anchored=len(anchors),
        mismatches=tuple(mismatches),
        report_path=report_path,
    )
