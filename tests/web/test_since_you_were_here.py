from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from cr8.db import connect
from tests.web.conftest import WebFixture


PASSWORD = "correct horse battery staple"


def _time(hours_ago: float) -> str:
    return (
        datetime.now(UTC).replace(microsecond=0) - timedelta(hours=hours_ago)
    ).isoformat()


def _start_returning_session(
    owner: TestClient, web: WebFixture, *, last_seen: str
) -> None:
    connection = connect(web.db_path)
    try:
        previous = connection.execute(
            "SELECT id FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert previous is not None
        connection.execute(
            "UPDATE sessions SET created_at=?, last_seen=? WHERE id=?",
            (last_seen, last_seen, int(previous["id"])),
        )
    finally:
        connection.close()

    response = owner.post(
        "/login",
        data={"username": "hareesh", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_counts_from_previous_sessions_last_seen(
    owner: TestClient, web: WebFixture
) -> None:
    previous_last_seen = _time(8)
    before = _time(10)
    after = _time(2)
    _start_returning_session(owner, web, last_seen=previous_last_seen)

    connection = connect(web.db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 2
        connection.execute("UPDATE files SET first_seen=?", (before,))
        connection.execute(
            """
            UPDATE files SET first_seen=?
            WHERE bounce_id IN (
              SELECT id FROM bounces WHERE song_id IN (
                SELECT id FROM songs ORDER BY id LIMIT 2
              )
            )
            """,
            (after,),
        )
        connection.executemany(
            """
            INSERT INTO playback_events(
              share_id, bounce_ulid, actor, started_at
            ) VALUES(0, ?, ?, ?)
            """,
            [
                (web.bounce_ulids[0], "hareesh", after),
                (web.bounce_ulids[0], "henry", after),
                (web.bounce_ulids[1], "henry", after),
                (web.bounce_ulids[2], "rohiit", after),
                (web.bounce_ulids[3], "before", before),
            ],
        )
    finally:
        connection.close()

    response = owner.get("/api/since-you-were-here")
    assert response.status_code == 200
    assert response.json() == {"new_songs": 2, "people": 2}


def test_first_visit_is_quiet(owner: TestClient) -> None:
    response = owner.get("/api/since-you-were-here")
    assert response.status_code == 200
    assert response.json() == {"quiet": True}


def test_a_recent_previous_session_is_quiet(
    owner: TestClient, web: WebFixture
) -> None:
    _start_returning_session(owner, web, last_seen=_time(1))

    response = owner.get("/api/since-you-were-here")
    assert response.status_code == 200
    assert response.json() == {"quiet": True}


def test_requester_is_excluded_from_people_count(
    owner: TestClient, web: WebFixture
) -> None:
    _start_returning_session(owner, web, last_seen=_time(8))
    after = _time(1)
    connection = connect(web.db_path)
    try:
        connection.executemany(
            """
            INSERT INTO playback_events(
              share_id, bounce_ulid, actor, started_at
            ) VALUES(0, ?, ?, ?)
            """,
            [
                (web.bounce_ulids[0], "hareesh", after),
                (web.bounce_ulids[1], "hareesh", after),
                (web.bounce_ulids[2], "henry", after),
                (web.bounce_ulids[3], "henry", after),
            ],
        )
    finally:
        connection.close()

    response = owner.get("/api/since-you-were-here")
    assert response.status_code == 200
    assert response.json() == {"new_songs": 0, "people": 1}
