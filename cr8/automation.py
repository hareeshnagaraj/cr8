"""Safe local automation for ingest, nightly maintenance, and monitoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import plistlib
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .audio import sha256_file
from .config import Config
from .db import connect, utc_now
from .enrichment import detect, fingerprint, import_mik
from .mirror import BuildSummary, build_mirror
from .scan import ScanSummary, scan_catalog
from .scrub import scrub
from .tooling import find_tool, run_tool
from .verify import coverage_snapshot, run_verify
from .paths import source_path


DEBOUNCE_SECONDS = 120.0
NIGHTLY_STAGE_PLAN = (
    "scan",
    "verify (V1-V4/V7)",
    "import-mik",
    "fingerprint (incremental)",
    "detect (incremental)",
    "build",
    "scrub (weekly rotation)",
    "WAL checkpoint (TRUNCATE)",
    "database snapshot + integrity_check",
    "restic local",
    "restic B2 (not configured)",
    "final verify",
    "digest",
    "macOS notification",
    "healthchecks.io",
)
_PREBUILD_VERIFY_PREFIXES = ("V1:", "V2:", "V3:", "V4:", "V7:")
_NOTIFICATION_SCRIPT = """\
on run argv
  display notification (item 1 of argv) with title (item 2 of argv)
end run
"""


@dataclass(frozen=True)
class IngestResult:
    scan: ScanSummary
    build: BuildSummary | None
    build_detail: str
    unparsed: tuple[str, ...]


@dataclass(frozen=True)
class StageResult:
    name: str
    ok: bool
    detail: str
    skipped: bool
    seconds: float


@dataclass
class NightlyReport:
    started: str
    stages: list[StageResult] = field(default_factory=list)
    pinged: bool = False
    already_running: bool = False
    digest_path: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.already_running and all(stage.ok for stage in self.stages)

    @property
    def failures(self) -> tuple[StageResult, ...]:
        return tuple(stage for stage in self.stages if not stage.ok)


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


class LockBusy(RuntimeError):
    """Raised when another process holds an automation lock."""


class FileLock:
    """Exclusive non-blocking advisory lock backed by flock(2)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: object | None = None

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise LockBusy(str(self.path)) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()} {utc_now()}\n")
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, *_exc: object) -> None:
        handle = self._handle
        if handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            self._handle = None


BUILD_LOCK_NAME = ".cr8-build.lock"


