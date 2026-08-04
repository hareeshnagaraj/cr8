from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import os
import time

from conftest import old_audio
from cr8.db import connect
from cr8.paths import archive_relpath
from cr8.scan import scan_catalog
from cr8.verify import run_verify


def test_scan_layers_debounce_missing_and_idempotency(fixture_config, tmp_path):
    config, root = fixture_config
    old_audio(root / "1-1-24-loose.wav")
    old_audio(root / "1-1-24-loose.mp3")
    old_audio(root / "curated" / "2-2-24-inside.wav")
    old_audio(root / "Session Project" / "Samples" / "Imported" / "kick.wav")
    old_audio(root / "other" / "reference.wav")
    old_audio(root / "unexpected" / "stray.wav")
    old_audio(root / "curated" / "4-4-24-silence.wav", b"")
    fresh = root / "curated" / "3-3-24-fresh.wav"
    fresh.write_bytes(b"still-writing")
    (root / "Session Project" / "song.als").write_bytes(b"ableton")
    (root / "Session Project" / "Backup").mkdir()
    (root / "Session Project" / "Backup" / "song.als").write_bytes(b"backup")

    connection = connect(tmp_path / "catalog.db")
    try:
        first = scan_catalog(
            connection, config, debounce_seconds=120, stability_wait_seconds=0
        )
        assert first.new == 7
        assert first.skipped_debounce == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM files WHERE relpath=?", (fresh.relative_to(root).as_posix(),)
        ).fetchone()[0] == 0
        layers = {
            row["relpath"]: row["layer"]
            for row in connection.execute("SELECT relpath, layer FROM files")
        }
        assert layers["1-1-24-loose.wav"] == "curated"
        assert layers["curated/2-2-24-inside.wav"] == "curated"
        assert layers["Session Project/Samples/Imported/kick.wav"] == "project"
        assert layers["other/reference.wav"] == "other"
        assert layers["unexpected/stray.wav"] == "other"
        assert connection.execute(
            "SELECT COUNT(*) FROM review_queue WHERE kind='stray_location'"
        ).fetchone()[0] == 1
        project = connection.execute("SELECT * FROM projects").fetchone()
        assert project["als_count"] == 1
        assert project["backup_als_count"] == 1
        public_ids = {
            "songs": [
                row["public_id"]
                for row in connection.execute("SELECT public_id FROM songs ORDER BY id")
            ],
            "bounces": [
                row["public_id"]
                for row in connection.execute("SELECT public_id FROM bounces ORDER BY id")
            ],
        }

        second = scan_catalog(
            connection, config, debounce_seconds=120, stability_wait_seconds=0
        )
        assert (second.new, second.changed) == (0, 0)
        assert second.unchanged == 7
        assert public_ids == {
            "songs": [
                row["public_id"]
                for row in connection.execute("SELECT public_id FROM songs ORDER BY id")
            ],
            "bounces": [
                row["public_id"]
                for row in connection.execute("SELECT public_id FROM bounces ORDER BY id")
            ],
        }

        missing_path = root / "curated" / "2-2-24-inside.wav"
        missing_path.unlink()
        scan_catalog(connection, config, debounce_seconds=120, stability_wait_seconds=0)
        assert connection.execute(
            "SELECT missing_since FROM files WHERE relpath='curated/2-2-24-inside.wav'"
        ).fetchone()[0]
        old_audio(missing_path)
        scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
        assert connection.execute(
            "SELECT missing_since FROM files WHERE relpath='curated/2-2-24-inside.wav'"
        ).fetchone()[0] is None
    finally:
        connection.close()


def test_curated_tick_preserves_the_last_full_scan_verification_token(
    fixture_config, tmp_path
):
    config, root = fixture_config
    old_audio(root / "1-1-24-first.wav")
    old_audio(root / "Session Project" / "Samples" / "Imported" / "kick.wav")
    connection = connect(tmp_path / "catalog.db")
    try:
        scan_catalog(
            connection,
            config,
            debounce_seconds=0,
            stability_wait_seconds=0,
        )
        old_audio(root / "curated" / "2-2-24-second.wav")
        scan_catalog(
            connection,
            config,
            debounce_seconds=0,
            stability_wait_seconds=0,
            curated_only=True,
        )

        verified = run_verify(connection, config)
        assert not any(item.startswith("V1:") for item in verified.findings)
    finally:
        connection.close()


