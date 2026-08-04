from pathlib import Path
from datetime import UTC, datetime, timedelta
import os
import subprocess

from mediafile import MediaFile
import pytest

from conftest import tone_wav
from cr8.audio import bounce_files, choose_mirror_source, sha256_file
from cr8.db import connect, utc_now
from cr8.mirror import (
    SENTINEL,
    _write_tags,
    build_mirror,
    generate_cover_bytes,
    mirror_expectations,
)
from cr8.resolve import resolve_catalog
from cr8.tooling import find_tool
from cr8.verify import run_verify


pytestmark = pytest.mark.skipif(
    find_tool("ffmpeg") is None or find_tool("ffprobe") is None,
    reason="ffmpeg/ffprobe required for mirror integration tests",
)
requires_audiowaveform = pytest.mark.skipif(
    find_tool("audiowaveform") is None,
    reason="audiowaveform required for complete-mirror verification",
)


def _scanless_catalog(connection, config, root, *, duration=0.25):
    source = tone_wav(root / "1-1-24-song.wav", duration_s=duration)
    connection.execute(
        """
        INSERT INTO files(
          relpath, layer, ext, size, mtime, md5, duration_s,
          first_seen, last_seen
        ) VALUES('1-1-24-song.wav', 'curated', '.wav', ?, ?, 'md5', ?, 's', 's')
        """,
        (source.stat().st_size, source.stat().st_mtime, duration),
    )
    resolve_catalog(connection, config)
    connection.execute(
        """
        INSERT INTO song_tags(song_id, dim, value, source)
        SELECT id, 'vibe', 'dreamy', 'human' FROM songs
        """
    )
    connection.execute(
        """
        INSERT INTO song_tags(song_id, dim, value, source)
        SELECT id, 'instr', 'guitar', 'human' FROM songs
        """
    )
    return source


def test_cover_is_deterministic():
    first = generate_cover_bytes("Drown Me", era_color="#3155aa", size=200)
    second = generate_cover_bytes("Drown Me", era_color="#3155aa", size=200)
    assert first == second
    assert first.startswith(b"\xff\xd8")


def test_mediafile_tags_mp3_with_leading_zero_padding(tmp_path):
    path = tmp_path / "padded.mp3"
    subprocess.run(
        [
            str(find_tool("ffmpeg")),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.1",
            str(path),
        ],
        check=True,
    )
    path.write_bytes(b"\0" * 64 + path.read_bytes())
    tags = {
        "title": "Padded",
        "album": "Padded",
        "albumartist": "Hareesh",
        "artist": "Hareesh",
        "track": 1,
        "date": "2026-07-29",
        "genres": [],
        "bpm": None,
        "initial_key": None,
        "CAMELOT": "",
        "STATUS": "demo",
        "ERA": "",
        "INSTR": "",
        "COLLAB": "",
        "MIXROLE": "main",
        "ENERGY": "",
        "SONGID": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "BOUNCEID": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
        "cover_sha256": "unused",
    }
    _write_tags(path, tags, generate_cover_bytes("Padded", size=200))
    media = MediaFile(path)
    assert media.title == "Padded"
    assert media.crate_bounce_id == "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def test_build_projection_ignores_non_id3_use_tags(fixture_config, tmp_path):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        _scanless_catalog(connection, config, root)
        connection.execute(
            """
            INSERT INTO song_tags(song_id, dim, value, source)
            SELECT id, 'use', 'sync-candidate', 'derived' FROM songs
            """
        )
        expected = mirror_expectations(connection, config)
        assert len(expected.items) == 1
        assert expected.items[0].tags["genres"] == ["dreamy"]
        assert "sync-candidate" not in str(expected.items[0].tags)
    finally:
        connection.close()


@requires_audiowaveform
def test_build_is_atomic_incremental_and_tags_round_trip(fixture_config, tmp_path):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    mirror = tmp_path / "mirror"
    try:
        source = _scanless_catalog(connection, config, root)
        (mirror / "tracks").mkdir(parents=True)
        orphan = mirror / "tracks" / "dead.mp3.tmp.999"
        orphan.write_bytes(b"partial")
        first = build_mirror(connection, config, mirror_root=mirror)
        assert first.rebuilt == 1
        assert first.swept_tmp == 1
        assert not orphan.exists()
        row = connection.execute(
            """
            SELECT mf.*, b.public_id AS bounce_public_id,
                   s.public_id AS song_public_id
            FROM mirror_files mf
            JOIN bounces b ON b.id=mf.bounce_id
            JOIN songs s ON s.id=b.song_id
            """
        ).fetchone()
        track = mirror / row["mirror_relpath"]
        peak = mirror / "peaks" / f"{row['bounce_public_id']}.json"
        art = mirror / "art" / f"{row['song_public_id']}.jpg"
        assert track.is_file() and peak.is_file() and art.is_file()
        assert (mirror / SENTINEL).is_file()
        tags = MediaFile(track)
        assert tags.title == "Song"
        assert tags.album == "Song"
        assert tags.albumartist == "Hareesh"
        assert tags.genres == ["dreamy"]
        assert tags.crate_song_id == row["song_public_id"]
        assert tags.crate_bounce_id == row["bounce_public_id"]
        verified = run_verify(connection, config)
        assert "V5 mirror integrity: PASS" in verified.output
        assert "V9 build state: PASS" in verified.output

        second = build_mirror(connection, config, mirror_root=mirror)
        assert (second.rebuilt, second.retagged, second.unchanged) == (0, 0, 1)

        original_mtime = source.stat().st_mtime
        os.utime(source, (original_mtime + 10, original_mtime + 10))
        mtime_only = build_mirror(connection, config, mirror_root=mirror)
        assert mtime_only.rebuilt == 0

        tone_wav(source, frequency=660.0)
        os.utime(source, (original_mtime, original_mtime))
        content_change = build_mirror(connection, config, mirror_root=mirror)
        assert content_change.rebuilt == 1
    finally:
        connection.close()


