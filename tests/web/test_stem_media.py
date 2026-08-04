from __future__ import annotations

from fastapi.testclient import TestClient

from cr8.db import connect
from cr8.web.common.queries import track_by_ulid

from conftest import WebFixture


STEM_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FB4"


def _seed_stem(web: WebFixture) -> None:
    connection = connect(web.db_path)
    try:
        bounce_id = int(
            connection.execute(
                "SELECT id FROM bounces WHERE public_id=?",
                (web.bounce_ulids[0],),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO stem_runs(
              bounce_id, recipe, model_a, model_b, pass_a_done, pass_b_done,
              src_relpath, src_sha256, separator_version, ok
            ) VALUES(?, 'default-v1', 'a', 'b', 1, 1, 'source.wav',
                     'source-sha', '0.44.5', 1)
            """,
            (bounce_id,),
        )
        run_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO stems(
              public_id, run_id, bounce_id, kind, archive_relpath,
              archive_sha256, mirror_relpath, duration_s, built_at
            ) VALUES(?, ?, ?, 'vocals', 'stems/test/vocals.flac',
                     'archive-sha', ?, 61.0, '2026-07-29T00:00:00+00:00')
            """,
            (STEM_ULID, run_id, bounce_id, f"tracks/{STEM_ULID}.mp3"),
        )
    finally:
        connection.close()
    (web.mirror / "tracks" / f"{STEM_ULID}.mp3").write_bytes(
        b"ID3" + bytes(range(128))
    )
    (web.mirror / "peaks" / f"{STEM_ULID}.json").write_text(
        '{"version":2,"data":[-1,1]}',
        encoding="utf-8",
    )


def test_stem_ulid_uses_existing_lookup_queue_and_range_media(
    web: WebFixture, owner: TestClient
):
    _seed_stem(web)

    track = track_by_ulid(web.owner_settings, STEM_ULID)
    assert track is not None
    assert track["bounce_ulid"] == STEM_ULID
    assert track["parent_bounce_ulid"] == web.bounce_ulids[0]
    assert track["stem_kind"] == track["version_label"] == "vocals"

    metadata = owner.get(f"/api/tracks/{STEM_ULID}")
    assert metadata.status_code == 200
    assert metadata.json()["audio_url"] == f"/m/{STEM_ULID}"
    partial = owner.get(
        f"/m/{STEM_ULID}",
        headers={"Range": "bytes=0-9"},
    )
    assert partial.status_code == 206
    assert partial.headers["content-range"] == "bytes 0-9/131"
    assert partial.content == b"ID3" + bytes(range(7))
    assert owner.get(f"/peaks/{STEM_ULID}").status_code == 200
