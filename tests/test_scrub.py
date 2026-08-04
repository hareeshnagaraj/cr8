from conftest import old_audio
from cr8.db import connect
from cr8.scrub import scrub


def test_scrub_anchors_then_reports_content_change(fixture_config, tmp_path):
    config, root = fixture_config
    path = old_audio(root / "curated" / "song.wav", b"first")
    connection = connect(tmp_path / "catalog.db")
    try:
        connection.execute(
            """
            INSERT INTO files(
              id, relpath, layer, ext, size, mtime, md5, first_seen, last_seen
            ) VALUES(8, 'curated/song.wav', 'curated', '.wav', ?, ?, 'm', 's', 's')
            """,
            (path.stat().st_size, path.stat().st_mtime),
        )
        initial = scrub(connection, config, bucket=0, notify=False)
        assert (initial.checked, initial.anchored, initial.exit_code) == (1, 1, 0)
        path.write_bytes(b"second")
        changed = scrub(connection, config, bucket=0, notify=False)
        assert changed.exit_code == 1
        assert changed.mismatches and "sha256 changed" in changed.mismatches[0]
        assert changed.report_path.is_file()
    finally:
        connection.close()