@requires_audiowaveform
def test_stem_archive_renders_incrementally_and_survives_prune(
    fixture_config, tmp_path
):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    mirror = tmp_path / "mirror"
    try:
        source = _scanless_catalog(connection, config, root)
        bounce = connection.execute(
            "SELECT id, public_id FROM bounces"
        ).fetchone()
        stem_dir = config.state_dir / "stems" / str(bounce["public_id"])
        stem_dir.mkdir(parents=True)
        archive = stem_dir / "vocals.flac"
        subprocess.run(
            [
                str(find_tool("ffmpeg")),
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                str(archive),
            ],
            check=True,
        )
        connection.execute(
            """
            INSERT INTO stem_runs(
              bounce_id, recipe, model_a, model_b, pass_a_done, pass_b_done,
              src_relpath, src_sha256, separator_version, ok
            ) VALUES(?, 'default-v1', 'a', 'b', 1, 1, ?, ?, '0.44.5', 1)
            """,
            (bounce["id"], source.name, sha256_file(source)),
        )
        run_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        stem_public_id = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
        connection.execute(
            """
            INSERT INTO stems(
              public_id, run_id, bounce_id, kind, archive_relpath,
              archive_sha256, duration_s
            ) VALUES(?, ?, ?, 'vocals', ?, ?, 0.25)
            """,
            (
                stem_public_id,
                run_id,
                bounce["id"],
                archive.relative_to(config.state_dir).as_posix(),
                sha256_file(archive),
            ),
        )

        first = build_mirror(connection, config, mirror_root=mirror)
        stem_track = mirror / "tracks" / f"{stem_public_id}.mp3"
        stem_peak = mirror / "peaks" / f"{stem_public_id}.json"
        assert first.total == 2
        assert first.rebuilt == 2
        assert stem_track.is_file() and stem_peak.is_file()
        assert MediaFile(stem_track).crate_mixrole == "vocals"
        assert connection.execute(
            "SELECT mirror_relpath FROM stems"
        ).fetchone()[0] == f"tracks/{stem_public_id}.mp3"
        assert "V5 mirror integrity: PASS" in run_verify(
            connection, config
        ).output

        second = build_mirror(connection, config, mirror_root=mirror)
        assert (second.rebuilt, second.retagged, second.unchanged) == (0, 0, 2)

        source.unlink()
        expired = (datetime.now(UTC) - timedelta(days=31)).replace(
            microsecond=0
        ).isoformat()
        connection.execute("UPDATE files SET missing_since=?", (expired,))
        pruned = build_mirror(
            connection, config, mirror_root=mirror, force_shrink=True
        )
        assert pruned.pruned == 1
        assert archive.is_file()
        assert stem_track.is_file() and stem_peak.is_file()
        assert "V5 mirror integrity: PASS" in run_verify(
            connection, config
        ).output
    finally:
        connection.close()


def test_twin_duration_mismatch_selects_lossless_source(fixture_config, tmp_path):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        wav = _scanless_catalog(connection, config, root, duration=0.25)
        mp3 = root / "1-1-24-song.mp3"
        subprocess.run(
            [
                str(find_tool("ffmpeg")),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                str(mp3),
            ],
            check=True,
        )
        bounce_id = connection.execute("SELECT id FROM bounces").fetchone()[0]
        connection.execute(
            """
            INSERT INTO files(
              relpath, layer, ext, size, mtime, md5, duration_s, bounce_id,
              parse_status, first_seen, last_seen
            ) VALUES('1-1-24-song.mp3', 'curated', '.mp3', ?, ?, 'm', 2.0, ?,
                     'parsed', 's', 's')
            """,
            (mp3.stat().st_size, mp3.stat().st_mtime, bounce_id),
        )
        choice = choose_mirror_source(
            bounce_files(connection, config, bounce_id),
            state_dir=config.state_dir,
        )
        assert choice.source.path == wav
        assert choice.mismatch is not None
        assert choice.encoder_settings == "libmp3lame-cbr-320k"
    finally:
        connection.close()


