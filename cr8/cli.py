"""Command-line interface for crate."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Sequence

from .art import render_art_strips, render_cover_previews
from .automation import (
    NIGHTLY_STAGE_PLAN,
    LockBusy,
    doctor_setup_text,
    install_launchd,
    run_doctor,
    run_ingest_tick,
    run_monthly,
    run_nightly,
)
from .config import ConfigError, load_config
from .csvio import export_csv, import_csv
from .db import connect
from .enrichment import detect, fingerprint, import_mik
from .export import export_portable
from .init import run_init
from .mirror import build_mirror
from .push import push_mirror
from .resolve import backfill_dates
from .review import review_loop, set_song
from .scan import scan_catalog
from .scrub import scrub
from .status import render_status
from .stems import (
    clean_stem_jobs,
    enqueue_stem_job,
    run_stem_worker,
    separate_bounce,
    stem_job_status,
)
from .verify import run_verify


def _bake_login_mark(config_path: Path, db_path: Path, mirror_root: Path | None) -> str | None:
    """Refresh web/lib/loginMark.ts from this install's music when possible."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "bake-login-mark.py"
    if not script.is_file():
        return None
    root = mirror_root or (db_path.parent / "mirror")
    argv = [
        sys.executable,
        str(script),
        "--db",
        str(db_path),
        "--mirror-root",
        str(root),
    ]
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            cwd=str(config_path.parent),
        )
    except OSError as exc:
        return f"login mark bake skipped: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "bake failed").strip()
        return f"login mark bake skipped: {detail}"
    return (result.stdout or "").strip() or "login mark baked"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cr8")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    parser.add_argument("--db", help="override catalog database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "init",
        help="bootstrap config, dirs, session secret, and database for a fresh clone",
    )
    subparsers.add_parser("scan", help="scan, parse, and resolve the corpus")
    backfill_dates_parser = subparsers.add_parser(
        "backfill-dates", help="fill missing bounce dates from source file mtimes"
    )
    backfill_dates_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser(
        "ingest-tick",
        help="incrementally ingest settled files from curated paths",
    )
    subparsers.add_parser("status", help="show catalog status")

    nightly_parser = subparsers.add_parser(
        "nightly",
        help="run the locked nightly safety pipeline",
    )
    nightly_parser.add_argument("--dry-run", action="store_true")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check services, catalog freshness, backups, and disk",
    )
    doctor_parser.add_argument("--setup", action="store_true")

    launchd_parser = subparsers.add_parser(
        "install-launchd",
        help="write ingest, nightly, and monthly LaunchAgent plists",
    )
    launchd_parser.add_argument(
        "--dir",
        dest="launchd_dir",
        help="destination (default: ~/Library/LaunchAgents)",
    )
    subparsers.add_parser(
        "monthly",
        help="check the local restic repo and run a restore drill",
    )

    verify_parser = subparsers.add_parser("verify", help="verify catalog coverage")
    verify_parser.add_argument("--strict", action="store_true")

    subparsers.add_parser("review", help="work through the review queue")

    set_parser = subparsers.add_parser("set", help="set human song metadata")
    set_parser.add_argument("--allow-new", action="store_true")
    set_parser.add_argument("target")
    set_parser.add_argument("changes", nargs=argparse.REMAINDER)

    export_parser = subparsers.add_parser("export-csv", help="export songs as CSV")
    export_parser.add_argument("--filter")
    export_parser.add_argument("out")
    portable_parser = subparsers.add_parser(
        "export",
        help="export every song and tag plus collection playlists",
    )
    portable_parser.add_argument(
        "out",
        nargs="?",
        default="crate-export",
        help="output directory (default: crate-export)",
    )

    import_parser = subparsers.add_parser("import-csv", help="apply song metadata from CSV")
    import_parser.add_argument("--allow-new", action="store_true")
    import_parser.add_argument("--dry-run", action="store_true")
    import_parser.add_argument("file")

    mik_parser = subparsers.add_parser(
        "import-mik", help="import a copied snapshot of Mixed In Key metadata"
    )
    mik_parser.add_argument("--source", type=Path)

    detect_parser = subparsers.add_parser(
        "detect", help="detect missing key and BPM metadata"
    )
    detect_parser.add_argument("--limit", type=int)

    subparsers.add_parser(
        "fingerprint", help="fingerprint curated bounces and enrich duplicate reviews"
    )

    scrub_parser = subparsers.add_parser(
        "scrub", help="verify one rotating eighth of immutable source audio"
    )
    scrub_parser.add_argument("--bucket", type=int, choices=range(8))

    build_parser = subparsers.add_parser("build", help="build the listening mirror")
    build_parser.add_argument("--force-shrink", action="store_true")
    build_parser.add_argument("--mirror-root", type=Path)

    previews_parser = subparsers.add_parser(
        "render-cover-previews",
        help="render spectral and envelope covers into the preview mirror",
    )
    previews_parser.add_argument(
        "--style", choices=("spectral", "envelope", "all"), default="all"
    )
    previews_parser.add_argument("--limit", type=int)
    previews_parser.add_argument("--workers", type=int, default=4)

    strips_parser = subparsers.add_parser(
        "render-strips",
        help="render wide spectral strips into the listening mirror",
    )
    strips_parser.add_argument("--limit", type=int)
    strips_parser.add_argument("--workers", type=int, default=4)

    push_parser = subparsers.add_parser("push", help="guarded jukebox mirror sync")
    push_parser.add_argument("destination")
    push_parser.add_argument("--mirror-root", type=Path)
    push_parser.add_argument("--dry-run", action="store_true")
    push_parser.add_argument("--rescan-url")

    stems_parser = subparsers.add_parser(
        "stems", help="separate and manage archival stems"
    )
    stems_subparsers = stems_parser.add_subparsers(
        dest="stems_command", required=True
    )
    separate_parser = stems_subparsers.add_parser(
        "separate", help="synchronously separate one bounce"
    )
    separate_parser.add_argument("bounce_ulid")
    separate_parser.add_argument(
        "--recipe", choices=("default-v1", "hq-v1"), default="default-v1"
    )
    separate_parser.add_argument("--job-ulid", help=argparse.SUPPRESS)
    enqueue_parser = stems_subparsers.add_parser(
        "enqueue", help="queue one bounce for background separation"
    )
    enqueue_parser.add_argument("bounce_ulid")
    enqueue_parser.add_argument(
        "--recipe", choices=("default-v1", "hq-v1"), default="default-v1"
    )
    enqueue_parser.add_argument("--priority", type=int, default=100)
    worker_parser = stems_subparsers.add_parser(
        "worker", help="claim queued stem jobs"
    )
    worker_mode = worker_parser.add_mutually_exclusive_group(required=True)
    worker_mode.add_argument("--once", action="store_true")
    worker_mode.add_argument("--drain", action="store_true")
    stems_subparsers.add_parser(
        "clean", help="sweep stale scratch and reclaim expired leases"
    )
    stems_subparsers.add_parser("status", help="show the stems queue")

    return parser


