"""Catalog verification checks and coverage reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import json
import sqlite3

from .config import Config
from .mirror import SENTINEL, mirror_expectations, stem_mirror_expectations
from .scan import _walk


@dataclass(frozen=True)
class Coverage:
    total: int
    released: frozenset[int]
    status: frozenset[int]
    key: frozenset[int]
    vibe: frozenset[int]
    instr: frozenset[int]
    collab: frozenset[int]
    gaps: dict[int, tuple[str, ...]]

    def percent(self, dimension: str) -> float:
        if self.total == 0:
            return 100.0
        return 100.0 * len(getattr(self, dimension)) / self.total


@dataclass(frozen=True)
class VerifyResult:
    exit_code: int
    output: str
    report_path: Path
    findings: tuple[str, ...]


def coverage_snapshot(connection: sqlite3.Connection) -> Coverage:
    song_rows = connection.execute(
        "SELECT id, status, human_touched, key_canon FROM songs ORDER BY id"
    ).fetchall()
    tags: dict[tuple[int, str], set[str]] = {}
    for row in connection.execute("SELECT song_id, dim, value FROM song_tags"):
        tags.setdefault((int(row["song_id"]), str(row["dim"])), set()).add(
            str(row["value"])
        )
    status: set[int] = set()
    key: set[int] = set()
    vibe: set[int] = set()
    instr: set[int] = set()
    collab: set[int] = set()
    released: set[int] = set()
    gaps: dict[int, tuple[str, ...]] = {}
    for row in song_rows:
        song_id = int(row["id"])
        if row["status"] == "released":
            released.add(song_id)
            status.add(song_id)
            key.add(song_id)
            vibe.add(song_id)
            instr.add(song_id)
            collab.add(song_id)
            continue
        missing: list[str] = []
        if int(row["human_touched"]) or row["status"] != "demo":
            status.add(song_id)
        else:
            missing.append("status")
        if row["key_canon"] is not None:
            key.add(song_id)
        else:
            missing.append("key")
        if tags.get((song_id, "vibe")):
            vibe.add(song_id)
        else:
            missing.append("vibe")
        if tags.get((song_id, "instr")):
            instr.add(song_id)
        else:
            missing.append("instr")
        if tags.get((song_id, "collab")):
            collab.add(song_id)
        else:
            missing.append("collab")
        if missing:
            gaps[song_id] = tuple(missing)
    return Coverage(
        total=len(song_rows),
        released=frozenset(released),
        status=frozenset(status),
        key=frozenset(key),
        vibe=frozenset(vibe),
        instr=frozenset(instr),
        collab=frozenset(collab),
        gaps=gaps,
    )


def _disk_catalog_findings(
    connection: sqlite3.Connection, config: Config
) -> tuple[list[str], str | None]:
    last_scan = connection.execute(
        "SELECT started, notes FROM runs WHERE kind='scan' AND ok=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last_scan is None:
        return ["V1: no successful scan exists"], None
    scan_token = str(last_scan["started"])
    candidates, present, _, _, _ = _walk(config)
    del candidates
    db_rows = connection.execute(
        "SELECT relpath, last_seen, missing_since FROM files"
    ).fetchall()
    by_path = {str(row["relpath"]): row for row in db_rows}
    findings: list[str] = []
    for relpath in sorted(present):
        row = by_path.get(relpath)
        if row is None:
            findings.append(f"V1: on disk but not cataloged: {relpath}")
        elif row["last_seen"] != scan_token:
            findings.append(f"V1: stale catalog row: {relpath}")
    for relpath, row in sorted(by_path.items()):
        if row["missing_since"] is None and relpath not in present:
            findings.append(f"V1: cataloged but absent: {relpath}")
    return findings, scan_token


def _entity_findings(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT f.id, f.relpath, f.parse_status, f.bounce_id, b.song_id
        FROM files AS f
        LEFT JOIN bounces AS b ON b.id=f.bounce_id
        WHERE f.layer='curated' AND f.missing_since IS NULL
        ORDER BY f.relpath
        """
    ).fetchall()
    findings: list[str] = []
    for row in rows:
        status = str(row["parse_status"])
        has_review = connection.execute(
            """
            SELECT 1 FROM review_queue
            WHERE kind='unparsed_name' AND file_id=? AND status='open'
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        if status == "na":
            findings.append(f"V3: curated file incorrectly marked na: {row['relpath']}")
        elif status in {"parsed", "assigned"}:
            if row["bounce_id"] is None or row["song_id"] is None:
                findings.append(f"V3: entity chain incomplete: {row['relpath']}")
        elif has_review is None:
            findings.append(f"V3: no entity or unparsed review: {row['relpath']}")
    return findings


def _mirror_findings(
    connection: sqlite3.Connection, config: Config
) -> tuple[list[str], list[str], list[str], bool]:
    root = config.state_dir / "mirror"
    sentinel = root / SENTINEL
    mirror_count = int(
        connection.execute("SELECT COUNT(*) FROM mirror_files").fetchone()[0]
    )
    stem_count = int(connection.execute("SELECT COUNT(*) FROM stems").fetchone()[0])
    if not sentinel.is_file() and mirror_count == 0 and stem_count == 0:
        return [], [], [], False
    v5: list[str] = []
    v9: list[str] = []
    v9_notes: list[str] = []
    try:
        # A bounce whose source file is unreadable THIS INSTANT (an rsync
        # mid-swap, a corpus mid-copy) is skipped by mirror_expectations on
        # the explicit theory that the file has not arrived rather than gone.
        # The verifier must honour the same theory: a skipped bounce's
        # already-built artifacts are neither orphans nor missing — they are
        # in flight. Two nightlies went red on exactly this.
        projection = mirror_expectations(connection, config)
        expected = projection.items
        skipped_sources = projection.skipped
        expected_stems = stem_mirror_expectations(connection, config)
    except (OSError, ValueError) as exc:
        return [f"V5: cannot compute mirror expectations: {exc}"], [], [], True
    if skipped_sources:
        in_flight = connection.execute(
            """
            SELECT b.id AS bounce_id, b.public_id AS bounce_public_id,
                   s.public_id AS song_public_id, mf.mirror_relpath
            FROM bounces AS b
            JOIN songs AS s ON s.id=b.song_id
            LEFT JOIN mirror_files AS mf ON mf.bounce_id=b.id
            WHERE b.id IN ({})
            """.format(",".join("?" for _ in skipped_sources)),
            tuple(skipped_sources),
        ).fetchall()
    else:
        in_flight = []
    counter_prefix = "in_flight_runs:"
    prior_counts = {
        int(str(row["key"]).removeprefix(counter_prefix)): int(row["value"])
        for row in connection.execute(
            "SELECT key, value FROM build_state WHERE key LIKE 'in_flight_runs:%'"
        )
    }
    skipped_ids = set(skipped_sources)
    for bounce_id in prior_counts.keys() - skipped_ids:
        connection.execute(
            "DELETE FROM build_state WHERE key=?",
            (f"{counter_prefix}{bounce_id}",),
        )
    for bounce_id in skipped_sources:
        consecutive = prior_counts.get(bounce_id, 0) + 1
        connection.execute(
            """
            INSERT INTO build_state(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (f"{counter_prefix}{bounce_id}", str(consecutive)),
        )
        v9_notes.append(
            f"V9: bounce {bounce_id} source unreadable (in flight)"
        )
        if consecutive >= 3:
            v5.append(
                f"V5: bounce {bounce_id} source unreadable for "
                f"{consecutive} consecutive runs"
            )
    rows = {
        int(row["bounce_id"]): row
        for row in connection.execute("SELECT * FROM mirror_files")
    }
    orphan_allowlist_tracks: set[str] = set()
    orphan_allowlist_peaks: set[str] = set()
    orphan_allowlist_art: set[str] = set()
    for item in expected:
        orphan_allowlist_tracks.add(item.mirror_relpath)
        orphan_allowlist_peaks.add(f"peaks/{item.bounce_public_id}.json")
        orphan_allowlist_art.add(f"art/{item.song_public_id}.jpg")
        row = rows.get(item.bounce_id)
        if row is None:
            v5.append(f"V5: bounce {item.bounce_id} has no mirror_files row")
            continue
        if row["src_sha256"] != item.src_sha256:
            v5.append(f"V5: bounce {item.bounce_id} source hash is stale")
        if row["tag_hash"] != item.tag_hash:
            v5.append(f"V5: bounce {item.bounce_id} tag hash is stale")
        if row["encoder_settings"] != item.encoder_settings:
            v5.append(f"V5: bounce {item.bounce_id} encoder settings are stale")
        for relpath in (
            item.mirror_relpath,
            f"peaks/{item.bounce_public_id}.json",
            f"art/{item.song_public_id}.jpg",
        ):
            if not (root / relpath).is_file():
                v5.append(f"V5: missing mirror artifact: {relpath}")
    stem_rows = {
        int(row["id"]): row
        for row in connection.execute(
            "SELECT id, mirror_relpath, built_at FROM stems"
        )
    }
    stem_tag_hashes = {
        int(str(row["key"]).removeprefix("stem_tag_hash:")): str(row["value"])
        for row in connection.execute(
            "SELECT key, value FROM build_state WHERE key LIKE 'stem_tag_hash:%'"
        )
    }
    for item in expected_stems:
        orphan_allowlist_tracks.add(item.mirror_relpath)
        orphan_allowlist_peaks.add(f"peaks/{item.stem_public_id}.json")
        orphan_allowlist_art.add(f"art/{item.song_public_id}.jpg")
        row = stem_rows[item.stem_id]
        if row["mirror_relpath"] != item.mirror_relpath:
            v5.append(f"V5: stem {item.stem_id} mirror path is stale")
        if row["built_at"] is None:
            v5.append(f"V5: stem {item.stem_id} has not been mirrored")
        if stem_tag_hashes.get(item.stem_id) != item.tag_hash:
            v5.append(f"V5: stem {item.stem_id} tag hash is stale")
        for relpath in (
            item.mirror_relpath,
            f"peaks/{item.stem_public_id}.json",
            f"art/{item.song_public_id}.jpg",
        ):
            if not (root / relpath).is_file():
                v5.append(f"V5: missing mirror artifact: {relpath}")
    cutoff = datetime.now(UTC) - timedelta(days=30)
    retained = connection.execute(
        """
        SELECT mf.mirror_relpath, b.public_id AS bounce_public_id,
               s.public_id AS song_public_id,
               MAX(f.missing_since) AS missing_since,
               SUM(CASE WHEN f.missing_since IS NULL THEN 1 ELSE 0 END) AS active
        FROM mirror_files AS mf
        JOIN bounces AS b ON b.id=mf.bounce_id
        JOIN songs AS s ON s.id=b.song_id
        LEFT JOIN files AS f ON f.bounce_id=b.id AND f.layer='curated'
        GROUP BY mf.bounce_id
        """
    ).fetchall()
    for row in retained:
        if int(row["active"] or 0) or not row["missing_since"]:
            continue
        try:
            missing = datetime.fromisoformat(str(row["missing_since"]))
            if missing.tzinfo is None:
                missing = missing.replace(tzinfo=UTC)
            else:
                missing = missing.astimezone(UTC)
        except ValueError:
            continue
        if missing >= cutoff:
            orphan_allowlist_tracks.add(str(row["mirror_relpath"]))
            orphan_allowlist_peaks.add(f"peaks/{row['bounce_public_id']}.json")
            orphan_allowlist_art.add(f"art/{row['song_public_id']}.jpg")
    for row in in_flight:
        # In-flight bounces are exempt in BOTH directions: their artifacts
        # may exist (built before the file went briefly unreadable) or not
        # (never built) — neither state is a finding while the source is
        # mid-copy.
        if row["mirror_relpath"]:
            orphan_allowlist_tracks.add(str(row["mirror_relpath"]))
        orphan_allowlist_peaks.add(f"peaks/{row['bounce_public_id']}.json")
        orphan_allowlist_art.add(f"art/{row['song_public_id']}.jpg")
    actual_tracks = {
        path.relative_to(root).as_posix()
        for path in (root / "tracks").glob("*.mp3")
        if path.is_file()
    }
    actual_peaks = {
        path.relative_to(root).as_posix()
        for path in (root / "peaks").glob("*.json")
        if path.is_file()
    }
    actual_art = {
        path.relative_to(root).as_posix()
        for path in (root / "art").glob("*.jpg")
        if path.is_file()
    }
    for relpath in sorted(
        (actual_tracks - orphan_allowlist_tracks)
        | (actual_peaks - orphan_allowlist_peaks)
        | (actual_art - orphan_allowlist_art)
    ):
        v5.append(f"V5: orphan mirror artifact: {relpath}")
    temporary = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.tmp.*")
        if path.is_file()
    )
    for relpath in temporary:
        v5.append(f"V5: temporary mirror artifact remains: {relpath}")
        v9.append(f"V9: temporary mirror artifact remains: {relpath}")
    last_good = connection.execute(
        "SELECT value FROM build_state WHERE key='last_good_count'"
    ).fetchone()
    if last_good is None:
        v9.append("V9: last_good_count is missing")
    elif (len(expected) + len(skipped_sources)) * 10 < int(last_good["value"]) * 9:
        v9.append(
            f"V9: current count {len(expected) + len(skipped_sources)} "
            "is below 90% of "
            f"last-known-good {last_good['value']}"
        )
    if not sentinel.is_file():
        v9.append(f"V9: mirror sentinel {SENTINEL} is missing")
    return v5, v9, v9_notes, True


