"""Invites: the only way a new person gets an account.

The interesting cases are all failure cases. An invite that outlives its
usefulness, one that two people click at the same moment, one whose chosen
username is taken — each has to leave the invite in a state you would defend
out loud.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from cr8.web.common.auth import create_member
from tests.web.conftest import WebFixture


HEADERS = {"X-CR8-Request": "1"}
GOOD_PASSWORD = "correct horse battery staple"


def _create_invite(client: TestClient, **payload: object) -> dict:
    body = {"label": "for henry", "role": "band", "max_uses": 1}
    body.update(payload)
    response = client.post("/api/admin/invites", headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _token_from(join_url: str) -> str:
    return join_url.rsplit("/join/", 1)[1]


def test_invite_returns_a_link_once_and_stores_only_a_digest(
    owner: TestClient, web: WebFixture
) -> None:
    created = _create_invite(owner)
    token = _token_from(created["join_url"])

    connection = sqlite3.connect(web.db_path)
    stored = connection.execute("SELECT token_sha256 FROM invites").fetchall()
    connection.close()
    assert len(stored) == 1
    assert token not in stored[0][0]

    # Listing never replays the secret.
    listed = owner.get("/api/admin/invites").json()["invites"]
    assert listed[0]["state"] == "active"
    assert "token" not in str(listed[0])


def test_claiming_an_invite_creates_a_band_member_and_signs_them_in(
    owner: TestClient, web: WebFixture
) -> None:
    created = _create_invite(owner)
    token = _token_from(created["join_url"])
    owner.post("/logout", headers=HEADERS, follow_redirects=False)

    response = owner.post(
        "/api/join",
        headers=HEADERS,
        json={
            "token": token,
            "username": "henry",
            "display": "Henry",
            "password": GOOD_PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"username": "henry", "role": "band"}

    # Signed in already — no second login step.
    me = owner.get("/api/me").json()
    assert me["username"] == "henry"
    assert me["is_admin"] is False
    # And not an admin, even though an admin created the invite.
    assert owner.get("/members", follow_redirects=False).status_code == 303


def test_song_invite_stores_the_bounce_and_lands_on_the_song(
    owner: TestClient, web: WebFixture
) -> None:
    bounce_ulid = web.bounce_ulids[0]
    song_ulid = web.song_ulids[0]
    created = _create_invite(owner, bounce_ulid=bounce_ulid)
    token = _token_from(created["join_url"])
    assert created["invite"]["bounce_ulid"] == bounce_ulid
    assert created["invite"]["song_title"] == "Stayhere"

    connection = sqlite3.connect(web.db_path)
    stored = connection.execute(
        "SELECT bounce_ulid FROM invites WHERE ulid=?",
        (created["invite"]["ulid"],),
    ).fetchone()
    connection.close()
    assert stored == (bounce_ulid,)

    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    response = owner.post(
        "/api/join",
        headers=HEADERS,
        json={
            "token": token,
            "username": "henry",
            "display": "Henry",
            "password": GOOD_PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json() == {
        "username": "henry",
        "role": "band",
        "redirect": f"/songs/{song_ulid}?welcome=1",
    }
    assert owner.get("/api/me").json()["username"] == "henry"


def test_unknown_song_is_refused_before_an_invite_is_minted(
    owner: TestClient,
) -> None:
    response = owner.post(
        "/api/admin/invites",
        headers=HEADERS,
        json={"bounce_ulid": "not-a-bounce"},
    )
    assert response.status_code == 400
    assert owner.get("/api/admin/invites").json()["invites"] == []


def test_song_invite_frontend_picker_redirect_and_one_time_playback_contract() -> None:
    admin = Path("web/app/admin/page.tsx").read_text(encoding="utf-8")
    join = Path("web/app/join/[token]/page.tsx").read_text(encoding="utf-8")
    song = Path("web/app/songs/[ulid]/page.tsx").read_text(encoding="utf-8")

    assert 'bounce_ulid: bounceUlid || null' in admin
    assert "searchable" in admin
    assert 'response.ok ? response.json() : Promise.reject(response.status)' in admin
    assert 'window.location.href = destination' in join
    assert 'data.redirect.startsWith("/")' in join
    assert 'location.searchParams.get("welcome") !== "1"' in song
    assert "welcomeAttempted.current = true" in song
    assert "player.play(queue, selected)" in song
    assert 'location.searchParams.delete("welcome")' in song


def test_an_owner_invite_makes_an_admin(owner: TestClient) -> None:
    created = _create_invite(owner, role="owner")
    token = _token_from(created["join_url"])
    owner.post("/logout", headers=HEADERS, follow_redirects=False)

    owner.post(
        "/api/join",
        headers=HEADERS,
        json={"token": token, "username": "henry", "password": GOOD_PASSWORD},
    )
    assert owner.get("/api/me").json()["is_admin"] is True


def test_an_invite_is_spent_after_its_last_use(owner: TestClient) -> None:
    created = _create_invite(owner, max_uses=1)
    token = _token_from(created["join_url"])

    first = owner.post(
        "/api/join",
        headers=HEADERS,
        json={"token": token, "username": "henry", "password": GOOD_PASSWORD},
    )
    assert first.status_code == 201

    second = owner.post(
        "/api/join",
        headers=HEADERS,
        json={"token": token, "username": "connor", "password": GOOD_PASSWORD},
    )
    assert second.status_code == 404
    assert "exhausted" in second.json()["detail"]


def test_revoked_invites_stop_working_immediately(owner: TestClient) -> None:
    created = _create_invite(owner)
    token = _token_from(created["join_url"])
    ulid = created["invite"]["ulid"]

    revoked = owner.post(f"/api/admin/invites/{ulid}/revoke", headers=HEADERS)
    assert revoked.status_code == 200
    assert revoked.json()["invite"]["state"] == "revoked"

    assert owner.get(f"/api/join/{token}").status_code == 404
    response = owner.post(
        "/api/join",
        headers=HEADERS,
        json={"token": token, "username": "henry", "password": GOOD_PASSWORD},
    )
    assert response.status_code == 404
    assert "revoked" in response.json()["detail"]


def test_expired_invites_are_refused(owner: TestClient, web: WebFixture) -> None:
    created = _create_invite(owner)
    token = _token_from(created["join_url"])
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    connection = sqlite3.connect(web.db_path)
    connection.execute("UPDATE invites SET expires_at=?", (past,))
    connection.commit()
    connection.close()

    assert owner.get(f"/api/join/{token}").status_code == 404
    response = owner.post(
        "/api/join",
        headers=HEADERS,
        json={"token": token, "username": "henry", "password": GOOD_PASSWORD},
    )
    assert response.status_code == 404
    assert "expired" in response.json()["detail"]


def test_a_taken_username_does_not_consume_the_invite(
    owner: TestClient, web: WebFixture
) -> None:
    create_member(web.owner_settings, username="henry", display="Henry")
    created = _create_invite(owner, max_uses=1)
    token = _token_from(created["join_url"])

    clash = owner.post(
        "/api/join",
        headers=HEADERS,
        json={"token": token, "username": "henry", "password": GOOD_PASSWORD},
    )
    assert clash.status_code == 400

    # The invite survives the mistake and still works for a free name.
    assert owner.get(f"/api/join/{token}").status_code == 200
    good = owner.post(
        "/api/join",
        headers=HEADERS,
        json={"token": token, "username": "connor", "password": GOOD_PASSWORD},
    )
    assert good.status_code == 201


def test_short_passwords_are_refused_before_the_invite_is_touched(
    owner: TestClient
) -> None:
    created = _create_invite(owner, max_uses=1)
    token = _token_from(created["join_url"])

    weak = owner.post(
        "/api/join",
        headers=HEADERS,
        json={"token": token, "username": "henry", "password": "short"},
    )
    assert weak.status_code == 400
    assert owner.get("/api/admin/invites").json()["invites"][0]["use_count"] == 0


def test_unknown_tokens_look_the_same_as_used_ones(owner: TestClient) -> None:
    assert owner.get("/api/join/not-a-real-token").status_code == 404
    response = owner.post(
        "/api/join",
        headers=HEADERS,
        json={
            "token": "not-a-real-token",
            "username": "henry",
            "password": GOOD_PASSWORD,
        },
    )
    assert response.status_code == 404


def test_two_people_racing_the_last_use_do_not_both_get_in(
    owner: TestClient, web: WebFixture
) -> None:
    created = _create_invite(owner, max_uses=1)
    token = _token_from(created["join_url"])

    def claim(name: str):
        with TestClient(web.owner.app) as client:
            return client.post(
                "/api/join",
                headers=HEADERS,
                json={
                    "token": token,
                    "username": name,
                    "password": GOOD_PASSWORD,
                },
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["henry", "connor"]))

    assert sorted(results) == [201, 404] or sorted(results) == [201, 409]
    connection = sqlite3.connect(web.db_path)
    count = connection.execute(
        "SELECT COUNT(*) FROM users WHERE username IN ('henry','connor')"
    ).fetchone()[0]
    connection.close()
    assert count == 1


def test_join_requires_the_csrf_header(owner: TestClient) -> None:
    created = _create_invite(owner)
    token = _token_from(created["join_url"])
    response = owner.post(
        "/api/join",
        json={"token": token, "username": "henry", "password": GOOD_PASSWORD},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/admin/invites"), ("post", "/api/admin/invites")],
)
def test_invite_management_is_admin_only(
    owner: TestClient, web: WebFixture, method: str, path: str
) -> None:
    credentials = create_member(
        web.owner_settings, username="henry", display="Henry"
    )
    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    owner.post(
        "/login",
        data={"username": "henry", "password": credentials.password},
        follow_redirects=False,
    )
    response = (
        owner.get(path)
        if method == "get"
        else owner.post(path, headers=HEADERS, json={})
    )
    assert response.status_code == 403


def test_invite_management_requires_a_session(web: WebFixture) -> None:
    assert web.owner.get("/api/admin/invites").status_code == 401


def test_bad_role_is_refused(owner: TestClient) -> None:
    response = owner.post(
        "/api/admin/invites", headers=HEADERS, json={"role": "wizard"}
    )
    assert response.status_code == 400