def test_cascade_guard_refuses_shrunken_catalog(fixture_config, tmp_path):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        _scanless_catalog(connection, config, root)
        connection.execute(
            "INSERT INTO build_state(key, value) VALUES('last_good_count', '2')"
        )
        with pytest.raises(ValueError, match="cascade guard"):
            build_mirror(connection, config, mirror_root=tmp_path / "mirror")
    finally:
        connection.close()


def test_failed_rebuild_withholds_sentinel(
    fixture_config, tmp_path, monkeypatch
):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    mirror = tmp_path / "mirror"
    try:
        source = _scanless_catalog(connection, config, root)
        build_mirror(connection, config, mirror_root=mirror)
        assert (mirror / SENTINEL).is_file()
        tone_wav(source, frequency=660.0)

        def fail_transcode(_choice, temporary, *, ffmpeg):
            del ffmpeg
            temporary.write_bytes(b"partial")
            raise RuntimeError("simulated killed transcode")

        monkeypatch.setattr("cr8.mirror._transcode_or_copy", fail_transcode)
        with pytest.raises(RuntimeError, match="simulated killed transcode"):
            build_mirror(connection, config, mirror_root=mirror)
        assert not (mirror / SENTINEL).exists()
        assert list(mirror.rglob("*.tmp.*"))
    finally:
        connection.close()


@requires_audiowaveform
def test_missing_bounce_artifacts_observe_thirty_day_grace(
    fixture_config, tmp_path
):
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    mirror = tmp_path / "mirror"
    try:
        source = _scanless_catalog(connection, config, root)
        build_mirror(connection, config, mirror_root=mirror)
        track = next((mirror / "tracks").glob("*.mp3"))
        peak = next((mirror / "peaks").glob("*.json"))
        art = next((mirror / "art").glob("*.jpg"))
        source.unlink()
        connection.execute(
            "UPDATE files SET missing_since=?",
            (utc_now(),),
        )
        retained = build_mirror(
            connection, config, mirror_root=mirror, force_shrink=True
        )
        assert retained.pruned == 0
        assert track.is_file() and peak.is_file() and art.is_file()
        assert "V5 mirror integrity: PASS" in run_verify(
            connection, config
        ).output

        expired = (datetime.now(UTC) - timedelta(days=31)).replace(
            microsecond=0
        ).isoformat()
        connection.execute(
            "UPDATE files SET missing_since=?",
            (expired,),
        )
        pruned = build_mirror(
            connection, config, mirror_root=mirror, force_shrink=True
        )
        assert pruned.pruned == 1
        assert not track.exists() and not peak.exists() and not art.exists()
        assert "V5 mirror integrity: PASS" in run_verify(
            connection, config
        ).output
    finally:
        connection.close()


def test_a_bounce_whose_audio_has_not_arrived_is_skipped_not_fatal(
    fixture_config, tmp_path
):
    """One absent file must not stop every other track from being mirrored.

    While a corpus is copying onto a new machine, the catalogue knows about
    files the copy has not reached yet. Raising there abandoned the whole
    build, so a single track that had not landed meant nothing new appeared in
    the app at all - and the ingest tick just printed "bounce has no readable
    source file" and stopped.
    """
    config, root = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        source = _scanless_catalog(connection, config, root)
        initial = mirror_expectations(connection, config)
        assert len(initial.items) == 1
        assert initial.skipped == ()

        # The file the catalogue expects has not been copied over yet.
        source.unlink()

        result = mirror_expectations(connection, config)
        assert result.items == ()
        assert len(result.skipped) == 1
    finally:
        connection.close()


def test_the_sweep_leaves_another_live_builds_temporary_files_alone(tmp_path):
    """Two mirror builds can overlap, and one must not delete the other's work.

    The mirror writes `<name>.tmp.<pid>` and renames it into place. The sweep
    used to delete every `*.tmp.*` under the root, and the ingest tick builds
    the mirror as well and takes no lock - it runs every five minutes, a
    nightly build takes about six. So a tick landing inside the nightly deleted
    the nightly's temporary files and the nightly died on the rename with
    FileNotFoundError, taking the build stage down and leaving 83 tracks with
    stale tags behind it. Seen in production on 1 August.
    """
    from cr8.mirror import _sweep_temporary_files

    root = tmp_path / "mirror" / "tracks"
    root.mkdir(parents=True)

    mine = os.getpid()
    ours = root / f"a.mp3.tmp.{mine}"
    ours.write_bytes(b"")
    # A pid that cannot be running: the sweep should reclaim this one.
    dead = root / "b.mp3.tmp.999999"
    dead.write_bytes(b"")
    # A different, definitely-alive process. Our own parent will do.
    alive = root / f"c.mp3.tmp.{os.getppid()}"
    alive.write_bytes(b"")
    # Not one of ours at all.
    foreign = root / "d.mp3.tmp.partial"
    foreign.write_bytes(b"")

    swept = _sweep_temporary_files(tmp_path / "mirror", pid=mine)

    assert not ours.exists(), "should reclaim its own leftovers"
    assert not dead.exists(), "should reclaim a dead process's leftovers"
    assert alive.exists(), "must not delete a live build's work in progress"
    assert foreign.exists(), "must not guess about names it does not own"
    assert swept == 2
