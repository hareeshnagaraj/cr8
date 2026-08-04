from pathlib import Path
from types import SimpleNamespace

import pytest

from cr8.db import connect
from cr8.mirror import SENTINEL
from cr8.push import push_mirror


def test_push_guards_and_dry_run(fixture_config, tmp_path, monkeypatch):
    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    mirror = tmp_path / "mirror"
    tracks = mirror / "tracks"
    tracks.mkdir(parents=True)
    (mirror / SENTINEL).write_text("cr8 mirror\n")
    (tracks / "one.mp3").write_bytes(b"track")
    connection.execute(
        "INSERT INTO build_state(key, value) VALUES('last_good_count', '1')"
    )
    monkeypatch.setattr("cr8.push.find_tool", lambda *args, **kwargs: Path("/usr/bin/rsync"))
    called = {}

    def fake_run(executable, args, timeout=None):
        called["args"] = list(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("cr8.push.run_tool", fake_run)
    try:
        summary = push_mirror(
            connection,
            config,
            str(tmp_path / "jukebox"),
            mirror_root=mirror,
            dry_run=True,
        )
        assert summary.dry_run
        assert "--delete" in called["args"]
        assert "--max-delete=50" in called["args"]
        assert "--dry-run" in called["args"]
        with pytest.raises(ValueError, match="colon-free"):
            push_mirror(
                connection,
                config,
                "host:/bad:path",
                mirror_root=mirror,
                dry_run=True,
            )
        connection.execute(
            "INSERT INTO songs(id, slug, title) VALUES(1, 'one', 'One')"
        )
        connection.executemany(
            "INSERT INTO bounces(id, song_id, source_stem) VALUES(?, 1, ?)",
            [(1, "one"), (2, "two")],
        )
        connection.executemany(
            """
            INSERT INTO files(
              relpath, layer, bounce_id, parse_status, first_seen, last_seen
            ) VALUES(?, 'curated', ?, 'parsed', 's', 's')
            """,
            [("one.wav", 1), ("two.wav", 2)],
        )
        with pytest.raises(ValueError, match="below 90%"):
            push_mirror(
                connection,
                config,
                str(tmp_path / "jukebox"),
                mirror_root=mirror,
                dry_run=True,
            )
    finally:
        connection.close()