def run_verify(
    connection: sqlite3.Connection,
    config: Config,
    *,
    strict: bool = False,
    today: date | None = None,
) -> VerifyResult:
    today_value = today or date.today()
    findings: list[str] = []
    lines = ["cr8 verify", "============", ""]

    v1, scan_token = _disk_catalog_findings(connection, config)
    findings.extend(v1)
    lines.append(f"V1 disk↔catalog: {'PASS' if not v1 else f'FAIL ({len(v1)})'}")
    if scan_token:
        lines.append(f"  scan token: {scan_token}")
    lines.extend(f"  {item}" for item in v1)

    stray_rows = connection.execute(
        """
        SELECT payload FROM review_queue
        WHERE kind='stray_location' AND status='open'
        ORDER BY payload
        """
    ).fetchall()
    if stray_rows:
        stray = [
            json.loads(row["payload"]).get("directory", row["payload"])
            for row in stray_rows
        ]
        findings.extend(f"V2: unclassified directory: {value}" for value in stray)
        lines.append(f"V2 unclassified locations: FAIL ({len(stray)})")
        lines.extend(f"  {value}" for value in stray)
    else:
        lines.append("V2 unclassified locations: PASS")

    v3 = _entity_findings(connection)
    findings.extend(v3)
    lines.append(f"V3 entity closure: {'PASS' if not v3 else f'FAIL ({len(v3)})'}")
    lines.extend(f"  {item}" for item in v3)

    coverage = coverage_snapshot(connection)
    lines.append("V4 dimension coverage:")
    for dimension in ("status", "key", "vibe", "instr", "collab"):
        count = len(getattr(coverage, dimension))
        lines.append(
            f"  {dimension:<7} {count:>4}/{coverage.total:<4} "
            f"{coverage.percent(dimension):6.1f}%"
        )
    lines.append(f"  released {len(coverage.released):>4} archived")
    if coverage.gaps:
        lines.append("  exact gaps:")
        song_rows = {
            int(row["id"]): (str(row["slug"]), str(row["disambig"]))
            for row in connection.execute("SELECT id, slug, disambig FROM songs")
        }
        for song_id, dimensions in coverage.gaps.items():
            slug, disambig = song_rows[song_id]
            label = f"{slug}:{disambig}" if disambig else slug
            lines.append(f"    {song_id} {label}: {', '.join(dimensions)}")
            if strict:
                findings.append(
                    f"V4: {label} missing {', '.join(dimensions)}"
                )
    else:
        lines.append("  no gaps")

    v5, v9, v9_notes, mirror_active = _mirror_findings(connection, config)
    if mirror_active:
        findings.extend(v5)
        lines.append(
            f"V5 mirror integrity: {'PASS' if not v5 else f'FAIL ({len(v5)})'}"
        )
        lines.extend(f"  {item}" for item in v5)
    else:
        lines.append("V5 mirror integrity: SKIP (mirror not built yet)")
    lines.append("V6 remote integrity: SKIP (remote phase not built yet)")

    cutoff = datetime.combine(
        today_value - timedelta(days=14), datetime.min.time(), tzinfo=UTC
    )
    stale: list[sqlite3.Row] = []
    for row in connection.execute(
        """
        SELECT id, kind, created_at FROM review_queue
        WHERE status='open' ORDER BY created_at, id
        """
    ):
        try:
            created = datetime.fromisoformat(str(row["created_at"]))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            continue
        if created < cutoff:
            stale.append(row)
    lines.append(f"V7 review SLA: {'PASS' if not stale else f'FINDINGS ({len(stale)})'}")
    for row in stale:
        item = f"V7: review #{row['id']} {row['kind']} opened {row['created_at']}"
        findings.append(item)
        lines.append(f"  {item}")
    lines.append("V8 backup integrity: SKIP (backup phase not built yet)")
    if mirror_active:
        findings.extend(v9)
        lines.append(
            f"V9 build state: {'PASS' if not v9 else f'FAIL ({len(v9)})'}"
        )
        lines.extend(f"  {item}" for item in v9)
        lines.extend(f"  {item}" for item in v9_notes)
    else:
        lines.append("V9 build state: SKIP (mirror not built yet)")

    report_dir = config.state_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"coverage-{today_value.isoformat()}.md"
    markdown_lines = [
        f"# cr8 coverage — {today_value.isoformat()}",
        "",
        "```text",
        *lines,
        "```",
        "",
    ]
    report_path.write_text("\n".join(markdown_lines), encoding="utf-8")
    output = "\n".join(lines)
    return VerifyResult(
        exit_code=1 if findings else 0,
        output=output,
        report_path=report_path,
        findings=tuple(findings),
    )
