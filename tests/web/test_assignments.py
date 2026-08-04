"""Homework, and the rules that make it trustworthy.

The load-bearing behaviour is that listening marks a track *listened* and never
*done*. If a scrub could clear the list, nobody would believe the list.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
import pytest

from cr8.web.common.auth import create_member
from cr8.web.owner.routes_assignments import listened_enough
from tests.web.conftest import SONGS, WebFixture


HEADERS = {"X-CR8-Request": "1"}
FIRST = SONGS[0][1]
SECOND = SONGS[1][1]


def _member(web: WebFixture, username: str) -> str:
    return create_member(
        web.owner_settings, username=username, display=username.title()
    ).password


def _become(client: TestClient, username: str, password: str) -> None:
    client.post("/logout", headers=HEADERS, follow_redirects=False)
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code in {200, 303}


def _assign(client: TestClient, to: str, *ulids: str, note: str = "") -> dict:
    response = client.post(
        "/api/assignments",
        headers=HEADERS,
        json={"bounce_ulids": list(ulids), "to": to, "note": note},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _progress(client: TestClient, bounce_ulid: str, heard_s: float) -> None:
    response = client.post(
        f"/progress/{bounce_ulid}",
        headers=HEADERS,
        data={"state": "heard", "heard_s": str(heard_s)},
    )
    assert response.status_code == 204


def test_assigning_puts_it_on_their_plate(owner: TestClient, web: WebFixture) -> None:
    password = _member(web, "henry")
    result = _assign(owner, "henry", FIRST, note="the bridge at 1:20")
    assert result["created"] == 1
    assert result["skipped"] == 0
    assert len(result["assignments"]) == 1
    assert result["assignments"][0]["bounce_ulid"] == FIRST
    assert result["assignments"][0]["era"]
    assert result["assignments"][0]["date_label"] == "Jul 29, 2026"

    _become(owner, "henry", password)
    inbox = owner.get("/api/assignments").json()["assignments"]
    assert len(inbox) == 1
    assert inbox[0]["assigned_by"] == "hareesh"
    assert inbox[0]["note"] == "the bridge at 1:20"
    assert inbox[0]["state"] == "pending"
    assert inbox[0]["title"]
    assert inbox[0]["era"] == "working"
    assert inbox[0]["date_label"] == "Jul 29, 2026"
    assert owner.get("/api/assignments/count").json()["pending"] == 1


def test_my_inbox_is_only_mine(owner: TestClient, web: WebFixture) -> None:
    henry = _member(web, "henry")
    _member(web, "connor")
    _assign(owner, "connor", FIRST)

    _become(owner, "henry", henry)
    assert owner.get("/api/assignments").json()["assignments"] == []
    assert owner.get("/api/assignments/count").json()["pending"] == 0


def test_sender_can_see_what_they_sent(owner: TestClient, web: WebFixture) -> None:
    _member(web, "henry")
    _assign(owner, "henry", FIRST)
    sent = owner.get("/api/assignments/sent").json()["assignments"]
    assert len(sent) == 1
    assert sent[0]["assigned_to"] == "henry"


def test_unknown_recipient_is_refused(owner: TestClient) -> None:
    response = owner.post(
        "/api/assignments",
        headers=HEADERS,
        json={"bounce_ulids": [FIRST], "to": "nobody"},
    )
    assert response.status_code == 400


def test_no_tracks_is_refused(owner: TestClient, web: WebFixture) -> None:
    _member(web, "henry")
    response = owner.post(
        "/api/assignments", headers=HEADERS, json={"bounce_ulids": [], "to": "henry"}
    )
    assert response.status_code == 400


def test_sending_the_same_track_twice_does_not_stack(
    owner: TestClient, web: WebFixture
) -> None:
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    again = _assign(owner, "henry", FIRST)
    assert again["created"] == 0 and again["skipped"] == 1

    _become(owner, "henry", password)
    assert len(owner.get("/api/assignments").json()["assignments"]) == 1


def test_unknown_track_is_skipped_not_fatal(
    owner: TestClient, web: WebFixture
) -> None:
    _member(web, "henry")
    result = _assign(owner, "henry", FIRST, "01ARZ3NDEKTSV4RRFFQ69G5XXX")
    assert result["created"] == 1 and result["skipped"] == 1


def test_you_can_assign_to_yourself(owner: TestClient) -> None:
    result = _assign(owner, "hareesh", FIRST)
    assert result["created"] == 1
    assert owner.get("/api/assignments/count").json()["pending"] == 1


def test_a_short_listen_does_not_touch_the_state(
    owner: TestClient, web: WebFixture
) -> None:
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    connection = sqlite3.connect(web.db_path)
    connection.execute(
        """
        UPDATE files SET duration_s=240
        WHERE bounce_id=(SELECT id FROM bounces WHERE public_id=?)
        """,
        (FIRST,),
    )
    connection.commit()
    connection.close()
    _become(owner, "henry", password)

    _progress(owner, FIRST, 59.0)
    inbox = owner.get("/api/assignments").json()["assignments"]
    assert inbox[0]["state"] == "pending"


def test_a_real_listen_marks_it_heard_but_leaves_it_on_the_list(
    owner: TestClient, web: WebFixture
) -> None:
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    _become(owner, "henry", password)

    _progress(owner, FIRST, 75.0)
    inbox = owner.get("/api/assignments").json()["assignments"]
    assert len(inbox) == 1, "listening must not clear the card"
    assert inbox[0]["state"] == "heard"
    assert inbox[0]["heard_at"]
    # Still counted: it is not finished until they say so.
    assert owner.get("/api/assignments/count").json()["pending"] == 1


def test_short_track_needs_half_its_duration(
    owner: TestClient, web: WebFixture
) -> None:
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    connection = sqlite3.connect(web.db_path)
    connection.execute(
        """
        UPDATE files SET duration_s=100
        WHERE bounce_id=(SELECT id FROM bounces WHERE public_id=?)
        """,
        (FIRST,),
    )
    connection.commit()
    connection.close()
    _become(owner, "henry", password)

    _progress(owner, FIRST, 40.0)
    assert (
        owner.get("/api/assignments").json()["assignments"][0]["state"]
        == "pending"
    )
    _progress(owner, FIRST, 50.0)
    assert (
        owner.get("/api/assignments").json()["assignments"][0]["state"]
        == "heard"
    )


def test_only_an_explicit_tap_finishes_it(
    owner: TestClient, web: WebFixture
) -> None:
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    _become(owner, "henry", password)
    _progress(owner, FIRST, 90.0)

    ulid = owner.get("/api/assignments").json()["assignments"][0]["ulid"]
    done = owner.post(f"/api/assignments/{ulid}/done", headers=HEADERS)
    assert done.status_code == 200

    assert owner.get("/api/assignments").json()["assignments"] == []
    assert owner.get("/api/assignments/count").json()["pending"] == 0
    history = owner.get("/api/assignments?state=done").json()["assignments"]
    assert len(history) == 1 and history[0]["done_at"]
    done_at = history[0]["done_at"]
    assert owner.post(
        f"/api/assignments/{ulid}/done", headers=HEADERS
    ).status_code == 200
    repeated = owner.get("/api/assignments?state=done").json()["assignments"][0]
    assert repeated["done_at"] == done_at


def test_done_does_not_claim_the_track_was_heard(
    owner: TestClient, web: WebFixture
) -> None:
    """This used to assert that Done was refused until you had listened, which
    made the button do nothing on most of a plate. Closing something you never
    played is allowed; what must stay true is that doing so does not pretend
    you listened, because heard_at is what the sender is shown."""
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    _become(owner, "henry", password)
    ulid = owner.get("/api/assignments").json()["assignments"][0]["ulid"]

    response = owner.post(f"/api/assignments/{ulid}/done", headers=HEADERS)
    assert response.status_code == 200
    closed = owner.get("/api/assignments?state=done").json()["assignments"][0]
    assert closed["done_at"]
    assert closed["heard_at"] is None


def test_dismiss_clears_without_pretending_it_was_heard(
    owner: TestClient, web: WebFixture
) -> None:
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    _become(owner, "henry", password)

    ulid = owner.get("/api/assignments").json()["assignments"][0]["ulid"]
    assert owner.post(f"/api/assignments/{ulid}/dismiss", headers=HEADERS).status_code == 200
    # Idempotent: tapping twice is not an error.
    assert owner.post(f"/api/assignments/{ulid}/dismiss", headers=HEADERS).status_code == 200
    assert owner.get("/api/assignments").json()["assignments"] == []


def test_you_cannot_close_someone_elses_homework(
    owner: TestClient, web: WebFixture
) -> None:
    henry = _member(web, "henry")
    _member(web, "connor")
    _assign(owner, "connor", FIRST)
    ulid = owner.get("/api/assignments/sent").json()["assignments"][0]["ulid"]

    _become(owner, "henry", henry)
    assert owner.post(f"/api/assignments/{ulid}/done", headers=HEADERS).status_code == 404
    assert (
        owner.post(f"/api/assignments/{ulid}/dismiss", headers=HEADERS).status_code
        == 404
    )


def test_removing_a_member_clears_their_plate_but_keeps_what_they_sent(
    owner: TestClient, web: WebFixture
) -> None:
    henry = _member(web, "henry")
    _assign(owner, "henry", FIRST)

    _become(owner, "henry", henry)
    _assign(owner, "hareesh", SECOND)

    connection = sqlite3.connect(web.db_path)
    user_id = connection.execute(
        "SELECT id FROM users WHERE username='henry'"
    ).fetchone()[0]
    connection.close()

    _become(owner, "hareesh", "correct horse battery staple")
    removed = owner.post(
        f"/members/{user_id}/remove", headers=HEADERS, follow_redirects=False
    )
    assert removed.status_code == 303

    connection = sqlite3.connect(web.db_path)
    to_henry = connection.execute(
        "SELECT COUNT(*) FROM listen_assignments WHERE assigned_to='henry'"
    ).fetchone()[0]
    from_henry = connection.execute(
        "SELECT COUNT(*) FROM listen_assignments WHERE assigned_by='henry'"
    ).fetchone()[0]
    connection.close()
    assert to_henry == 0
    assert from_henry == 1


@pytest.mark.parametrize(
    ("heard", "duration", "expected"),
    [
        (59.0, 240.0, False),
        (60.0, 240.0, True),
        (600.0, None, True),
        (30.0, 100.0, False),  # short track, under half
        (50.0, 100.0, True),  # short track, past half
        (40.0, 100.0, False),
        (61.0, 100.0, True),
        (0.0, 10.0, False),
    ],
)
def test_what_counts_as_listening(
    heard: float, duration: float | None, expected: bool
) -> None:
    assert listened_enough(heard, duration) is expected


@pytest.mark.parametrize(
    "path",
    ["/api/assignments", "/api/assignments/count", "/api/assignments/sent"],
)
def test_assignments_need_a_session(web: WebFixture, path: str) -> None:
    assert web.owner.get(path).status_code == 401


def test_assigning_needs_a_session(web: WebFixture) -> None:
    response = web.owner.post(
        "/api/assignments",
        headers=HEADERS,
        json={"bounce_ulids": [FIRST], "to": "henry"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("action", ["done", "dismiss"])
def test_assignment_actions_need_a_session(
    web: WebFixture, action: str
) -> None:
    response = web.owner.post(
        f"/api/assignments/01ARZ3NDEKTSV4RRFFQ69G5FAV/{action}",
        headers=HEADERS,
    )
    assert response.status_code == 401


def test_done_works_on_something_you_have_not_played(
    owner: TestClient, web: WebFixture
) -> None:
    """The guard used to require heard first, so Done silently did nothing on
    most of what was on a plate. You are allowed to already know a track."""
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    _become(owner, "henry", password)

    item = owner.get("/api/assignments").json()["assignments"][0]
    assert item["state"] == "pending"
    response = owner.post(f"/api/assignments/{item['ulid']}/done", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert owner.get("/api/assignments").json()["assignments"] == []
    # It is done, but it never claimed to have been listened to.
    history = owner.get("/api/assignments?state=done").json()["assignments"]
    assert history[0]["done_at"] and not history[0]["heard_at"]


def test_closing_something_twice_is_not_an_error(
    owner: TestClient, web: WebFixture
) -> None:
    password = _member(web, "henry")
    _assign(owner, "henry", FIRST)
    _become(owner, "henry", password)
    ulid = owner.get("/api/assignments").json()["assignments"][0]["ulid"]
    assert owner.post(f"/api/assignments/{ulid}/done", headers=HEADERS).status_code == 200
    assert owner.post(f"/api/assignments/{ulid}/done", headers=HEADERS).status_code == 200
