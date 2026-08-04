from __future__ import annotations

import re

from fastapi.testclient import TestClient

from cr8.db import connect
from cr8.web.owner.app import create_app
from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}


def test_second_user_can_log_in_and_tag_writes_use_their_username(
    web: WebFixture, owner: TestClient
):
    created = owner.post(
        "/members",
        headers=CSRF,
        data={"username": "henry", "display": "Henry"},
    )
    assert created.status_code == 201
    password_match = re.search(
        r'<strong class="mono">([^<]+)</strong>', created.text
    )
    assert password_match is not None
    password = password_match.group(1)

    with TestClient(create_app(web.owner_settings)) as henry:
        logged_in = henry.post(
            "/login",
            data={"username": "henry", "password": password},
            follow_redirects=False,
        )
        assert logged_in.status_code == 303
        assert logged_in.headers["location"] == "/"

        tagged = henry.post(
            f"/songs/{web.song_ulids[0]}/tags/toggle",
            headers=CSRF,
            data={"dim": "vibe", "value": "henry-tag"},
        )
        assert tagged.status_code == 200
        assert "henry" in tagged.text

    connection = connect(web.db_path)
    try:
        tag = connection.execute(
            """
            SELECT author FROM song_tags
            WHERE song_id=(SELECT id FROM songs WHERE public_id=?)
              AND dim='vibe' AND value='henry-tag'
            """,
            (web.song_ulids[0],),
        ).fetchone()
        audit = connection.execute(
            """
            SELECT actor FROM reactions
            WHERE kind='chip' AND value='henry-tag'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()

    assert tag["author"] == "henry"
    assert audit["actor"] == "henry:audit:add"


def test_member_can_be_listed_and_removed(
    web: WebFixture, owner: TestClient
):
    created = owner.post(
        "/members",
        headers=CSRF,
        data={"username": "charlie", "display": "Charlie"},
    )
    assert created.status_code == 201
    password_match = re.search(
        r'<strong class="mono">([^<]+)</strong>', created.text
    )
    assert password_match is not None
    assert "@charlie" in owner.get("/members").text

    connection = connect(web.db_path)
    try:
        member_id = int(
            connection.execute(
                "SELECT id FROM users WHERE username='charlie'"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    removed = owner.post(
        f"/members/{member_id}/remove",
        headers=CSRF,
        follow_redirects=False,
    )
    assert removed.status_code == 303
    assert "@charlie" not in owner.get("/members").text

    with TestClient(create_app(web.owner_settings)) as charlie:
        rejected = charlie.post(
            "/login",
            data={
                "username": "charlie",
                "password": password_match.group(1),
            },
        )
    assert rejected.status_code == 401
