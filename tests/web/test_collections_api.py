from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from cr8.db import connect

from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}


def _create(
    owner: TestClient,
    web: WebFixture,
    *,
    name: str = "API collection",
    bounce_ulids: list[str] | None = None,
) -> str:
    response = owner.post(
        "/api/collections",
        headers=CSRF,
        data={
            "name": name,
            "source": "selection",
            "bounce_ulid": bounce_ulids or web.bounce_ulids[:2],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/api/collections/")
    return location.rsplit("/", 1)[1]


def test_create_collection_api_alias_uses_the_legacy_contract(
    web: WebFixture,
    owner: TestClient,
) -> None:
    rejected = owner.post(
        "/api/collections",
        headers=CSRF,
        data={"name": "Empty", "source": "selection"},
    )
    assert rejected.status_code == 400

    collection_ulid = _create(owner, web, name="Selected songs")

    detail = owner.get(f"/api/collections/{collection_ulid}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["collection"]["ulid"] == collection_ulid
    assert payload["collection"]["name"] == "Selected songs"
    assert [track["bounce_ulid"] for track in payload["tracks"]] == [
        web.bounce_ulids[0],
        web.bounce_ulids[1],
    ]


def test_reorder_collection_api_alias_persists_the_full_order(
    web: WebFixture,
    owner: TestClient,
) -> None:
    collection_ulid = _create(owner, web)
    expected = [web.bounce_ulids[1], web.bounce_ulids[0]]

    response = owner.post(
        f"/api/collections/{collection_ulid}/order",
        headers=CSRF,
        data={"bounce_ulid": expected},
    )
    assert response.status_code == 204

    detail = owner.get(f"/api/collections/{collection_ulid}").json()
    assert [track["bounce_ulid"] for track in detail["tracks"]] == expected


def test_remove_collection_track_api_alias_reindexes_the_collection(
    web: WebFixture,
    owner: TestClient,
) -> None:
    collection_ulid = _create(owner, web)

    response = owner.post(
        f"/api/collections/{collection_ulid}/remove/{web.bounce_ulids[0]}",
        headers=CSRF,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/api/collections/{collection_ulid}"

    detail = owner.get(response.headers["location"]).json()
    assert [track["bounce_ulid"] for track in detail["tracks"]] == [
        web.bounce_ulids[1]
    ]
    connection = connect(web.db_path)
    try:
        position = connection.execute(
            """
            SELECT ci.position
            FROM collection_items AS ci
            JOIN collections AS c ON c.id=ci.collection_id
            WHERE c.ulid=?
            """,
            (collection_ulid,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert position == 0


def test_delete_collection_api_alias_removes_the_collection_and_items(
    web: WebFixture,
    owner: TestClient,
) -> None:
    collection_ulid = _create(owner, web)
    connection = connect(web.db_path)
    try:
        collection_id = connection.execute(
            "SELECT id FROM collections WHERE ulid=?",
            (collection_ulid,),
        ).fetchone()[0]
    finally:
        connection.close()

    response = owner.post(
        f"/api/collections/{collection_ulid}/delete",
        headers=CSRF,
    )
    assert response.status_code == 204
    assert owner.get(f"/api/collections/{collection_ulid}").status_code == 404

    connection = connect(web.db_path)
    try:
        collection_count = connection.execute(
            "SELECT COUNT(*) FROM collections WHERE ulid=?",
            (collection_ulid,),
        ).fetchone()[0]
        item_count = connection.execute(
            "SELECT COUNT(*) FROM collection_items WHERE collection_id=?",
            (collection_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert collection_count == 0
    assert item_count == 0

    missing = owner.post(
        f"/api/collections/{collection_ulid}/delete",
        headers=CSRF,
    )
    assert missing.status_code == 404


def test_a_separated_stem_can_join_a_collection(
    owner: TestClient, web: WebFixture
) -> None:
    # The library's newest rows are often stems; they play through the same
    # track path as bounces, so the availability gate must accept them. This
    # exact selection 400ed in production before the gate learned stems.
    stem_ulid = "01BXZ3NDEKTSV4RRFFQ69G5FAV"
    connection = sqlite3.connect(web.db_path)
    bounce_id = connection.execute(
        "SELECT id FROM bounces ORDER BY id LIMIT 1"
    ).fetchone()[0]
    run_id = connection.execute(
        "SELECT id FROM stem_runs ORDER BY id LIMIT 1"
    ).fetchone()
    if run_id is None:
        connection.execute(
            """
            INSERT INTO stem_runs(
              bounce_id, recipe, model_a, src_relpath, src_sha256,
              separator_version
            ) VALUES(?, 'test', 'test-model', 'src/t.wav', 'sha', 'test')
            """,
            (bounce_id,),
        )
        run_id = connection.execute(
            "SELECT id FROM stem_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    connection.execute(
        """
        INSERT INTO stems(
          public_id, run_id, bounce_id, kind,
          archive_relpath, archive_sha256, mirror_relpath
        ) VALUES(?, ?, ?, 'vocals', 'stems/t.flac', 'sha', 'tracks/stem.mp3')
        """,
        (stem_ulid, run_id[0], bounce_id),
    )
    connection.commit()
    connection.close()

    collection_ulid = _create(
        owner, web, name="With a stem", bounce_ulids=[stem_ulid]
    )
    detail = owner.get(f"/api/collections/{collection_ulid}")
    assert detail.status_code == 200
