"""Read-only corpus scanner."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from typing import Iterator

from .config import Config
from .db import transaction, utc_now
from .paths import (
    ARCHIVE_PREFIX,
    DROPS_PREFIX,
    archive_relpath,
    archive_root_key,
    scan_root_key,
)
from .resolve import ResolveSummary, enqueue_review, resolve_catalog, slugify


# How much of the catalogue may vanish in one tick before the scan stops
# believing it. A share alone is wrong for a small crate, where four files are
# 10%; a floor alone is wrong for a large one, where 25 gone out of 11,000 is
# a normal afternoon of tidying. Both have to be exceeded.
MASS_MISSING_SHARE = 0.10
MASS_MISSING_FLOOR = 25


@dataclass(frozen=True)
class FileCandidate:
    path: Path
    relpath: str
    layer: str
    ext: str
    size: int
    mtime: float


@dataclass
class ProjectStats:
    relpath: str
    name: str
    als_count: int = 0
    backup_als_count: int = 0
    total_bytes: int = 0


@dataclass(frozen=True)
class ScanSummary:
    new: int
    changed: int
    unchanged: int
    skipped_debounce: int
    missing: int
    files_seen: int
    resolve: ResolveSummary
    run_id: int
    touched_relpaths: tuple[str, ...]
    touched_bounce_ids: tuple[int, ...]
    # Non-zero when a mass disappearance was refused: how many files the scan
    # declined to mark missing. Callers report it; nothing acts on it.
    missing_guarded: int = 0
    root_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WalkResult:
    candidates: list[FileCandidate]
    present_audio: set[str]
    stray_dirs: set[str]
    skipped_counts: dict[str, Counter[str]]
    project_stats: dict[str, ProjectStats]
    available_archive_roots: frozenset[str]
    missing_archive_roots: tuple[tuple[str, Path], ...]


def _classify(
    config: Config,
    relpath: Path,
    project_relpath: str | None,
) -> tuple[str, str | None]:
    if project_relpath is not None:
        return "project", None
    if len(relpath.parts) == 1:
        return "curated", None
    top = relpath.parts[0]
    if top in config.corpus.curated_dirs:
        return "curated", None
    if top in config.corpus.other_dirs:
        return "other", None
    return "other", top


def _walk_all(config: Config) -> WalkResult:
    candidates: list[FileCandidate] = []
    present_audio: set[str] = set()
    stray_dirs: set[str] = set()
    skipped_counts: dict[str, Counter[str]] = defaultdict(Counter)
    project_stats: dict[str, ProjectStats] = {}
    available_archive_roots: set[str] = set()
    missing_archive_roots: list[tuple[str, Path]] = []
    roots = [
        (config.corpus.root, False),
        *((root, True) for root in config.corpus.archive_roots),
    ]

    for root, is_archive_root in roots:
        root_key = archive_root_key(root) if is_archive_root else ""
        if not root.is_dir():
            if is_archive_root:
                missing_archive_roots.append((root_key, root))
                continue
            raise FileNotFoundError(f"corpus root is not a directory: {root}")
        if is_archive_root:
            available_archive_roots.add(root_key)
        stack: list[tuple[Path, str | None]] = [(root, None)]

        while stack:
            directory, inherited_project = stack.pop()
            rel_directory = directory.relative_to(root)
            project_relpath = inherited_project
            if rel_directory.parts and config.corpus.is_project_name(directory.name):
                project_relpath = rel_directory.as_posix()
                qualified_project = (
                    archive_relpath(root, project_relpath)
                    if is_archive_root
                    else project_relpath
                )
                project_stats.setdefault(
                    qualified_project,
                    ProjectStats(relpath=qualified_project, name=directory.name),
                )
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                path = Path(entry.path)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((path, project_relpath))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                relpath = path.relative_to(root)
                top = relpath.parts[0] if len(relpath.parts) > 1 else "."
                qualified_top = (
                    archive_root_key(root) + top if is_archive_root else top
                )

                if project_relpath is not None:
                    qualified_project = (
                        archive_relpath(root, project_relpath)
                        if is_archive_root
                        else project_relpath
                    )
                    stats = project_stats[qualified_project]
                    stats.total_bytes += int(stat.st_size)
                    if path.suffix.casefold() == ".als":
                        relative_to_project = relpath.relative_to(project_relpath)
                        if any(
                            part.casefold() == "backup"
                            for part in relative_to_project.parts
                        ):
                            stats.backup_als_count += 1
                        else:
                            stats.als_count += 1

                suffix = path.suffix.casefold()
                if entry.name == ".DS_Store":
                    skipped_counts[qualified_top]["ds_store"] += 1
                    continue
                if stat.st_size == 0 and entry.name.startswith("Icon"):
                    skipped_counts[qualified_top]["icon"] += 1
                    continue
                if suffix in {".asd", ".als", ".amxd"}:
                    skipped_counts[qualified_top][suffix.lstrip(".")] += 1
                    continue
                if suffix not in config.audio.extensions:
                    continue

                local_relpath = relpath.as_posix()
                rel_text = (
                    archive_relpath(root, local_relpath)
                    if is_archive_root
                    else local_relpath
                )
                present_audio.add(rel_text)
                layer, stray = _classify(config, relpath, project_relpath)
                if stray is not None:
                    stray_dirs.add(
                        archive_root_key(root) + stray
                        if is_archive_root
                        else stray
                    )
                candidates.append(
                    FileCandidate(
                        path=path,
                        relpath=rel_text,
                        layer=layer,
                        ext=suffix,
                        size=int(stat.st_size),
                        mtime=float(stat.st_mtime),
                    )
                )

    # Drops are a writable inbox, not an archive; they retain their existing
    # prefix and participate in the primary-root disappearance guard.
    _add_drops(config, candidates, present_audio)
    candidates.sort(key=lambda item: item.relpath)
    return WalkResult(
        candidates=candidates,
        present_audio=present_audio,
        stray_dirs=stray_dirs,
        skipped_counts=skipped_counts,
        project_stats=project_stats,
        available_archive_roots=frozenset(available_archive_roots),
        missing_archive_roots=tuple(missing_archive_roots),
    )


def _walk(
    config: Config,
) -> tuple[
    list[FileCandidate],
    set[str],
    set[str],
    dict[str, Counter[str]],
    dict[str, ProjectStats],
]:
    walked = _walk_all(config)
    return (
        walked.candidates,
        walked.present_audio,
        walked.stray_dirs,
        walked.skipped_counts,
        walked.project_stats,
    )


def _walk_curated(
    config: Config,
) -> tuple[
    list[FileCandidate],
    set[str],
    set[str],
    dict[str, Counter[str]],
    dict[str, ProjectStats],
]:
    """Walk only loose top-level audio and explicitly curated directories."""
    root = config.corpus.root
    if not root.is_dir():
        raise FileNotFoundError(f"corpus root is not a directory: {root}")
    candidates: list[FileCandidate] = []
    present_audio: set[str] = set()
    skipped_counts: dict[str, Counter[str]] = defaultdict(Counter)
    root_resolved = root.resolve()

    def add_file(path: Path, stat: os.stat_result) -> None:
        suffix = path.suffix.casefold()
        top = path.relative_to(root).parts[0]
        if path.name == ".DS_Store":
            skipped_counts[top]["ds_store"] += 1
            return
        if stat.st_size == 0 and path.name.startswith("Icon"):
            skipped_counts[top]["icon"] += 1
            return
        if suffix in {".asd", ".als", ".amxd"}:
            skipped_counts[top][suffix.lstrip(".")] += 1
            return
        if suffix not in config.audio.extensions:
            return
        relpath = path.relative_to(root).as_posix()
        present_audio.add(relpath)
        candidates.append(
            FileCandidate(
                path=path,
                relpath=relpath,
                layer="curated",
                ext=suffix,
                size=int(stat.st_size),
                mtime=float(stat.st_mtime),
            )
        )

    try:
        root_entries = list(os.scandir(root))
    except OSError:
        root_entries = []
    for entry in root_entries:
        try:
            if entry.is_file(follow_symlinks=False):
                add_file(Path(entry.path), entry.stat(follow_symlinks=False))
        except OSError:
            continue

    stack: list[Path] = []
    for relative in config.corpus.curated_dirs:
        directory = root / relative
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
            raise ValueError(f"curated directory escapes corpus root: {relative}")
        if directory.is_dir():
            stack.append(directory)
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    add_file(path, entry.stat(follow_symlinks=False))
            except OSError:
                continue

    _add_drops(config, candidates, present_audio)
    candidates.sort(key=lambda item: item.relpath)
    return candidates, present_audio, set(), skipped_counts, {}


def _add_drops(
    config: Config,
    candidates: list[FileCandidate],
    present_audio: set[str],
) -> None:
    """Pick up anything uploaded into the drops root.

    Uploads are catalogued exactly like curated files — same parse, same
    resolve, same mirror build — they just live somewhere we are allowed to
    write. The `_drops/` prefix on the relpath is what tells cr8.paths where to
    find them again.
    """
    drops_root = config.corpus.drops_root
    if drops_root is None or not drops_root.is_dir():
        return
    stack = [drops_root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            suffix = path.suffix.casefold()
            if suffix not in config.audio.extensions:
                continue
            relpath = (
                DROPS_PREFIX + path.relative_to(drops_root).as_posix()
            )
            present_audio.add(relpath)
            candidates.append(
                FileCandidate(
                    path=path,
                    relpath=relpath,
                    layer="curated",
                    ext=suffix,
                    size=int(stat.st_size),
                    mtime=float(stat.st_mtime),
                )
            )


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _duration(path: Path) -> float | None:
    executable = (
        Path("/opt/homebrew/bin/ffprobe")
        if Path("/opt/homebrew/bin/ffprobe").is_file()
        else Path("ffprobe")
    )
    try:
        result = subprocess.run(
            [
                str(executable),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _chunks(items: list[tuple[object, ...]], size: int = 500) -> Iterator[list[tuple[object, ...]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _project_name_parts(name: str, mtime: float | None = None) -> tuple[str, str | None]:
    cleaned = name
    if cleaned.endswith(" Project"):
        cleaned = cleaned[: -len(" Project")]
    elif " Project " in cleaned:
        cleaned = cleaned.split(" Project ", 1)[0]
    from .keys import default_spellings
    from .parse import parse_name

    parsed = parse_name(cleaned, mtime=mtime, keymap=default_spellings())
    title = parsed.title_tokens or [cleaned]
    return slugify(title), parsed.date


def _candidate_changed(item: FileCandidate, old: sqlite3.Row | None) -> bool:
    if old is None:
        return True
    old_size = int(old["size"]) if old["size"] is not None else None
    old_mtime = float(old["mtime"]) if old["mtime"] is not None else None
    return old_size != item.size or old_mtime != item.mtime


def scan_catalog(
    connection: sqlite3.Connection,
    config: Config,
    *,
    debounce_seconds: float = 120.0,
    stability_wait_seconds: float = 2.0,
    curated_only: bool = False,
) -> ScanSummary:
    started = utc_now()
    run_kind = "ingest" if curated_only else "scan"
    seen_token = started
    if curated_only:
        last_full_scan = connection.execute(
            """
            SELECT started FROM runs
            WHERE kind='scan' AND ok=1
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if last_full_scan is not None:
            seen_token = str(last_full_scan["started"])
    cursor = connection.execute(
        "INSERT INTO runs(kind, started, ok, notes) VALUES(?, ?, 0, ?)",
        (run_kind, started, "{}"),
    )
    run_id = int(cursor.lastrowid)
    try:
        root_warnings: list[str] = []
        available_archive_roots: frozenset[str] = frozenset()
        missing_archive_keys: set[str] = set()
        if curated_only:
            candidates, present, stray_dirs, skipped_counts, projects = (
                _walk_curated(config)
            )
        else:
            walked = _walk_all(config)
            candidates = walked.candidates
            present = walked.present_audio
            stray_dirs = walked.stray_dirs
            skipped_counts = walked.skipped_counts
            projects = walked.project_stats
            available_archive_roots = walked.available_archive_roots
            for root_key, root in walked.missing_archive_roots:
                missing_archive_keys.add(root_key)
                root_warnings.append(
                    f"archive root is not a directory: {root}; "
                    "refusing disappearance inference for that root"
                )
        if config.corpus.curated_dirs:
            matched = sum(
                1
                for name in config.corpus.curated_dirs
                if (config.corpus.root / name).is_dir()
            )
            if matched == 0:
                root_warnings.append(
                    f"0 of {len(config.corpus.curated_dirs)} curated_dirs "
                    f"matched under {config.corpus.root}; "
                    "scan will only see top-level loose audio files"
                )
        existing_rows = connection.execute(
            """
            SELECT id, relpath, size, mtime, md5, duration_s, bounce_id,
                   missing_since
            FROM files
            WHERE ?=0 OR (layer='curated' AND relpath NOT GLOB ?)
            """,
            (int(curated_only), f"{ARCHIVE_PREFIX}*"),
        ).fetchall()
        existing = {str(row["relpath"]): row for row in existing_rows}
        now = time.time()
        pending = [
            item
            for item in candidates
            if _candidate_changed(item, existing.get(item.relpath))
        ]
        stable_sizes: dict[str, int | None] = {}
        if pending and stability_wait_seconds > 0:
            time.sleep(stability_wait_seconds)
        for item in pending:
            try:
                stable_sizes[item.relpath] = item.path.stat().st_size
            except OSError:
                stable_sizes[item.relpath] = None

        upserts: list[tuple[object, ...]] = []
        touches: list[tuple[object, ...]] = []
        new_count = 0
        changed_count = 0
        unchanged_count = 0
        debounce_count = 0
        for item in candidates:
            old = existing.get(item.relpath)
            is_changed = _candidate_changed(item, old)
            if not is_changed:
                unchanged_count += 1
                touches.append((seen_token, item.layer, item.relpath))
                continue
            stable_size = stable_sizes.get(item.relpath)
            if now - item.mtime < debounce_seconds or stable_size != item.size:
                debounce_count += 1
                continue
            try:
                checksum = _md5(item.path)
            except OSError:
                debounce_count += 1
                continue
            duration = _duration(item.path) if item.layer == "curated" else None
            upserts.append(
                (
                    item.relpath,
                    item.layer,
                    item.ext,
                    item.size,
                    item.mtime,
                    checksum,
                    duration,
                    started,
                    seen_token,
                )
            )
            if old is None:
                new_count += 1
            else:
                changed_count += 1

        for batch in _chunks(touches):
            with transaction(connection):
                connection.executemany(
                    """
                    UPDATE files
                    SET last_seen=?, layer=?, missing_since=NULL
                    WHERE relpath=?
                    """,
                    batch,
                )
        for batch in _chunks(upserts):
            with transaction(connection):
                connection.executemany(
                    """
                    INSERT INTO files(
                      relpath, layer, ext, size, mtime, md5, duration_s,
                      first_seen, last_seen, missing_since
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(relpath) DO UPDATE SET
                      layer=excluded.layer,
                      ext=excluded.ext,
                      size=excluded.size,
                      mtime=excluded.mtime,
                      md5=excluded.md5,
                      duration_s=excluded.duration_s,
                      last_seen=excluded.last_seen,
                      missing_since=NULL
                    """,
                    batch,
                )

        existing_by_root: dict[str, set[str]] = defaultdict(set)
        present_by_root: dict[str, set[str]] = defaultdict(set)
        for relpath in existing:
            existing_by_root[scan_root_key(relpath)].add(relpath)
        for relpath in present:
            present_by_root[scan_root_key(relpath)].add(relpath)

        missing_relpaths: list[str] = []
        # A scan infers "deleted" from "not on disk", which is only sound when
        # the disk is fully present. It is not always: a corpus mid-rsync, an
        # external drive that did not mount, a config pointed at the wrong root.
        # In those cases the files are fine and the catalogue is what breaks -
        # every absent track loses its mirror, drops out of search and out of
        # anyone's queue, and nothing says why.
        #
        # People delete a few takes at a time. They do not delete a tenth of the
        # crate in the gap between two ticks, so treat that as a storage event
        # and refuse it. The scan still records everything it did find; it just
        # declines to conclude anything from an absence it cannot trust.
        # Judge that inference independently for each archive. A healthy large
        # root must never dilute the disappearance percentage of a smaller,
        # missing one.
        guarded = 0
        root_keys = sorted(set(existing_by_root) | set(present_by_root))
        for root_key in root_keys:
            root_existing = existing_by_root[root_key]
            root_missing = sorted(root_existing - present_by_root[root_key])
            is_archive_root = root_key.startswith(ARCHIVE_PREFIX)
            if is_archive_root and root_key not in available_archive_roots:
                guarded += len(root_missing)
                if root_key not in missing_archive_keys:
                    root_name = root_key[len(ARCHIVE_PREFIX) :].rstrip("/")
                    root_warnings.append(
                        f"archive root is not configured: {root_name}; "
                        "refusing disappearance inference for that root"
                    )
                continue
            if root_missing and root_existing:
                limit = max(
                    MASS_MISSING_FLOOR,
                    int(len(root_existing) * MASS_MISSING_SHARE),
                )
                if len(root_missing) > limit:
                    guarded += len(root_missing)
                    continue
            missing_relpaths.extend(root_missing)
        missing_relpaths.sort()
        newly_missing = [
            relpath
            for relpath in missing_relpaths
            if existing[relpath]["missing_since"] is None
        ]
        touched_relpaths = sorted(
            {str(row[0]) for row in upserts} | set(newly_missing)
        )
        touched_bounce_ids = {
            int(existing[relpath]["bounce_id"])
            for relpath in touched_relpaths
            if relpath in existing and existing[relpath]["bounce_id"] is not None
        }
        with transaction(connection):
            if missing_relpaths:
                connection.executemany(
                    """
                    UPDATE files
                    SET missing_since=COALESCE(missing_since, ?)
                    WHERE relpath=?
                    """,
                    [(started, relpath) for relpath in missing_relpaths],
                )
            for stray in sorted(stray_dirs):
                enqueue_review(
                    connection,
                    "stray_location",
                    payload={"directory": stray},
                )
            # A directory that gained a classification stops being a finding.
            # Without this, stray_location reviews were immortal: the seven
            # archive folders stayed on V2 after they were curated, because
            # nothing ever closed the open rows the earlier scans filed.
            open_strays = connection.execute(
                """
                SELECT id, payload FROM review_queue
                WHERE kind='stray_location' AND status='open'
                """
            ).fetchall()
            resolved_strays = [
                (int(row["id"]),)
                for row in open_strays
                if json.loads(row["payload"]).get("directory") not in stray_dirs
            ]
            if resolved_strays:
                connection.executemany(
                    """
                    UPDATE review_queue
                    SET status='resolved', resolved_at=?
                    WHERE id=?
                    """,
                    [(utc_now(), row_id) for (row_id,) in resolved_strays],
                )
            for stats in projects.values():
                name_slug, name_date = _project_name_parts(stats.name)
                connection.execute(
                    """
                    INSERT INTO projects(
                      relpath, name_slug, name_date, als_count,
                      backup_als_count, total_bytes
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(relpath) DO UPDATE SET
                      name_slug=excluded.name_slug,
                      name_date=excluded.name_date,
                      als_count=excluded.als_count,
                      backup_als_count=excluded.backup_als_count,
                      total_bytes=excluded.total_bytes
                    """,
                    (
                        stats.relpath,
                        name_slug,
                        name_date,
                        stats.als_count,
                        stats.backup_als_count,
                        stats.total_bytes,
                    ),
                )

        resolved = resolve_catalog(connection, config)
        if touched_relpaths:
            placeholders = ",".join("?" for _ in touched_relpaths)
            touched_bounce_ids.update(
                int(row["bounce_id"])
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT bounce_id FROM files
                    WHERE relpath IN ({placeholders}) AND bounce_id IS NOT NULL
                    """,
                    touched_relpaths,
                )
            )
        if touched_bounce_ids:
            placeholders = ",".join("?" for _ in touched_bounce_ids)
            touched_song_ids = tuple(
                int(row["song_id"])
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT song_id FROM bounces
                    WHERE id IN ({placeholders})
                    """,
                    tuple(sorted(touched_bounce_ids)),
                )
            )
            if touched_song_ids:
                placeholders = ",".join("?" for _ in touched_song_ids)
                touched_bounce_ids.update(
                    int(row["id"])
                    for row in connection.execute(
                        f"""
                        SELECT id FROM bounces
                        WHERE song_id IN ({placeholders})
                        """,
                        touched_song_ids,
                    )
                )
        notes = {
            "counts": {
                "new": new_count,
                "changed": changed_count,
                "unchanged": unchanged_count,
                "skipped_debounce": debounce_count,
                "missing": len(missing_relpaths),
                "missing_guarded": guarded,
            },
            "skipped_by_top_level": {
                directory: dict(sorted(counts.items()))
                for directory, counts in sorted(skipped_counts.items())
            },
            "scan_token": started,
            "scope": "curated" if curated_only else "full",
            "root_warnings": root_warnings,
            "touched_relpaths": touched_relpaths,
            "touched_bounce_ids": sorted(touched_bounce_ids),
        }
        connection.execute(
            "UPDATE runs SET finished=?, ok=1, notes=? WHERE id=?",
            (utc_now(), json.dumps(notes, sort_keys=True), run_id),
        )
        return ScanSummary(
            new=new_count,
            changed=changed_count,
            unchanged=unchanged_count,
            skipped_debounce=debounce_count,
            missing=len(missing_relpaths),
            files_seen=len(candidates),
            resolve=resolved,
            run_id=run_id,
            touched_relpaths=tuple(touched_relpaths),
            touched_bounce_ids=tuple(sorted(touched_bounce_ids)),
            missing_guarded=guarded,
            root_warnings=tuple(root_warnings),
        )
    except BaseException as exc:
        connection.execute(
            "UPDATE runs SET finished=?, ok=0, notes=? WHERE id=?",
            (utc_now(), json.dumps({"error": str(exc)}), run_id),
        )
        raise