def _acquire_build_lock(
    state_dir: Path,
    *,
    timeout_seconds: float = 30 * 60,
    poll_seconds: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
) -> FileLock:
    """Wait for the mirror-build lock instead of skipping the run.

    The nightly must never bail because a five-minute ingest tick happened
    to hold the lock at 03:30 — it waits the tick out. The timeout exists
    only for a wedged holder, where failing loudly beats racing it.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            lock = FileLock(state_dir / BUILD_LOCK_NAME)
            lock.__enter__()
            return lock
        except LockBusy:
            if time.monotonic() >= deadline:
                raise
            sleep(poll_seconds)


def _local_restic_repo() -> Path:
    """Where the local backup repository lives.

    Overridable because the default sits inside ~/Music, and macOS refuses a
    LaunchAgent write access there - the job dies with EX_CONFIG before the
    program runs, so the backup does not fail loudly, it never starts. That is
    survivable on a laptop somebody looks at. On the machine actually serving
    the catalogue it would mean believing there were nightly backups and having
    none, which is the failure you find out about on the day you need them.
    """
    override = os.environ.get("CR8_BACKUP_REPO", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Music" / "CrateBackup"


def _restic_password_file(config: Config) -> Path:
    return config.state_dir / "secrets" / "restic-password.txt"


def _run_osascript(config: Config, message: str, title: str = "cr8") -> bool:
    """Post a notification with all dynamic text passed as argv."""
    executable = find_tool("osascript", state_dir=config.state_dir)
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [
                str(executable),
                "-e",
                _NOTIFICATION_SCRIPT,
                "--",
                message,
                title,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _live_is_running(config: Config) -> bool:
    executable = find_tool("pgrep", state_dir=config.state_dir)
    if executable is None:
        return False
    result = run_tool(executable, ("-x", "Live"), timeout=10)
    return result.returncode == 0


def _unparsed_touched(
    connection: sqlite3.Connection,
    relpaths: Iterable[str],
) -> tuple[str, ...]:
    values = tuple(sorted(set(relpaths)))
    if not values:
        return ()
    placeholders = ",".join("?" for _ in values)
    return tuple(
        str(row["relpath"])
        for row in connection.execute(
            f"""
            SELECT relpath FROM files
            WHERE relpath IN ({placeholders})
              AND layer='curated' AND parse_status='residue'
              AND missing_since IS NULL
            ORDER BY relpath
            """,
            values,
        )
    )


def run_ingest_tick(
    db_path: Path,
    config: Config,
    *,
    debounce_seconds: float = DEBOUNCE_SECONDS,
    stability_wait_seconds: float = 0.2,
) -> IngestResult:
    """Scan only curated paths, resolve them, and build touched bounces.

    Raises LockBusy when the mirror-build lock is held — the nightly (or a
    previous tick) is mid-build, and a tick reading or rebuilding under it
    is how final-verify flapped red on hundreds of transient findings.
    A skipped tick costs nothing; the next one runs in five minutes.
    """
    with FileLock(config.state_dir / BUILD_LOCK_NAME):
        return _ingest_tick_locked(
            db_path,
            config,
            debounce_seconds=debounce_seconds,
            stability_wait_seconds=stability_wait_seconds,
        )


def _ingest_tick_locked(
    db_path: Path,
    config: Config,
    *,
    debounce_seconds: float,
    stability_wait_seconds: float,
) -> IngestResult:
    connection = connect(db_path)
    try:
        summary = scan_catalog(
            connection,
            config,
            debounce_seconds=debounce_seconds,
            stability_wait_seconds=stability_wait_seconds,
            curated_only=True,
        )
        unparsed = _unparsed_touched(connection, summary.touched_relpaths)
        built: BuildSummary | None = None
        if not summary.touched_bounce_ids:
            build_detail = "no touched bounces"
        elif _live_is_running(config):
            build_detail = "deferred while Ableton Live is running"
        else:
            built = build_mirror(
                connection,
                config,
                bounce_ids=frozenset(summary.touched_bounce_ids),
            )
            build_detail = (
                f"{built.rebuilt} rebuilt, {built.retagged} retagged, "
                f"{built.unchanged} unchanged"
            )
        if unparsed:
            names = ", ".join(Path(item).name for item in unparsed[:3])
            if len(unparsed) > 3:
                names += f", and {len(unparsed) - 3} more"
            _run_osascript(
                config,
                f"Could not parse {len(unparsed)} filename(s): {names}",
                "cr8 ingest",
            )
        return IngestResult(summary, built, build_detail, unparsed)
    finally:
        connection.close()


def verify_database_copy(path: Path) -> None:
    """Raise unless every integrity_check row is exactly ``ok``."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"integrity_check could not read {path}: {exc}") from exc
    verdicts = [str(row[0]) for row in rows]
    if verdicts != ["ok"]:
        detail = "; ".join(verdicts) or "no result"
        raise RuntimeError(f"integrity_check failed for {path}: {detail}")


def snapshot_database(
    db_path: Path,
    backups_dir: Path,
    *,
    today: date | None = None,
) -> tuple[Path, int]:
    """Create and verify a dated VACUUM copy, retaining 14 daily copies."""
    day = today or date.today()
    backups_dir.mkdir(parents=True, exist_ok=True)
    destination = backups_dir / f"catalog-{day.isoformat()}.db"
    temporary = backups_dir / f".{destination.name}.tmp.{os.getpid()}"
    temporary.unlink(missing_ok=True)
    try:
        source = sqlite3.connect(db_path, timeout=30)
        try:
            source.execute("PRAGMA busy_timeout=30000")
            source.execute("VACUUM INTO ?", (str(temporary),))
        finally:
            source.close()
        verify_database_copy(temporary)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    copies = sorted(backups_dir.glob("catalog-[0-9][0-9][0-9][0-9]-*.db"))
    expired = copies[:-14]
    for path in expired:
        path.unlink()
    return destination, len(expired)


def _restic_environment(config: Config) -> dict[str, str]:
    password_file = _restic_password_file(config)
    if not password_file.is_file():
        raise FileNotFoundError(
            f"restic password is not configured: {password_file}"
        )
    environment = os.environ.copy()
    environment["RESTIC_PASSWORD_FILE"] = str(password_file)
    return environment


def _restic_tool(config: Config) -> Path:
    executable = find_tool("restic", state_dir=config.state_dir)
    if executable is None:
        raise FileNotFoundError("restic is not installed")
    return executable


