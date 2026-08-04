from __future__ import annotations

from fastapi.testclient import TestClient

from cr8.db import connect
from cr8.web.common.database import migrate

from conftest import SECOND_BOUNCE, WebFixture


CSRF = {"X-CR8-Request": "1"}


def test_derived_tags_are_visible_filterable_and_never_replace_human_provenance(
    web: WebFixture, owner: TestClient
):
    connection = connect(web.db_path)
    try:
        song_id = int(
            connection.execute(
                "SELECT id FROM songs WHERE public_id=?",
                (web.song_ulids[0],),
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE bounces
            SET source_stem='stayhere-vox-gtar-henry',
                mixrole='vox', collab_raw='henry'
            WHERE public_id=?
            """,
            (SECOND_BOUNCE,),
        )
        connection.execute(
            """
            INSERT INTO song_tags(
              song_id, dim, value, source, author, created_at
            ) VALUES(?, 'instr', 'guitar', 'human', 'curator', '2026-01-01')
            """,
            (song_id,),
        )
        connection.execute(
            "UPDATE meta SET value='0' WHERE key='web_schema_version'"
        )
    finally:
        connection.close()

    migrate(web.db_path)
    connection = connect(web.db_path)
    try:
        rows = {
            (str(row["dim"]), str(row["value"])): (
                str(row["source"]),
                str(row["author"]),
            )
            for row in connection.execute(
                """
                SELECT dim, value, source, author FROM song_tags
                WHERE song_id=?
                """,
                (song_id,),
            )
        }
    finally:
        connection.close()
    assert rows[("instr", "guitar")] == ("human", "curator")
    assert rows[("instr", "vocals")][0] == "mixrole"
    assert rows[("collab", "henry")][0] == "filename"

    detail = owner.get(f"/songs/{web.song_ulids[0]}")
    assert detail.status_code == 200
    assert "catalog knowledge" in detail.text
    assert "instr · vocals" in detail.text
    assert "mixrole" in detail.text
    assert "collab · henry" in detail.text
    filtered = owner.get("/", params={"dim": "instr", "value": "vocals"})
    assert "Stayhere" in filtered.text

    response = owner.post(
        "/selection",
        headers=CSRF,
        data={
            "song_ulid": web.song_ulids[0],
            "instr": "vocals",
        },
    )
    assert response.status_code == 200
    connection = connect(web.db_path)
    try:
        promoted = connection.execute(
            """
            SELECT source, author FROM song_tags
            WHERE song_id=? AND dim='instr' AND value='vocals'
            """,
            (song_id,),
        ).fetchone()
    finally:
        connection.close()
    assert (promoted["source"], promoted["author"]) == ("human", "hareesh")


def test_chromaprint_neighbours_accelerate_only_confirmed_human_tags(
    web: WebFixture, owner: TestClient
):
    connection = connect(web.db_path)
    try:
        source_id = int(
            connection.execute(
                "SELECT id FROM songs WHERE public_id=?",
                (web.song_ulids[0],),
            ).fetchone()[0]
        )
        target_id = int(
            connection.execute(
                "SELECT id FROM songs WHERE public_id=?",
                (web.song_ulids[1],),
            ).fetchone()[0]
        )
        fingerprint = "1,2,3,4,5,6,7,8"
        connection.execute(
            """
            UPDATE files SET fingerprint=?
            WHERE bounce_id=(
              SELECT id FROM bounces WHERE public_id=?
            )
            """,
            (fingerprint, SECOND_BOUNCE),
        )
        connection.execute(
            """
            UPDATE files SET fingerprint=?
            WHERE bounce_id=(
              SELECT id FROM bounces WHERE public_id=?
            )
            """,
            (fingerprint, web.bounce_ulids[1]),
        )
        connection.executemany(
            """
            INSERT INTO song_tags(
              song_id, dim, value, source, author, created_at
            ) VALUES(?, ?, ?, ?, ?, '2026-01-01')
            """,
            [
                (source_id, "vibe", "airy", "human", "owner"),
                (source_id, "instr", "guitar", "human", "owner"),
                (target_id, "vibe", "airy", "human", "curator"),
                (target_id, "instr", "guitar", "proposed", "catalog"),
            ],
        )
    finally:
        connection.close()

    detail = owner.get(f"/songs/{web.song_ulids[0]}")
    assert "Diamond" in detail.text
    assert "100% chromaprint match" in detail.text
    applied = owner.post(
        f"/songs/{web.song_ulids[0]}/apply-neighbours",
        headers=CSRF,
        data={"neighbour_ulid": web.song_ulids[1]},
    )
    assert applied.status_code == 200

    connection = connect(web.db_path)
    try:
        target = {
            (str(row["dim"]), str(row["value"])): (
                str(row["source"]),
                str(row["author"]),
            )
            for row in connection.execute(
                """
                SELECT dim, value, source, author FROM song_tags
                WHERE song_id=?
                """,
                (target_id,),
            )
        }
    finally:
        connection.close()
    assert target[("vibe", "airy")] == ("human", "curator")
    assert target[("instr", "guitar")] == ("human", "owner:neighbours")


def test_dig_quality_floor_skips_short_status_and_filename_sketches(
    web: WebFixture, owner: TestClient
):
    connection = connect(web.db_path)
    try:
        connection.execute(
            "UPDATE songs SET status='idea' WHERE public_id=?",
            (web.song_ulids[0],),
        )
        connection.execute(
            "UPDATE bounces SET source_stem='diamond-sketch' WHERE public_id=?",
            (web.bounce_ulids[1],),
        )
    finally:
        connection.close()

    owner_page = owner.get("/", params={"skip_sketches": "true"})
    assert 'skip sketches under 90s</span>' in owner_page.text
    assert 'aria-pressed="true"' in owner_page.text
    owner_ids = {
        item["id"]
        for item in owner.get(
            "/api/dig", params={"skip_sketches": "true"}
        ).json()["tracks"]
    }
    assert SECOND_BOUNCE not in owner_ids
    assert web.bounce_ulids[1] not in owner_ids
