from __future__ import annotations

from fastapi.testclient import TestClient

from cr8.web.common import presence, reactions
from cr8.web.owner import routes_assignments
from tests.web.conftest import WebFixture


HEADERS = {"X-CR8-Request": "1"}


def test_progress_post_adds_listener_with_resolved_track(
    owner: TestClient, web: WebFixture, monkeypatch
) -> None:
    now = [100.0]
    monkeypatch.setattr(presence.time, "time", lambda: now[0])

    response = owner.post(
        f"/progress/{web.bounce_ulids[0]}",
        headers=HEADERS,
        data={"state": "heard", "heard_s": "15"},
    )
    assert response.status_code == 204

    now[0] = 112.0
    response = owner.get("/api/presence")
    assert response.status_code == 200
    assert response.json() == {
        "listeners": [
            {
                "actor": "hareesh",
                "bounce_ulid": web.bounce_ulids[0],
                "song_ulid": web.song_ulids[0],
                "title": "Stayhere",
                "key_canon": "C minor",
                "bpm": 119.0,
                "era": "working",
                "era_css": "working",
                "seen_s_ago": 12,
            }
        ]
    }


def test_stale_listener_is_absent(
    owner: TestClient, web: WebFixture, monkeypatch
) -> None:
    now = [200.0]
    monkeypatch.setattr(presence.time, "time", lambda: now[0])
    response = owner.post(
        f"/progress/{web.bounce_ulids[1]}",
        headers=HEADERS,
        data={"state": "heard", "heard_s": "15"},
    )
    assert response.status_code == 204

    now[0] = 261.0
    assert owner.get("/api/presence").json() == {"listeners": []}


def test_presence_requires_session(web: WebFixture) -> None:
    response = web.owner.get("/api/presence")
    assert response.status_code == 401


def test_late_flush_for_the_left_track_cannot_move_presence(
    owner: TestClient, web: WebFixture, monkeypatch
) -> None:
    # A skip fires the new track's start and the old track's listened-seconds
    # flush back-to-back; the flush can land second. Presence must stay on
    # the track that STARTED, or the rail names the song the person just left.
    now = [300.0]
    monkeypatch.setattr(presence.time, "time", lambda: now[0])

    response = owner.post(
        f"/progress/{web.bounce_ulids[1]}",
        headers=HEADERS,
        data={"state": "heard", "heard_s": "0", "started": "true"},
    )
    assert response.status_code == 204

    now[0] = 300.4
    response = owner.post(
        f"/progress/{web.bounce_ulids[0]}",
        headers=HEADERS,
        data={"state": "heard", "heard_s": "45"},
    )
    assert response.status_code == 204

    now[0] = 302.0
    listeners = owner.get("/api/presence").json()["listeners"]
    assert [entry["bounce_ulid"] for entry in listeners] == [web.bounce_ulids[1]]


def test_progress_presence_uses_the_existing_transaction(
    owner: TestClient, web: WebFixture, monkeypatch
) -> None:
    mutation_count = 0
    real_mutate = reactions.mutate

    def counting_mutate(*args, **kwargs):
        nonlocal mutation_count
        mutation_count += 1
        return real_mutate(*args, **kwargs)

    monkeypatch.setattr(reactions, "mutate", counting_mutate)
    monkeypatch.setattr(routes_assignments, "mutate", counting_mutate)

    response = owner.post(
        f"/progress/{web.bounce_ulids[0]}",
        headers=HEADERS,
        data={
            "state": "heard",
            "heard_s": "15",
            "started": "true",
        },
    )

    assert response.status_code == 204
    assert mutation_count == 2
