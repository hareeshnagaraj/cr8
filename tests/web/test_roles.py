"""Roles are enforced, not just stored.

`users.role` existed in the schema from the start but nothing read it, and
every member was created as an owner. Anyone invited in could remove accounts
and rewrite the shared tag vocabulary. These tests pin the boundary: signing in
gets you the catalog, being an admin gets you everyone else's access.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
import pytest

from cr8.web.common.auth import AuthError, create_member
from tests.web.conftest import WebFixture


HEADERS = {"X-CR8-Request": "1"}


def _role_of(web: WebFixture, username: str) -> str:
    connection = sqlite3.connect(web.owner_settings.db_path)
    try:
        return str(
            connection.execute(
                "SELECT role FROM users WHERE username=?", (username,)
            ).fetchone()[0]
        )
    finally:
        connection.close()


def _add_band_member(web: WebFixture, username: str = "henry") -> str:
    """Create a member the way the app does, then sign in as them."""
    credentials = create_member(
        web.owner_settings, username=username, display=username.title()
    )
    return credentials.password


def _sign_in(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code in {200, 303}


def test_new_members_are_band_not_owner(owner: TestClient, web: WebFixture) -> None:
    credentials = create_member(
        web.owner_settings, username="henry", display="Henry"
    )
    assert _role_of(web, credentials.username) == "band"


def test_explicit_owner_role_still_available(
    owner: TestClient, web: WebFixture
) -> None:
    credentials = create_member(
        web.owner_settings, username="henry", display="Henry", role="owner"
    )
    assert _role_of(web, credentials.username) == "owner"


def test_unknown_role_rejected(owner: TestClient, web: WebFixture) -> None:
    with pytest.raises(AuthError):
        create_member(
            web.owner_settings, username="henry", display="Henry", role="wizard"
        )


def test_first_account_is_an_owner(owner: TestClient) -> None:
    body = owner.get("/api/me").json()
    assert body["role"] == "owner"
    assert body["is_admin"] is True


def test_band_member_can_use_the_catalog(owner: TestClient, web: WebFixture) -> None:
    password = _add_band_member(web)
    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    _sign_in(owner, "henry", password)

    assert owner.get("/api/library").status_code == 200
    assert owner.get("/api/facets").status_code == 200
    me = owner.get("/api/me").json()
    assert me == {
        "username": "henry",
        "display": "Henry",
        "role": "band",
        "is_admin": False,
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/members"),
        ("post", "/members"),
        ("post", "/members/1/remove"),
        ("get", "/tags"),
        ("post", "/tags/rewrite"),
    ],
)
def test_admin_surfaces_refuse_band_members(
    owner: TestClient, web: WebFixture, method: str, path: str
) -> None:
    password = _add_band_member(web)
    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    _sign_in(owner, "henry", password)

    if method == "get":
        response = owner.get(path, follow_redirects=False)
        # HTML pages send them home rather than showing a wall.
        assert response.status_code == 303
        assert response.headers["location"] == "/"
    else:
        response = owner.post(
            path, headers=HEADERS, data={}, follow_redirects=False
        )
        assert response.status_code == 403


@pytest.mark.parametrize("path", ["/members", "/tags"])
def test_admin_pages_open_for_admins(owner: TestClient, path: str) -> None:
    response = owner.get(path, follow_redirects=False)
    assert response.status_code == 200


def test_signed_out_still_redirects_not_403(web: WebFixture) -> None:
    """Signed out is a different answer from signed in without permission."""
    response = web.owner.get("/members", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