def test_a_mass_disappearance_is_refused_rather_than_recorded(
    fixture_config, tmp_path
):
    """A corpus that is still copying must not be read as a mass deletion.

    This is the failure that motivated the guard: the catalogue was moved to a
    machine whose copy of the corpus was 70% complete, and the first ingest tick
    there would have marked every file the copy had not reached yet as missing -
    dropping them out of the mirror, out of search and out of people's queues,
    with nothing in the app to say why.
    """
    config, root = fixture_config
    made = [old_audio(root / "curated" / f"1-1-24-take-{n:03}.wav") for n in range(40)]

    connection = connect(tmp_path / "catalog.db")
    try:
        scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 40

        # The drive did not mount / the sync is half done.
        for path in made:
            path.unlink()

        summary = scan_catalog(
            connection, config, debounce_seconds=0, stability_wait_seconds=0
        )
        assert summary.missing_guarded == 40
        assert summary.missing == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_deleting_a_few_files_still_marks_them_missing(fixture_config, tmp_path):
    """The guard must not swallow ordinary tidying up.

    Deleting a handful of takes is a thing people do, and the catalogue is
    supposed to notice. Only a disappearance too large to be deliberate is
    refused.
    """
    config, root = fixture_config
    made = [old_audio(root / "curated" / f"1-1-24-take-{n:03}.wav") for n in range(40)]

    connection = connect(tmp_path / "catalog.db")
    try:
        scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
        for path in made[:3]:
            path.unlink()

        summary = scan_catalog(
            connection, config, debounce_seconds=0, stability_wait_seconds=0
        )
        assert summary.missing_guarded == 0
        assert summary.missing == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL"
        ).fetchone()[0] == 3
    finally:
        connection.close()


def test_archive_root_uses_main_curation_laws_and_mtime_dates(
    fixture_config, tmp_path
):
    config, root = fixture_config
    (root / "curated").mkdir()
    archive = tmp_path / "2021-New-Projects"
    archive.mkdir()
    config = replace(
        config,
        corpus=replace(config.corpus, archive_roots=(archive,)),
    )
    old_audio(root / "1-1-24-main.wav")
    bounce = old_audio(archive / "nameless-bounce.wav")
    mtime = datetime(2022, 6, 15, 12, tzinfo=UTC).timestamp()
    os.utime(bounce, (mtime, mtime))
    project = archive / "X Project"
    (project / "Samples").mkdir(parents=True)
    (project / "song.als").write_bytes(b"ableton")
    old_audio(project / "Samples" / "kick.wav")

    connection = connect(tmp_path / "catalog.db")
    try:
        summary = scan_catalog(
            connection, config, debounce_seconds=0, stability_wait_seconds=0
        )
        bounce_relpath = archive_relpath(archive, bounce)
        sample_relpath = archive_relpath(archive, project / "Samples" / "kick.wav")
        rows = {
            str(row["relpath"]): row
            for row in connection.execute(
                "SELECT relpath, layer, bounce_id FROM files"
            )
        }
        assert rows["1-1-24-main.wav"]["layer"] == "curated"
        assert rows[bounce_relpath]["layer"] == "curated"
        assert rows[bounce_relpath]["bounce_id"] is not None
        assert rows[sample_relpath]["layer"] == "project"
        assert rows[sample_relpath]["bounce_id"] is None
        assert connection.execute(
            "SELECT relpath FROM projects"
        ).fetchone()[0] == archive_relpath(archive, "X Project")
        dated = connection.execute(
            """
            SELECT b.bounce_date, b.date_source
            FROM bounces AS b JOIN files AS f ON f.bounce_id=b.id
            WHERE f.relpath=?
            """,
            (bounce_relpath,),
        ).fetchone()
        assert (dated["bounce_date"], dated["date_source"]) == (
            "2022-06-15",
            "mtime",
        )
        assert summary.root_warnings == ()
    finally:
        connection.close()


