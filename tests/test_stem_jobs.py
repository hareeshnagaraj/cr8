from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess

from cr8.db import connect
from cr8.stems import (
    STEM_KINDS,
    claim_stem_job,
    enqueue_stem_job,
    run_stem_worker,
)


BOUNCE_A = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
BOUNCE_B = "01ARZ3NDEKTSV4RRFFQ69G5FAX"


def _catalog(config) -> Path:
    db_path = config.state_dir / "catalog.db"
    connection = connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO songs(slug, title, public_id)
            VALUES('song', 'Song', '01ARZ3NDEKTSV4RRFFQ69G5FAV')
            """
        )
        song_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.executemany(
            """
            INSERT INTO bounces(public_id, song_id, source_stem)
            VALUES(?, ?, ?)
            """,
            (
                (BOUNCE_A, song_id, "song-a"),
                (BOUNCE_B, song_id, "song-b"),
            ),
        )
    finally:
        connection.close()
    return db_path


class SuccessfulRunner:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.calls: list[tuple[Path, tuple[object, ...], float | None]] = []

    def __call__(self, executable, args, *, timeout=None, env=None):
        del env
        values = tuple(args)
        self.calls.append((Path(executable), values, timeout))
        bounce_ulid = str(values[values.index("separate") + 1])
        connection = sqlite3.connect(self.db_path, timeout=0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            # This immediate writer lock proves the worker closed its claim connection.
            connection.execute("BEGIN IMMEDIATE")
            bounce_id = int(
                connection.execute(
                    "SELECT id FROM bounces WHERE public_id=?",
                    (bounce_ulid,),
                ).fetchone()["id"]
            )
            connection.execute(
                """
                INSERT INTO stem_runs(
                  bounce_id, recipe, model_a, model_b, pass_a_done, pass_b_done,
                  src_relpath, src_sha256, separator_version, ok
                ) VALUES(?, 'default-v1', 'a', 'b', 1, 1,
                         'source.wav', 'source-sha', '0.44.5', 1)
                """,
                (bounce_id,),
            )
            run_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            for index, kind in enumerate(STEM_KINDS):
                connection.execute(
                    """
                    INSERT INTO stems(
                      public_id, run_id, bounce_id, kind, archive_relpath,
                      archive_sha256, duration_s
                    ) VALUES(?, ?, ?, ?, ?, ?, 60)
                    """,
                    (
                        f"stem-{bounce_id}-{index}",
                        run_id,
                        bounce_id,
                        kind,
                        f"stems/{bounce_ulid}/{kind}.flac",
                        f"sha-{kind}",
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        return subprocess.CompletedProcess(
            [str(executable), *map(str, values)],
            0,
            stdout="ok\n",
            stderr="",
        )


class FailedRunner:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, executable, args, *, timeout=None, env=None):
        del timeout, env
        self.calls += 1
        return subprocess.CompletedProcess(
            [str(executable), *map(str, args)],
            2,
            stdout="",
            stderr="simulated separator failure",
        )


def test_enqueue_is_idempotent_and_claim_prefers_priority(fixture_config):
    config, _ = fixture_config
    db_path = _catalog(config)
    first = enqueue_stem_job(db_path, BOUNCE_A, priority=0)
    assert enqueue_stem_job(db_path, BOUNCE_A, priority=100) == first
    second = enqueue_stem_job(db_path, BOUNCE_B, priority=100)

    claimed = claim_stem_job(db_path, "worker-one")

    assert claimed is not None
    assert claimed.ulid == second
    connection = connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
        row = connection.execute(
            "SELECT attempts, lease_owner, lease_until FROM jobs WHERE ulid=?",
            (second,),
        ).fetchone()
        assert row["attempts"] == 1
        assert row["lease_owner"] == "worker-one"
        assert row["lease_until"]
    finally:
        connection.close()


def test_expired_lease_reclaims_then_terminal_failure_alerts(fixture_config):
    config, _ = fixture_config
    db_path = _catalog(config)
    job_ulid = enqueue_stem_job(db_path, BOUNCE_A, max_attempts=2)
    first = claim_stem_job(db_path, "worker-one")
    assert first is not None and first.attempts == 1
    connection = connect(db_path)
    try:
        connection.execute(
            "UPDATE jobs SET lease_until='2000-01-01 00:00:00' WHERE ulid=?",
            (job_ulid,),
        )
    finally:
        connection.close()

    reclaimed = claim_stem_job(db_path, "worker-two")

    assert reclaimed is not None
    assert reclaimed.ulid == job_ulid
    assert reclaimed.attempts == 2
    runner = FailedRunner()
    summary = run_stem_worker(
        db_path,
        config,
        config_path=config.state_dir / "config.toml",
        drain=False,
        worker_id="worker-three",
        runner=runner,
    )
    # The unexpired reclaimed lease cannot be stolen by another worker.
    assert summary.claimed == 0
    connection = connect(db_path)
    try:
        connection.execute(
            "UPDATE jobs SET lease_until='2000-01-01 00:00:00' WHERE ulid=?",
            (job_ulid,),
        )
    finally:
        connection.close()
    assert claim_stem_job(db_path, "worker-three") is None
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT state, attempts, error FROM jobs WHERE ulid=?",
            (job_ulid,),
        ).fetchone()
        assert row["state"] == "failed"
        assert row["attempts"] == 2
        assert "maximum attempts" in row["error"]
        assert connection.execute(
            """
            SELECT COUNT(*) FROM app_alerts
            WHERE kind='stem_job_failed' AND severity='critical'
            """
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_worker_closes_db_during_subprocess_and_completes(fixture_config):
    config, _ = fixture_config
    db_path = _catalog(config)
    job_ulid = enqueue_stem_job(db_path, BOUNCE_A)
    runner = SuccessfulRunner(db_path)

    summary = run_stem_worker(
        db_path,
        config,
        config_path=config.state_dir / "config.toml",
        drain=False,
        worker_id="worker-one",
        runner=runner,
    )

    assert summary.completed == 1
    assert len(runner.calls) == 1
    executable, args, timeout = runner.calls[0]
    assert executable.name.startswith("python")
    assert args[:3] == ("-m", "cr8.cli", "--config")
    assert args[args.index("separate") + 1] == BOUNCE_A
    assert args[args.index("--job-ulid") + 1] == job_ulid
    assert timeout == 7200
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT state, attempts, lease_owner, lease_until FROM jobs WHERE ulid=?",
            (job_ulid,),
        ).fetchone()
        assert tuple(row) == ("done", 1, None, None)
        assert connection.execute("SELECT COUNT(*) FROM stems").fetchone()[0] == 5
    finally:
        connection.close()


def test_worker_retries_to_limit_alerts_and_honors_pause(fixture_config):
    config, _ = fixture_config
    db_path = _catalog(config)
    job_ulid = enqueue_stem_job(db_path, BOUNCE_A, max_attempts=2)
    paused = config.state_dir / "stems" / ".paused"
    paused.parent.mkdir(parents=True)
    paused.touch()
    runner = FailedRunner()
    waiting = run_stem_worker(
        db_path,
        config,
        config_path=config.state_dir / "config.toml",
        drain=True,
        runner=runner,
    )
    assert waiting.paused and waiting.claimed == 0
    paused.unlink()

    summary = run_stem_worker(
        db_path,
        config,
        config_path=config.state_dir / "config.toml",
        drain=True,
        runner=runner,
    )

    assert (summary.claimed, summary.retried, summary.failed) == (2, 1, 1)
    assert runner.calls == 2
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT state, attempts, error FROM jobs WHERE ulid=?",
            (job_ulid,),
        ).fetchone()
        assert row["state"] == "failed"
        assert row["attempts"] == 2
        assert "simulated separator failure" in row["error"]
        assert connection.execute(
            "SELECT COUNT(*) FROM app_alerts WHERE kind='stem_job_failed'"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_a_second_worker_stands_down_while_one_is_running(tmp_path, fixture_config):
    """Separation is minutes of torch across every core, on the box serving
    the app. Job leases stop two workers taking the same job; they did not
    stop them taking different ones. On a timer, a drain outliving its own
    interval would stack a new worker on itself every tick.
    """
    from cr8.automation import FileLock
    from cr8.stems import run_stem_worker

    config, _ = fixture_config
    held = FileLock(config.state_dir / ".cr8-stems.lock")
    held.__enter__()
    try:
        summary = run_stem_worker(
            tmp_path / "catalog.db",
            config,
            config_path=tmp_path / "config.toml",
            drain=True,
        )
        # Stood down without touching the queue or the database.
        assert (summary.claimed, summary.completed, summary.failed) == (0, 0, 0)
        assert not summary.paused
    finally:
        held.__exit__()