def _execute(args: argparse.Namespace) -> int:
    if args.command == "init":
        return run_init(config_path=Path(args.config))

    config = load_config(args.config)
    db_path = Path(args.db).resolve() if args.db else config.db_path
    if args.command == "stems":
        if args.stems_command == "separate":
            result = separate_bounce(
                db_path,
                config,
                args.bounce_ulid,
                recipe=args.recipe,
                job_ulid=args.job_ulid,
            )
            action = "separated" if result.created else "verified existing"
            print(f"stems: {action} {result.bounce_ulid}")
            print(
                f"stems: pass A {result.pass_a_seconds:.1f}s, "
                f"pass B {result.pass_b_seconds:.1f}s"
            )
            print(f"stems: {result.output_dir}")
            return 0
        if args.stems_command == "enqueue":
            job_ulid = enqueue_stem_job(
                db_path,
                args.bounce_ulid,
                recipe=args.recipe,
                priority=args.priority,
                requested_by=os.environ.get("USER") or "cli",
            )
            print(f"stems: queued {job_ulid}")
            return 0
        if args.stems_command == "worker":
            summary = run_stem_worker(
                db_path,
                config,
                config_path=Path(args.config),
                drain=args.drain,
            )
            print(
                f"stems worker: {summary.claimed} claimed, "
                f"{summary.completed} done, {summary.retried} retry queued, "
                f"{summary.failed} failed"
            )
            if summary.paused:
                print("stems worker: paused")
            return 1 if summary.failed else 0
        if args.stems_command == "clean":
            swept, reclaimed = clean_stem_jobs(db_path, config)
            print(
                f"stems clean: {swept} scratch swept, "
                f"{reclaimed} lease(s) reclaimed"
            )
            return 0
        if args.stems_command == "status":
            status = stem_job_status(db_path)
            counts = status["counts"]
            print(
                "stems queue: "
                f"{counts.get('queued', 0)} queued, "
                f"{counts.get('running', 0)} running, "
                f"{counts.get('failed', 0)} failed, "
                f"{counts.get('done', 0)} done"
            )
            if status["running"]:
                running = status["running"]
                print(
                    f"stems running: {running['bounce_ulid']} "
                    f"({running['progress']}, attempt "
                    f"{running['attempts']}/{running['max_attempts']}, "
                    f"lease {running['lease_until']})"
                )
            return 0

    if args.command == "install-launchd":
        destination = Path(args.launchd_dir).expanduser() if args.launchd_dir else None
        written = install_launchd(config, db_path, destination=destination)
        for path in written:
            print(f"wrote {path}")
        print("\nRun these commands when ready:")
        domain = f"gui/{os.getuid()}"
        for path in written:
            print(f"launchctl bootstrap {domain} {path}")
        return 0

    if args.command == "doctor":
        checks = run_doctor(db_path, config)
        for check in checks:
            mark = "OK" if check.ok else "PROBLEM"
            print(f"[{mark}] {check.name}: {check.detail}")
        if args.setup:
            print()
            print(doctor_setup_text())
        return 0 if all(check.ok for check in checks) else 1

    if args.command == "ingest-tick":
        try:
            result = run_ingest_tick(db_path, config)
        except LockBusy:
            print("ingest: deferred — the mirror-build lock is held")
            return 0
        print(
            f"ingest: {result.scan.new} new, {result.scan.changed} changed, "
            f"{result.scan.unchanged} unchanged, "
            f"{result.scan.skipped_debounce} deferred"
        )
        if result.scan.missing_guarded:
            print(
                f"ingest: {result.scan.missing_guarded} files are not on disk - "
                "too many to be deletions, so they were left alone. the corpus "
                "is probably still copying or did not mount."
            )
        print(f"ingest build: {result.build_detail}")
        if result.unparsed:
            print(f"ingest: {len(result.unparsed)} unparseable filename(s)")
        return 0

    if args.command == "nightly":
        if args.dry_run:
            print("cr8 nightly --dry-run")
            for index, stage in enumerate(NIGHTLY_STAGE_PLAN, 1):
                print(f"{index:>2}. {stage}")
            return 0
        report = run_nightly(db_path, config)
        if report.already_running:
            print("nightly: already running")
            return 0
        for stage in report.stages:
            status = "SKIP" if stage.skipped else ("OK" if stage.ok else "FAILED")
            print(f"nightly {stage.name}: {status} — {stage.detail}")
        if report.digest_path:
            print(f"nightly digest: {report.digest_path}")
        return 0 if report.ok else 1

    if args.command == "monthly":
        results = run_monthly(db_path, config)
        for stage in results:
            status = "SKIP" if stage.skipped else ("OK" if stage.ok else "FAILED")
            print(f"{stage.name}: {status} — {stage.detail}")
        return 0 if all(stage.ok for stage in results) else 1

    connection = connect(db_path)
    try:
        if args.command == "scan":
            summary = scan_catalog(connection, config)
            print(
                f"scan: {summary.new} new, {summary.changed} changed, "
                f"{summary.unchanged} unchanged, "
                f"{summary.skipped_debounce} deferred, {summary.missing} missing"
            )
            for warning in summary.root_warnings:
                print(f"scan: WARNING — {warning}")
            if summary.missing_guarded:
                print(
                    f"scan: {summary.missing_guarded} absent files were left "
                    "active by per-root disappearance guards"
                )
            print(
                f"resolve: {summary.resolve.parsed} parsed, "
                f"{summary.resolve.residue} residue, "
                f"{summary.resolve.songs} songs, {summary.resolve.bounces} bounces"
            )
            return 0
        if args.command == "backfill-dates":
            summary = backfill_dates(connection, dry_run=args.dry_run)
            if summary.dry_run:
                print(
                    f"backfill-dates: would backfill {summary.bounces} bounces, "
                    f"roll up {summary.songs} songs"
                )
                for example in summary.examples:
                    print(
                        f"backfill-dates: bounce {example.bounce_id} "
                        f"({example.source_stem}) -> {example.bounce_date}"
                    )
            else:
                print(
                    f"backfill-dates: {summary.bounces} bounces backfilled, "
                    f"{summary.songs} songs rolled up"
                )
            return 0
        if args.command == "status":
            print(render_status(connection, db_path))
            return 0
        if args.command == "verify":
            result = run_verify(connection, config, strict=args.strict)
            print(result.output)
            print(f"\nreport: {result.report_path}")
            return result.exit_code
        if args.command == "review":
            handled = review_loop(connection, config)
            print(f"handled {handled} review item(s)")
            return 0
        if args.command == "set":
            changes = list(args.changes)
            allow_new = args.allow_new
            if "--allow-new" in changes:
                changes.remove("--allow-new")
                allow_new = True
            if not changes:
                raise ValueError("at least one change is required")
            song_id = set_song(
                connection,
                config,
                args.target,
                changes,
                allow_new=allow_new,
                author=os.environ.get("USER"),
            )
            print(f"updated song {song_id}")
            return 0
        if args.command == "export-csv":
            count = export_csv(
                connection, config, args.out, filter_value=args.filter
            )
            print(f"exported {count} song(s) to {args.out}")
            return 0
        if args.command == "export":
            summary = export_portable(
                connection,
                config,
                args.out,
            )
            print(
                f"exported {summary.songs} song(s) and "
                f"{summary.collections} collection(s) to "
                f"{summary.output_dir}"
            )
            return 0
        if args.command == "import-csv":
            summary = import_csv(
                connection,
                config,
                args.file,
                allow_new=args.allow_new,
                dry_run=args.dry_run,
                author=os.environ.get("USER"),
            )
            prefix = "would change" if summary.dry_run else "changed"
            print(
                f"{prefix} {summary.songs_changed} song(s), "
                f"{summary.fields_changed} field(s); {summary.rows} row(s) read"
            )
            return 0
        if args.command == "import-mik":
            summary = import_mik(connection, config, source_path=args.source)
            print(
                f"import-mik: {summary.imported} imported, {summary.matched} matched, "
                f"{summary.unmatched} unmatched, {summary.conflicts} conflicts"
            )
            return 0
        if args.command == "detect":
            if args.limit is not None and args.limit < 0:
                raise ValueError("--limit must be non-negative")
            summary = detect(connection, config, limit=args.limit)
            print(
                f"detect: {summary.candidates} candidates, "
                f"{summary.keys_analyzed} keys, {summary.bpms_analyzed} bpms, "
                f"{summary.failed} failed"
            )
            for tool in summary.skipped_tools:
                print(f"detect: skipped {tool} analysis (missing tool)")
            return 1 if summary.failed else 0
        if args.command == "fingerprint":
            summary = fingerprint(connection, config)
            if summary.missing_tool:
                print(
                    f"fingerprint: skipped {summary.candidates} bounces "
                    f"(missing tool: {summary.missing_tool})"
                )
                return 0
            print(
                f"fingerprint: {summary.analyzed} analyzed, {summary.skipped} resumed, "
                f"{summary.edges} review edges, {summary.failed} failed"
            )
            return 1 if summary.failed else 0
        if args.command == "scrub":
            summary = scrub(connection, config, bucket=args.bucket)
            print(
                f"scrub: bucket {summary.bucket}, {summary.checked} checked, "
                f"{summary.anchored} anchored, {len(summary.mismatches)} critical"
            )
            for mismatch in summary.mismatches:
                print(f"CRITICAL: {mismatch}")
            print(f"report: {summary.report_path}")
            return summary.exit_code
        if args.command == "build":
            summary = build_mirror(
                connection,
                config,
                mirror_root=args.mirror_root,
                force_shrink=args.force_shrink,
            )
            print(
                f"build: {summary.total} total, {summary.rebuilt} rebuilt, "
                f"{summary.retagged} retagged, {summary.unchanged} unchanged, "
                f"{summary.peaks_built} peaks, {summary.covers_built} covers"
            )
            if summary.swept_tmp:
                print(f"build: swept {summary.swept_tmp} orphan tmp file(s)")
            for tool in summary.skipped_tools:
                print(f"build: skipped {tool} step (missing tool)")
            baked = _bake_login_mark(
                Path(args.config).expanduser().resolve(),
                db_path,
                args.mirror_root,
            )
            if baked:
                print(f"build: {baked}")
            return 0
        if args.command == "render-cover-previews":
            summary = render_cover_previews(
                connection,
                config,
                style=args.style,
                limit=args.limit,
                workers=args.workers,
            )
            if args.style in {"spectral", "all"}:
                print(
                    f"render-cover-previews spectral: {summary.spectral} rendered, "
                    f"{summary.failures_for('spectral')} failed"
                )
            if args.style in {"envelope", "all"}:
                print(
                    f"render-cover-previews envelope: {summary.envelope} rendered, "
                    f"{summary.failures_for('envelope')} failed"
                )
            for failure in summary.failures:
                print(
                    f"render-cover-previews {failure.style}: "
                    f"{failure.bounce_ulid}: {failure.error}"
                )
            return 0
        if args.command == "render-strips":
            summary = render_art_strips(
                connection,
                config,
                limit=args.limit,
                workers=args.workers,
            )
            print(
                f"render-strips: {summary.rendered} rendered, "
                f"{len(summary.failures)} failed"
            )
            for failure in summary.failures:
                print(
                    f"render-strips: {failure.bounce_ulid}: {failure.error}"
                )
            return 0
        if args.command == "push":
            summary = push_mirror(
                connection,
                config,
                args.destination,
                mirror_root=args.mirror_root,
                dry_run=args.dry_run,
                rescan_url=args.rescan_url,
            )
            mode = "dry-run" if summary.dry_run else "synced"
            print(
                f"push: {mode} {summary.tracks} tracks to {summary.destination}"
            )
            if summary.rescan_posted:
                print("push: rescan hook posted")
            return 0
        raise ValueError(f"unknown command: {args.command}")
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        return _execute(parser.parse_args(argv))
    except (
        ConfigError,
        FileNotFoundError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        ValueError,
    ) as exc:
        print(f"cr8: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
