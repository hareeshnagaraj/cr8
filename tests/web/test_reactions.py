from __future__ import annotations

from fastapi.testclient import TestClient

from cr8.db import connect

from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}


def test_commit_ack_and_append_only_owner_reactions(
    web: WebFixture, owner: TestClient
):
    bounce = web.bounce_ulids[0]
    first = owner.post(f"/reactions/{bounce}/heart", headers=CSRF)
    assert first.status_code == 200
    assert 'aria-pressed="true"' in first.text
    assert 'hx-select="unset"' in first.text
    second = owner.post(f"/reactions/{bounce}/heart", headers=CSRF)
    assert 'aria-pressed="false"' in second.text
    third = owner.post(f"/reactions/{bounce}/heart", headers=CSRF)
    assert 'aria-pressed="true"' in third.text
    chip = owner.post(
        f"/reactions/{bounce}/chip",
        headers=CSRF,
        data={"value": "dreamy"},
    )
    assert chip.status_code == 200
    assert 'aria-pressed="true"' in chip.text
    assert 'hx-select="unset"' in chip.text

    connection = connect(web.db_path)
    rows = connection.execute(
        """
        SELECT * FROM reactions
        WHERE bounce_ulid=? AND actor='hareesh' AND kind='heart'
        ORDER BY id
        """,
        (bounce,),
    ).fetchall()
    connection.close()
    assert len(rows) == 2
    assert rows[0]["deleted_at"] is not None
    assert rows[1]["deleted_at"] is None
def test_triage_three_tracks_sets_keeper_without_status_change(
    web: WebFixture, owner: TestClient
):
    for bounce, value in zip(
        web.bounce_ulids[:3], ("gem", "keep", "archive"), strict=True
    ):
        response = owner.post(
            f"/triage/{bounce}", headers=CSRF, data={"value": value}
        )
        assert response.status_code == 200
        assert 'id="today-count"' in response.text
        assert 'hx-swap-oob="innerHTML"' in response.text
    connection = connect(web.db_path)
    first = connection.execute(
        """
        SELECT s.keeper, s.status FROM songs s
        JOIN bounces b ON b.song_id=s.id WHERE b.public_id=?
        """,
        (web.bounce_ulids[0],),
    ).fetchone()
    verdicts = connection.execute(
        """
        SELECT COUNT(*) FROM reactions
        WHERE actor='hareesh' AND kind='verdict' AND deleted_at IS NULL
        """
    ).fetchone()[0]
    connection.close()
    assert first["keeper"] == 5
    assert first["status"] == "demo"
    assert verdicts == 3


def test_multi_selection_ops_are_owner_http_mutations(
    web: WebFixture, owner: TestClient
):
    response = owner.post(
        "/selection",
        headers=CSRF,
        data={
            "song_ulid": web.song_ulids[:2],
            "status": "mixed",
            "instr": "guitar",
            "collab": "henry",
        },
    )
    assert response.status_code == 200
    connection = connect(web.db_path)
    assert connection.execute(
        "SELECT COUNT(*) FROM songs WHERE status='mixed'"
    ).fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM song_tags WHERE dim='instr' AND value='guitar'"
    ).fetchone()[0] == 2
    connection.close()
