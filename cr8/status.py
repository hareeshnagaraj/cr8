"""One-screen catalog status."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from .verify import coverage_snapshot


def render_status(connection: sqlite3.Connection, db_path: Path) -> str:
    lines = ["cr8 status", "============"]
    rows = connection.execute(
        """
        SELECT layer, parse_status, COUNT(*) AS count
        FROM files WHERE missing_since IS NULL
        GROUP BY layer, parse_status ORDER BY layer, parse_status
        """
    ).fetchall()
    total_files = sum(int(row["count"]) for row in rows)
    lines.append(f"audio files: {total_files:,}")
    for row in rows:
        lines.append(
            f"  {row['layer']:<7} {row['parse_status']:<8} {int(row['count']):>7,}"
        )
    curated_total = int(
        connection.execute(
            "SELECT COUNT(*) FROM files WHERE layer='curated' AND missing_since IS NULL"
        ).fetchone()[0]
    )
    curated_parsed = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM files
            WHERE layer='curated' AND missing_since IS NULL
              AND parse_status IN ('parsed','assigned')
            """
        ).fetchone()[0]
    )
    parse_rate = 100.0 * curated_parsed / curated_total if curated_total else 100.0
    songs = int(connection.execute("SELECT COUNT(*) FROM songs").fetchone()[0])
    bounces = int(connection.execute("SELECT COUNT(*) FROM bounces").fetchone()[0])
    unparsed = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM review_queue
            WHERE kind='unparsed_name' AND status='open'
            """
        ).fetchone()[0]
    )
    lines.extend(
        [
            f"curated: {curated_total:,}",
            f"curated parse rate: {parse_rate:.1f}%",
            f"songs: {songs:,}",
            f"bounces: {bounces:,}",
            f"open unparsed: {unparsed:,}",
            "open reviews:",
        ]
    )
    review_rows = connection.execute(
        """
        SELECT kind, COUNT(*) AS count FROM review_queue
        WHERE status='open' GROUP BY kind ORDER BY kind
        """
    ).fetchall()
    if review_rows:
        lines.extend(
            f"  {row['kind']:<20} {int(row['count']):>6,}" for row in review_rows
        )
    else:
        lines.append("  none")
    coverage = coverage_snapshot(connection)
    lines.append("dimension coverage:")
    for dimension in ("status", "key", "vibe", "instr", "collab"):
        lines.append(f"  {dimension:<7} {coverage.percent(dimension):6.1f}%")
    last_scan = connection.execute(
        "SELECT finished FROM runs WHERE kind='scan' AND ok=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    lines.append(f"last scan: {last_scan['finished'] if last_scan else 'never'}")
    try:
        size = db_path.stat().st_size
    except OSError:
        size = 0
    lines.append(f"database: {size / (1024 * 1024):.1f} MiB")
    return "\n".join(lines)
