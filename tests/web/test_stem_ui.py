from __future__ import annotations

from fastapi.testclient import TestClient

from cr8.db import connect

from conftest import WebFixture


CSRF = {"X-CR8-Request": "1", "HX-Request": "true"}
LATEST_BOUNCE = "01ARZ3NDEKTSV4RRFFQ69G5FB3"


def _seed_completed_stems(web: WebFixture) -> None:
    connection = connect(web.db_path)
    try:
        connection.execute(
            "UPDATE bounces SET mixrole='acap' WHERE public_id=?",
            (web.bounce_ulids[0],),
        )
        bounce_id = int(
            connection.execute(
                "SELECT id FROM bounces WHERE public_id=?",
                (LATEST_BOUNCE,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO stem_runs(
              bounce_id, recipe, model_a, model_b, pass_a_done, pass_b_done,
              src_relpath, src_sha256, separator_version, ok
            ) VALUES(?, 'default-v1', 'a', 'b', 1, 1,
                     'Stayhere.wav', 'source-sha', '0.44.5', 1)
            """,
            (bounce_id,),
        )
        run_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        for index, kind in enumerate(("vocals", "other")):
            public_id = f"stem-ui-{index}"
            connection.execute(
                """
                INSERT INTO stems(
                  public_id, run_id, bounce_id, kind, archive_relpath,
                  archive_sha256, mirror_relpath, duration_s, built_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 61, '2026-07-29T00:00:00+00:00')
                """,
                (
                    public_id,
                    run_id,
                    bounce_id,
                    kind,
                    f"stems/test/{kind}.flac",
                    f"sha-{kind}",
                    f"tracks/{public_id}.mp3",
                ),
            )
    finally:
        connection.close()


def test_song_stems_empty_enqueue_and_active_poll(
    web: WebFixture, owner: TestClient
):
    page = owner.get(f"/songs/{web.song_ulids[0]}")
    assert page.status_code == 200
    assert "no stems yet" in page.text
    assert ">separate<" in page.text

    queued = owner.post(
        f"/stems/{LATEST_BOUNCE}",
        headers=CSRF,
        data={"recipe": "default-v1"},
    )
    assert queued.status_code == 202
    assert "queued" in queued.text
    assert 'hx-trigger="every 5s"' in queued.text
    duplicate = owner.post(
        f"/stems/{LATEST_BOUNCE}",
        headers=CSRF,
        data={"recipe": "default-v1"},
    )
    assert duplicate.status_code == 202
    connection = connect(web.db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    finally:
        connection.close()


def test_completed_stems_are_playable_honest_and_offer_hq(
    web: WebFixture, owner: TestClient
):
    _seed_completed_stems(web)

    page = owner.get(f"/songs/{web.song_ulids[0]}")

    assert page.status_code == 200
    assert 'data-track-url="/api/tracks/stem-ui-0"' in page.text
    assert "vocals" in page.text
    assert "other" in page.text and "leftovers" in page.text
    assert "acap" in page.text and "source" in page.text
    assert "default-v1" in page.text
    assert "redo in high quality" in page.text
    assert "/shares" not in page.text


def test_multi_selection_stems_queues_latest_bounce_per_song(
    web: WebFixture, owner: TestClient
):
    response = owner.post(
        "/selection",
        headers={"X-CR8-Request": "1"},
        data={
            "song_ulid": web.song_ulids[:2],
            "action": "stems",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "<html" not in response.text.casefold()
    assert "Queued stems for 2 songs." in response.text
    connection = connect(web.db_path)
    try:
        rows = connection.execute(
            """
            SELECT j.priority, b.public_id
            FROM jobs j JOIN bounces b ON b.id=j.target_id
            ORDER BY b.public_id
            """
        ).fetchall()
        assert len(rows) == 2
        assert {row["public_id"] for row in rows} == {
            web.bounce_ulids[1],
            "01ARZ3NDEKTSV4RRFFQ69G5FB3",
        }
        assert {row["priority"] for row in rows} == {0}
    finally:
        connection.close()