def restic_backup_local(db_path: Path, config: Config) -> str:
    """Back up source data and verified DB copies, never the live database."""
    repository = _local_restic_repo()
    if not repository.is_dir():
        raise FileNotFoundError(f"local restic repository is not configured: {repository}")
    backups_dir = config.state_dir / "backups"
    if not any(backups_dir.glob("catalog-*.db")):
        raise RuntimeError("no verified database snapshot is available")
    targets = [
        config.corpus.root,
        backups_dir,
        config.path,
        config.keymap_path,
    ]
    missing = [path for path in targets if not path.exists()]
    if missing:
        raise FileNotFoundError(f"backup target is missing: {missing[0]}")
    result = run_tool(
        _restic_tool(config),
        (
            "-r",
            repository,
            "backup",
            "--tag",
            "crate-nightly",
            "--exclude",
            f"{db_path}*",
            *targets,
        ),
        timeout=12 * 60 * 60,
        env=_restic_environment(config),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"restic backup failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else "snapshot created"


def _stage_detail_scan(summary: ScanSummary) -> str:
    return (
        f"{summary.new} new, {summary.changed} changed, "
        f"{summary.skipped_debounce} deferred, {summary.missing} missing"
    )


def _prebuild_verify(connection: sqlite3.Connection, config: Config) -> str:
    result = run_verify(connection, config)
    findings = tuple(
        item
        for item in result.findings
        if item.startswith(_PREBUILD_VERIFY_PREFIXES)
    )
    if findings:
        raise RuntimeError(
            f"{len(findings)} pre-build finding(s): {findings[0]}"
        )
    return "V1-V4/V7 passed"


def _import_mik_stage(connection: sqlite3.Connection, config: Config) -> str:
    source = Path.home() / "Library/Application Support/Mixedinkey/Collection10.mikdb"
    if not source.is_file():
        return f"not configured: source not found at {source}"
    summary = import_mik(connection, config, source_path=source)
    return (
        f"{summary.imported} imported, {summary.matched} matched, "
        f"{summary.unmatched} unmatched, {summary.conflicts} conflicts"
    )


def _fingerprint_stage(connection: sqlite3.Connection, config: Config) -> str:
    summary = fingerprint(connection, config)
    if summary.missing_tool:
        return f"not configured: missing {summary.missing_tool}"
    # Reported, not raised, for the same reason as detection below: a file
    # fpcalc cannot decode must not stop the mirror build that runs behind it.
    unreadable = (
        f", {summary.failed} fpcalc could not read" if summary.failed else ""
    )
    return (
        f"{summary.analyzed} analyzed, {summary.skipped} already current, "
        f"{summary.edges} review edges{unreadable}"
    )


def _detect_stage(connection: sqlite3.Connection, config: Config) -> str:
    summary = detect(connection, config)
    # Per-file analysis failures are reported, never raised.
    #
    # Key and tempo detection is best-effort enrichment. The mirror build runs
    # behind this stage as a prerequisite, and the build is not best-effort -
    # it is what makes tracks playable and their tags correct. Letting an
    # analyser that cannot read one file stop the build inverts those
    # priorities, and it did: keyfinder-cli exits non-zero on any source it
    # cannot resample to 16-bit PCM, so those tracks failed every night.
    #
    # Worse, the candidate set is exactly the songs still missing a key or a
    # tempo - which is to say, the ones that have already failed. Raising here
    # could therefore never clear on its own. It was not a passing fault, it
    # was permanent, and it silently skipped the build every night.
    #
    # A tool that is missing entirely is a different thing and still surfaces,
    # via skipped_tools below.
    skipped = (
        f"; missing {', '.join(summary.skipped_tools)}"
        if summary.skipped_tools
        else ""
    )
    # Reported, never raised. A track with no findable tempo is a fact about
    # the music, not a fault, and treating it as one failed this stage every
    # night - which skipped the mirror build that runs behind it.
    undetermined = (
        f", {summary.undetermined} with nothing to detect"
        if summary.undetermined
        else ""
    )
    unreadable = (
        f", {summary.failed} the analyser could not read" if summary.failed else ""
    )
    return (
        f"{summary.candidates} candidates, {summary.keys_analyzed} keys, "
        f"{summary.bpms_analyzed} BPMs{undetermined}{unreadable}{skipped}"
    )


def _build_stage(connection: sqlite3.Connection, config: Config) -> str:
    if _live_is_running(config):
        raise RuntimeError("build refused while Ableton Live is running")
    summary = build_mirror(connection, config)
    return (
        f"{summary.total} total, {summary.rebuilt} rebuilt, "
        f"{summary.retagged} retagged, {summary.unchanged} unchanged, "
        f"{len(summary.awaiting_source)} awaiting source"
    )


def _same_iso_week(timestamp: str, now: datetime) -> bool:
    try:
        prior = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return False
    if prior.tzinfo is None:
        prior = prior.replace(tzinfo=UTC)
    return prior.isocalendar()[:2] == now.isocalendar()[:2]


def _scrub_stage(
    connection: sqlite3.Connection,
    config: Config,
    now: datetime,
) -> str:
    row = connection.execute(
        "SELECT value FROM build_state WHERE key='last_scrub_at'"
    ).fetchone()
    if row is not None and _same_iso_week(str(row["value"]), now):
        return f"weekly rotation already completed at {row['value']}"
    summary = scrub(connection, config, today=now.date(), notify=False)
    if summary.mismatches:
        raise RuntimeError(
            f"{len(summary.mismatches)} source hash mismatch(es): "
            f"{summary.mismatches[0]}"
        )
    return (
        f"bucket {summary.bucket}, {summary.checked} checked, "
        f"{summary.anchored} anchored"
    )


def _final_verify(connection: sqlite3.Connection, config: Config) -> str:
    result = run_verify(connection, config)
    if result.exit_code:
        raise RuntimeError(
            f"{len(result.findings)} finding(s): "
            f"{result.findings[0] if result.findings else 'verification failed'}"
        )
    return f"passed; {result.report_path}"


def _record_stage(
    connection: sqlite3.Connection,
    result: StageResult,
    *,
    started: str,
) -> None:
    try:
        connection.execute(
            """
            INSERT INTO runs(kind, started, finished, ok, notes)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                f"nightly:{result.name}",
                started,
                utc_now(),
                int(result.ok),
                json.dumps(
                    {
                        "status": (
                            "skipped"
                            if result.skipped
                            else ("ok" if result.ok else "failed")
                        ),
                        "detail": result.detail,
                        "seconds": round(result.seconds, 3),
                    },
                    sort_keys=True,
                ),
            ),
        )
    except sqlite3.Error:
        pass


def _run_stage(
    connection: sqlite3.Connection,
    name: str,
    action: Callable[[], str],
    *,
    prior: dict[str, StageResult],
    requires: tuple[str, ...] = (),
) -> StageResult:
    started = utc_now()
    began = time.monotonic()
    failed_requirements = [
        dependency
        for dependency in requires
        if dependency in prior and not prior[dependency].ok
    ]
    if failed_requirements:
        result = StageResult(
            name=name,
            ok=True,
            detail=f"prerequisite failed: {', '.join(failed_requirements)}",
            skipped=True,
            seconds=0.0,
        )
    else:
        try:
            detail = action() or "completed"
            result = StageResult(
                name=name,
                ok=True,
                detail=detail,
                skipped=detail.startswith(("not configured:", "weekly rotation already")),
                seconds=time.monotonic() - began,
            )
        except Exception as exc:
            result = StageResult(
                name=name,
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                skipped=False,
                seconds=time.monotonic() - began,
            )
    _record_stage(connection, result, started=started)
    return result


def _digest_metrics(
    connection: sqlite3.Connection,
    since: str,
) -> tuple[int, int, float]:
    rated = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM reactions
            WHERE kind='verdict' AND deleted_at IS NULL AND created_at>=?
            """,
            (since,),
        ).fetchone()[0]
    )
    hearts = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM reactions
            WHERE kind='heart' AND deleted_at IS NULL AND created_at>=?
            """,
            (since,),
        ).fetchone()[0]
    )
    coverage = coverage_snapshot(connection)
    percent = min(
        (coverage.percent(dimension) for dimension in ("status", "key", "vibe", "instr", "collab")),
        default=100.0,
    )
    return rated, hearts, percent


def _digest_markdown(report: NightlyReport, headline: str) -> str:
    lines = [
        f"# cr8 nightly — {report.started[:10]}",
        "",
        headline,
        "",
        "| Stage | Status | Detail | Seconds |",
        "| --- | --- | --- | ---: |",
    ]
    for stage in report.stages:
        status = "skipped" if stage.skipped else ("ok" if stage.ok else "FAILED")
        detail = stage.detail.replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {stage.name} | {status} | {detail} | {stage.seconds:.1f} |"
        )
    lines.extend(
        [
            "",
            (
                "Healthchecks.io is eligible to be pinged after notification."
                if not report.failures
                else "Healthchecks.io was not pinged because one or more stages failed."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write_digest(
    report: NightlyReport,
    config: Config,
    headline: str,
    today: date,
) -> Path:
    reports_dir = config.state_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    destination = reports_dir / f"digest-{today.isoformat()}.md"
    temporary = reports_dir / f".{destination.name}.tmp.{os.getpid()}"
    temporary.write_text(
        _digest_markdown(report, headline),
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def _healthcheck_url(config: Config, *, monthly: bool = False) -> str | None:
    environment_name = (
        "CR8_MONTHLY_HEALTHCHECK_URL" if monthly else "CR8_HEALTHCHECK_URL"
    )
    return os.environ.get(environment_name) or (
        config.automation.monthly_healthcheck_url
        if monthly
        else config.automation.healthcheck_url
    )


def ping_healthcheck(url: str, *, timeout: float = 15) -> None:
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200))
    if not 200 <= status < 300:
        raise RuntimeError(f"healthchecks.io returned HTTP {status}")


def _nightly_headline(
    connection: sqlite3.Connection,
    report: NightlyReport,
    now: datetime,
) -> str:
    if report.failures:
        names = ", ".join(stage.name for stage in report.failures)
        return f"cr8 nightly STALE · failed: {names}"
    rated, hearts, coverage = _digest_metrics(
        connection,
        datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC).isoformat(),
    )
    return (
        f"{rated} rated · {hearts} new hearts · "
        f"coverage {coverage:.0f}% · backups ✓"
    )


def run_nightly(
    db_path: Path,
    config: Config,
    *,
    now: datetime | None = None,
    ping: Callable[[str], None] = ping_healthcheck,
    notify: Callable[[Config, str, str], bool] = _run_osascript,
    stage_overrides: dict[str, Callable[[], str]] | None = None,
) -> NightlyReport:
    """Run the single nightly pipeline under a non-blocking flock."""
    moment = now or datetime.now(UTC)
    report = NightlyReport(started=moment.replace(microsecond=0).isoformat())
    try:
        lock = FileLock(config.state_dir / ".cr8-nightly.lock")
        lock.__enter__()
    except LockBusy:
        report.already_running = True
        return report

    try:
        build_lock = _acquire_build_lock(config.state_dir)
    except LockBusy:
        lock.__exit__(None, None, None)
        raise

    connection: sqlite3.Connection | None = None
    try:
        connection = connect(db_path)
        backup_path: Path | None = None

        def scan_action() -> str:
            return _stage_detail_scan(scan_catalog(connection, config))

        def snapshot_action() -> str:
            nonlocal backup_path
            backup_path, expired = snapshot_database(
                db_path,
                config.state_dir / "backups",
                today=moment.date(),
            )
            return f"{backup_path}; removed {expired} expired local copy/copies"

        def checkpoint_action() -> str:
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            busy, logged, checkpointed = (int(value) for value in row)
            return (
                "wal_checkpoint(TRUNCATE): "
                f"busy={busy}, logged={logged}, checkpointed={checkpointed}"
            )

        actions: list[tuple[str, Callable[[], str], tuple[str, ...]]] = [
            ("scan", scan_action, ()),
            (
                "verify",
                lambda: _prebuild_verify(connection, config),
                ("scan",),
            ),
            (
                "import-mik",
                lambda: _import_mik_stage(connection, config),
                ("scan",),
            ),
            (
                "fingerprint",
                lambda: _fingerprint_stage(connection, config),
                ("scan",),
            ),
            (
                "detect",
                lambda: _detect_stage(connection, config),
                ("scan",),
            ),
            (
                "build",
                lambda: _build_stage(connection, config),
                ("scan", "verify", "fingerprint", "detect"),
            ),
            (
                "scrub",
                lambda: _scrub_stage(connection, config, moment),
                ("scan",),
            ),
            ("checkpoint", checkpoint_action, ()),
            ("database-backup", snapshot_action, ("checkpoint",)),
            (
                "restic-local",
                lambda: restic_backup_local(db_path, config),
                ("database-backup",),
            ),
            (
                "restic-b2",
                lambda: "not configured: Backblaze B2 repository",
                (),
            ),
            (
                "final-verify",
                lambda: _final_verify(connection, config),
                (),
            ),
        ]
        overrides = stage_overrides or {}
        prior: dict[str, StageResult] = {}
        for name, action, dependencies in actions:
            result = _run_stage(
                connection,
                name,
                overrides.get(name, action),
                prior=prior,
                requires=dependencies,
            )
            report.stages.append(result)
            prior[name] = result

        headline = _nightly_headline(connection, report, moment)
        digest_started = utc_now()
        digest_began = time.monotonic()
        try:
            report.digest_path = _write_digest(
                report, config, headline, moment.date()
            )
            digest_result = StageResult(
                "digest",
                True,
                str(report.digest_path),
                False,
                time.monotonic() - digest_began,
            )
        except Exception as exc:
            digest_result = StageResult(
                "digest",
                False,
                f"{type(exc).__name__}: {exc}",
                False,
                time.monotonic() - digest_began,
            )
        report.stages.append(digest_result)
        _record_stage(connection, digest_result, started=digest_started)

        headline = _nightly_headline(connection, report, moment)
        notification_started = utc_now()
        notification_began = time.monotonic()
        notification_ok = notify(config, headline, "cr8 nightly")
        notification_result = StageResult(
            "notification",
            True,
            "posted" if notification_ok else "not configured: osascript unavailable",
            not notification_ok,
            time.monotonic() - notification_began,
        )
        report.stages.append(notification_result)
        _record_stage(connection, notification_result, started=notification_started)

        health_started = utc_now()
        health_began = time.monotonic()
        url = _healthcheck_url(config)
        if report.failures:
            health_result = StageResult(
                "healthcheck",
                True,
                "not pinged because a stage failed",
                True,
                time.monotonic() - health_began,
            )
        elif not url:
            health_result = StageResult(
                "healthcheck",
                True,
                "not configured: CR8_HEALTHCHECK_URL/config.toml unset",
                True,
                time.monotonic() - health_began,
            )
        else:
            try:
                ping(url)
                report.pinged = True
                health_result = StageResult(
                    "healthcheck",
                    True,
                    "pinged",
                    False,
                    time.monotonic() - health_began,
                )
            except Exception as exc:
                health_result = StageResult(
                    "healthcheck",
                    False,
                    f"{type(exc).__name__}: {exc}",
                    False,
                    time.monotonic() - health_began,
                )
        report.stages.append(health_result)
        _record_stage(connection, health_result, started=health_started)
        return report
    finally:
        if connection is not None:
            connection.close()
        build_lock.__exit__(None, None, None)
        lock.__exit__(None, None, None)


def _service_response(
    url: str,
    *,
    accepted: frozenset[int],
    timeout: float = 5,
) -> tuple[bool, str]:
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
    except HTTPError as exc:
        status = exc.code
    except Exception as exc:
        return False, f"unreachable ({type(exc).__name__}: {exc})"
    return status in accepted, f"HTTP {status}"


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _restic_snapshot_check(config: Config, now: datetime) -> DoctorCheck:
    repository = _local_restic_repo()
    if not repository.is_dir():
        return DoctorCheck("restic snapshot", False, f"repo missing: {repository}")
    try:
        result = run_tool(
            _restic_tool(config),
            (
                "-r",
                repository,
                "snapshots",
                "--json",
                "--latest",
                "1",
            ),
            timeout=60,
            env=_restic_environment(config),
        )
        if result.returncode != 0:
            return DoctorCheck(
                "restic snapshot",
                False,
                result.stderr.strip() or f"restic exited {result.returncode}",
            )
        snapshots = json.loads(result.stdout)
        if not snapshots:
            return DoctorCheck("restic snapshot", False, "no snapshots")
        newest = max(_parse_timestamp(str(item["time"])) for item in snapshots)
        age = now - newest
        return DoctorCheck(
            "restic snapshot",
            age <= timedelta(hours=36),
            f"{age.total_seconds() / 3600:.1f} hours old",
        )
    except Exception as exc:
        return DoctorCheck(
            "restic snapshot",
            False,
            f"{type(exc).__name__}: {exc}",
        )


def run_doctor(
    db_path: Path,
    config: Config,
    *,
    now: datetime | None = None,
) -> list[DoctorCheck]:
    """Run operational checks without mutating services or source media."""
    moment = now or datetime.now(UTC)
    checks: list[DoctorCheck] = []
    owner_ok, owner_detail = _service_response(
        config.automation.owner_url,
        accepted=frozenset({200}),
    )
    checks.append(DoctorCheck("owner service", owner_ok, owner_detail))
    connection = connect(db_path)
    try:
        last_scan = connection.execute(
            """
            SELECT finished FROM runs
            WHERE kind='scan' AND ok=1
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if last_scan is None or not last_scan["finished"]:
            checks.append(DoctorCheck("last scan", False, "never"))
        else:
            age = moment - _parse_timestamp(str(last_scan["finished"]))
            checks.append(
                DoctorCheck(
                    "last scan",
                    age <= timedelta(hours=26),
                    f"{age.total_seconds() / 3600:.1f} hours old",
                )
            )

        expected = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT bounce_id)
                FROM files
                WHERE layer='curated' AND missing_since IS NULL
                  AND bounce_id IS NOT NULL
                """
            ).fetchone()[0]
        )
        catalog_mirrored = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT f.bounce_id)
                FROM files AS f
                JOIN mirror_files AS mf ON mf.bounce_id=f.bounce_id
                WHERE f.layer='curated' AND f.missing_since IS NULL
                """
            ).fetchone()[0]
        )
        catalog_tracks = int(
            connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM mirror_files)
                  +
                  (SELECT COUNT(*) FROM stems WHERE mirror_relpath IS NOT NULL)
                """
            ).fetchone()[0]
        )
        physical = sum(
            path.is_file()
            for path in (config.state_dir / "mirror" / "tracks").glob("*.mp3")
        )
        mirror_ok = (
            expected == catalog_mirrored
            and physical == catalog_tracks
        )
        checks.append(
            DoctorCheck(
                "mirror vs catalog",
                mirror_ok,
                f"{catalog_mirrored}/{expected} catalog bounces; "
                f"{physical}/{catalog_tracks} track files",
            )
        )

        review = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN created_at < ? THEN 1 ELSE 0 END) AS stale
            FROM review_queue WHERE status='open'
            """,
            ((moment - timedelta(days=14)).isoformat(),),
        ).fetchone()
        total = int(review["total"] or 0)
        stale = int(review["stale"] or 0)
        checks.append(
            DoctorCheck(
                "open review items",
                stale == 0,
                f"{total} open; {stale} older than 14 days",
            )
        )
    finally:
        connection.close()

    checks.append(_restic_snapshot_check(config, moment))
    usage = shutil.disk_usage(config.state_dir)
    free_gib = usage.free / (1024**3)
    checks.append(
        DoctorCheck(
            "disk free",
            free_gib >= 10,
            f"{free_gib:.1f} GiB",
        )
    )
    return checks


