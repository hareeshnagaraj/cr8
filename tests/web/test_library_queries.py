from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest

from cr8.db import connect
from cr8.web.common.queries import LibraryFilter, library_songs
from cr8.web.owner import helpers
from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}


def test_ears_sort_applies_before_limit(web: WebFixture) -> None:
    oldest_bounce = web.bounce_ulids[-1]
    connection = connect(web.db_path)
    try:
        connection.executemany(
            """
            INSERT INTO playback_events(
              share_id, bounce_ulid, actor, started_at
            ) VALUES(0, ?, ?, '2026-08-01T00:00:00+00:00')
            """,
            [(oldest_bounce, f"listener-{index}") for index in range(5)],
        )
    finally:
        connection.close()

    songs = library_songs(
        web.owner_settings,
        LibraryFilter(),
        sort="ears-desc",
        limit=2,
    )

    assert len(songs) == 2
    assert songs[0]["bounce_ulid"] == oldest_bounce
    assert songs[0]["ears"] == 5


def test_hearted_filter_applies_before_limit(web: WebFixture) -> None:
    oldest_bounce = web.bounce_ulids[-1]
    connection = connect(web.db_path)
    try:
        connection.execute(
            """
            INSERT INTO reactions(
              bounce_ulid, actor, kind, created_at
            ) VALUES(?, 'hareesh', 'heart', '2026-08-01T00:00:00+00:00')
            """,
            (oldest_bounce,),
        )
    finally:
        connection.close()

    songs = library_songs(
        web.owner_settings,
        LibraryFilter(hearted=True),
        actor="hareesh",
        limit=1,
    )

    assert [song["bounce_ulid"] for song in songs] == [oldest_bounce]


def test_write_result_fetches_only_requested_songs_once(
    web: WebFixture,
    owner: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def recording_library_songs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(helpers, "library_songs", recording_library_songs)
    requested = web.song_ulids[:2]

    response = owner.post(
        "/selection",
        headers=CSRF,
        data={"song_ulid": requested, "status": "finished"},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    positional, _keywords = calls[0]
    assert tuple(getattr(positional[1], "song_ulids", ())) == tuple(requested)