def test_scan_warns_when_no_curated_dirs_match(fixture_config, tmp_path):
    config, _root = fixture_config
    mismatched = replace(
        config,
        corpus=replace(
            config.corpus,
            curated_dirs=frozenset({"does-not-exist-here"}),
        ),
    )
    connection = connect(tmp_path / "warn.sqlite")
    try:
        summary = scan_catalog(
            connection, mismatched, stability_wait_seconds=0
        )
    finally:
        connection.close()
    assert any(
        "0 of 1 curated_dirs matched" in warning
        for warning in summary.root_warnings
    )


def test_missing_archive_root_is_loud_and_does_not_delete_anything(
    fixture_config, tmp_path
):
    config, root = fixture_config
    (root / "curated").mkdir()
    archive = tmp_path / "2021-New-Projects"
    archive.mkdir()
    config = replace(
        config,
        corpus=replace(config.corpus, archive_roots=(archive,)),
    )
    old_audio(root / "1-1-24-main.wav")
    old_audio(archive / "1-1-22-archive.wav")
    connection = connect(tmp_path / "catalog.db")
    try:
        scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
        ingest = scan_catalog(
            connection,
            config,
            debounce_seconds=0,
            stability_wait_seconds=0,
            curated_only=True,
        )
        assert ingest.missing == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL"
        ).fetchone()[0] == 0
        archive.rename(tmp_path / "archive-unmounted")
        old_audio(root / "2-2-24-main-new.wav")

        summary = scan_catalog(
            connection, config, debounce_seconds=0, stability_wait_seconds=0
        )
        assert summary.new == 1
        assert summary.missing == 0
        assert summary.missing_guarded == 1
        assert len(summary.root_warnings) == 1
        assert str(archive) in summary.root_warnings[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_mass_disappearance_threshold_is_evaluated_per_archive_root(
    fixture_config, tmp_path
):
    config, _ = fixture_config
    shrinking = tmp_path / "shrinking-archive"
    healthy = tmp_path / "healthy-archive"
    shrinking.mkdir()
    project = healthy / "Bulk Project" / "Samples"
    project.mkdir(parents=True)
    config = replace(
        config,
        corpus=replace(config.corpus, archive_roots=(shrinking, healthy)),
    )
    shrinking_files = [
        old_audio(shrinking / f"1-1-22-take-{index:03}.wav")
        for index in range(40)
    ]
    for index in range(300):
        old_audio(project / f"sample-{index:03}.wav")

    connection = connect(tmp_path / "catalog.db")
    try:
        scan_catalog(connection, config, debounce_seconds=0, stability_wait_seconds=0)
        for path in shrinking_files[:26]:
            path.unlink()
        summary = scan_catalog(
            connection, config, debounce_seconds=0, stability_wait_seconds=0
        )
        assert summary.missing == 0
        assert summary.missing_guarded == 26
        assert connection.execute(
            "SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_a_classified_directory_leaves_the_stray_list(fixture_config, tmp_path):
    from cr8.db import connect
    from cr8.scan import scan_catalog

    config, _root = fixture_config
    stray = config.corpus.root / "mystery-bounces"
    stray.mkdir()
    (stray / "one.wav").write_bytes(b"RIFF0000WAVE")
    connection = connect(config.db_path)
    try:
        scan_catalog(connection, config, stability_wait_seconds=0)
        open_strays = connection.execute(
            "SELECT payload FROM review_queue"
            " WHERE kind='stray_location' AND status='open'"
        ).fetchall()
        assert any("mystery-bounces" in str(row["payload"]) for row in open_strays)

        from dataclasses import replace

        curated = replace(
            config,
            corpus=replace(
                config.corpus,
                curated_dirs=frozenset(
                    set(config.corpus.curated_dirs) | {"mystery-bounces"}
                ),
            ),
        )
        scan_catalog(connection, curated, stability_wait_seconds=0)
        still_open = connection.execute(
            "SELECT payload FROM review_queue"
            " WHERE kind='stray_location' AND status='open'"
        ).fetchall()
        assert not any(
            "mystery-bounces" in str(row["payload"]) for row in still_open
        )
    finally:
        connection.close()