def doctor_setup_text() -> str:
    return "\n".join(
        (
            "Setup checklist (operator actions):",
            "- FileVault on; auto-login off; manual unlock after unplanned reboot accepted.",
            "- Tailscale tag:cr8, device approval, and tailnet lock enabled.",
            "- Bandmates are not tailnet members.",
            "- On the jukebox: pmset -c sleep 0; disablesleep 1.",
            "- Configure separate nightly and monthly healthchecks.io URLs.",
        )
    )


def _monthly_check(config: Config) -> str:
    repository = _local_restic_repo()
    if not repository.is_dir():
        raise FileNotFoundError(f"local restic repository is not configured: {repository}")
    result = run_tool(
        _restic_tool(config),
        ("-r", repository, "check", "--read-data-subset=5%"),
        timeout=24 * 60 * 60,
        env=_restic_environment(config),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return "read-data-subset=5% passed"


def _restore_target(scratch: Path, source: Path) -> Path:
    if source.is_absolute():
        return scratch / source.relative_to(source.anchor)
    return scratch / source


def _monthly_restore_drill(
    connection: sqlite3.Connection,
    config: Config,
    *,
    sample_size: int = 20,
) -> str:
    rows = connection.execute(
        """
        SELECT relpath, sha256 FROM files
        WHERE layer IN ('curated','project') AND missing_since IS NULL
        ORDER BY relpath
        """
    ).fetchall()
    candidates = [
        (source_path(config, str(row["relpath"])), row["sha256"])
        for row in rows
        if source_path(config, str(row["relpath"])).is_file()
    ]
    if not candidates:
        raise RuntimeError("no source files are available for a restore drill")
    selected = random.SystemRandom().sample(
        candidates, min(sample_size, len(candidates))
    )
    expected = {
        path: str(digest) if digest else sha256_file(path)
        for path, digest in selected
    }
    with tempfile.TemporaryDirectory(prefix="crate-restore-") as scratch_text:
        scratch = Path(scratch_text)
        arguments: list[str | Path] = [
            "-r",
            _local_restic_repo(),
            "restore",
            "latest",
            "--tag",
            "crate-nightly",
            "--target",
            scratch,
            "--verify",
        ]
        for path in expected:
            arguments.extend(("--include", path))
        result = run_tool(
            _restic_tool(config),
            arguments,
            timeout=24 * 60 * 60,
            env=_restic_environment(config),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        for source, digest in expected.items():
            restored = _restore_target(scratch, source)
            if not restored.is_file():
                raise RuntimeError(f"restic did not restore {source}")
            if sha256_file(restored) != digest:
                raise RuntimeError(f"restored hash mismatch: {source}")
    return f"{len(selected)} random source files restored and hash-verified"


def run_monthly(db_path: Path, config: Config) -> list[StageResult]:
    """Run the local restic check and restore drill used by launchd."""
    results: list[StageResult] = []
    with FileLock(config.state_dir / ".cr8-monthly.lock"):
        connection = connect(db_path)
        try:
            prior: dict[str, StageResult] = {}
            for name, action, dependencies in (
                ("restic-check", lambda: _monthly_check(config), ()),
                (
                    "restore-drill",
                    lambda: _monthly_restore_drill(connection, config),
                    ("restic-check",),
                ),
            ):
                result = _run_stage(
                    connection,
                    f"monthly:{name}",
                    action,
                    prior=prior,
                    requires=tuple(f"monthly:{item}" for item in dependencies),
                )
                results.append(result)
                prior[f"monthly:{name}"] = result
        finally:
            connection.close()
    url = _healthcheck_url(config, monthly=True)
    if not any(not result.ok for result in results) and url:
        try:
            ping_healthcheck(url)
            results.append(StageResult("monthly:healthcheck", True, "pinged", False, 0))
        except Exception as exc:
            results.append(
                StageResult(
                    "monthly:healthcheck",
                    False,
                    f"{type(exc).__name__}: {exc}",
                    False,
                    0,
                )
            )
    else:
        detail = (
            "not pinged because a stage failed"
            if any(not result.ok for result in results)
            else "not configured: CR8_MONTHLY_HEALTHCHECK_URL/config.toml unset"
        )
        results.append(StageResult("monthly:healthcheck", True, detail, True, 0))
    return results


def _program_arguments(config: Config, db_path: Path, command: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "cr8.cli",
        "--config",
        str(config.path),
        "--db",
        str(db_path),
        command,
    ]


def launchd_plists(
    config: Config,
    db_path: Path,
) -> dict[str, dict[str, object]]:
    logs = config.state_dir / "logs"
    common: dict[str, object] = {
        "WorkingDirectory": str(config.state_dir),
        "ProcessType": "Background",
    }

    def base(label: str, command: str) -> dict[str, object]:
        return {
            **common,
            "Label": label,
            "ProgramArguments": _program_arguments(config, db_path, command),
            "StandardOutPath": str(logs / f"{label}.log"),
            "StandardErrorPath": str(logs / f"{label}.log"),
        }

    watch_paths = [
        str(config.corpus.root),
        *(
            str(config.corpus.root / relative)
            for relative in sorted(config.corpus.curated_dirs)
        ),
    ]
    # Uploads land outside the corpus, so watching only the corpus means an
    # uploaded file waits for the next nightly run instead of being picked up.
    if config.corpus.drops_root is not None:
        watch_paths.append(str(config.corpus.drops_root))
    ingest = {
        **base("com.crate.ingest", "ingest-tick"),
        "WatchPaths": watch_paths,
        "ThrottleInterval": 300,
    }
    nightly = {
        **base("com.crate.nightly", "nightly"),
        "StartCalendarInterval": {"Hour": 3, "Minute": 30},
        "RunAtLoad": True,
    }
    monthly = {
        **base("com.crate.monthly", "monthly"),
        "StartCalendarInterval": {"Day": 1, "Hour": 4, "Minute": 30},
        "RunAtLoad": True,
    }
    return {
        "com.crate.ingest": ingest,
        "com.crate.nightly": nightly,
        "com.crate.monthly": monthly,
    }


def install_launchd(
    config: Config,
    db_path: Path,
    *,
    destination: Path | None = None,
) -> list[Path]:
    """Write LaunchAgent plists. Loading them remains an operator action."""
    target = destination or (Path.home() / "Library" / "LaunchAgents")
    target.mkdir(parents=True, exist_ok=True)
    (config.state_dir / "logs").mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for label, payload in launchd_plists(config, db_path).items():
        path = target / f"{label}.plist"
        with path.open("wb") as handle:
            plistlib.dump(payload, handle, sort_keys=False)
        written.append(path)
    return written
