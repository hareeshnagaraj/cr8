"""Digging: substring matching, tags, and the short-query cliff.

The old index covered title and slug only, tokenised on words, so "ridge" found
nothing in "bridges redo" and a collaborator's name was unsearchable. The
trigram index fixes both and introduces one sharp edge worth pinning: it cannot
match anything shorter than three characters, which every search passes through
on the way to being typed.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from tests.web.conftest import WebFixture


HEADERS = {"X-CR8-Request": "1"}


def _titles(client: TestClient, query: str = "", **params: object) -> list[str]:
    request = {"limit": 500, **params}
    if query:
        request["q"] = query
    response = client.get("/api/library", params=request)
    assert response.status_code == 200, response.text
    return [track["title"] for track in response.json()["tracks"]]


def test_substring_inside_a_word_matches(owner: TestClient) -> None:
    """The whole point of trigrams: "yline" is inside "Skylinedrive"."""
    assert "Skylinedrive" in _titles(owner, "yline")
    assert "Skylinedrive" in _titles(owner, "drive")


def test_prefix_and_whole_word_still_match(owner: TestClient) -> None:
    assert "Diamond" in _titles(owner, "Diamond")
    assert "Diamond" in _titles(owner, "Diam")


def test_search_is_case_insensitive(owner: TestClient) -> None:
    assert "Diamond" in _titles(owner, "diamond")
    assert "Diamond" in _titles(owner, "DIAMOND")


def test_tags_are_searchable(owner: TestClient, web: WebFixture) -> None:
    """A collaborator's name lives in song_tags, not the title."""
    connection = sqlite3.connect(web.db_path)
    song_id = connection.execute(
        "SELECT id FROM songs WHERE title='Diamond'"
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO song_tags(song_id, dim, value, source, created_at)
        VALUES(?, 'collab', 'henrytest', 'human', '2026-07-31')
        """,
        (song_id,),
    )
    connection.commit()
    connection.close()

    from cr8.web.common.database import migrate

    migrate(web.db_path)
    found = _titles(owner, "henrytest")
    assert found == ["Diamond"], found


def test_a_new_tag_is_findable_without_a_rebuild(
    owner: TestClient, web: WebFixture
) -> None:
    """The triggers are the point: tagging in the app updates the index."""
    song_ulid = owner.get("/api/library", params={"limit": 1}).json()["tracks"][0][
        "song_ulid"
    ]
    response = owner.post(
        f"/api/songs/{song_ulid}/tags/toggle",
        headers=HEADERS,
        json={"dim": "vibe", "value": "zzquirk"},
    )
    assert response.status_code == 200, response.text
    assert len(_titles(owner, "zzquirk")) == 1


def test_removing_a_tag_removes_it_from_search(
    owner: TestClient, web: WebFixture
) -> None:
    song_ulid = owner.get("/api/library", params={"limit": 1}).json()["tracks"][0][
        "song_ulid"
    ]
    body = {"dim": "vibe", "value": "zzgone"}
    owner.post(f"/api/songs/{song_ulid}/tags/toggle", headers=HEADERS, json=body)
    assert len(_titles(owner, "zzgone")) == 1
    owner.post(f"/api/songs/{song_ulid}/tags/toggle", headers=HEADERS, json=body)
    assert _titles(owner, "zzgone") == []


def test_two_character_queries_still_return_results(owner: TestClient) -> None:
    """Trigram matches nothing under three characters. Without the LIKE path
    the library would empty out on the way to typing a real search."""
    assert _titles(owner, "Di")
    assert _titles(owner, "am")


def test_one_character_queries_work(owner: TestClient) -> None:
    assert _titles(owner, "D")


def test_a_query_matching_nothing_returns_nothing(owner: TestClient) -> None:
    assert _titles(owner, "qqzzxx") == []
    assert _titles(owner, "zq") == []


def test_wildcards_in_short_queries_are_not_special(owner: TestClient) -> None:
    """A stray % must not turn into "match everything"."""
    assert _titles(owner, "%%") == []


def test_bpm_range_filters(owner: TestClient) -> None:
    everything = owner.get("/api/library", params={"limit": 500}).json()["tracks"]
    tempos = sorted(t["bpm"] for t in everything if t["bpm"])
    assert tempos, "fixture should have tempos"
    floor, ceiling = tempos[0], tempos[len(tempos) // 2]

    tracks = owner.get(
        "/api/library", params={"limit": 500, "bpm_min": floor, "bpm_max": ceiling}
    ).json()["tracks"]
    assert tracks
    for track in tracks:
        assert track["bpm"] is not None
        assert floor <= track["bpm"] <= ceiling


def test_bpm_minimum_alone_works(owner: TestClient) -> None:
    everything = owner.get("/api/library", params={"limit": 500}).json()["tracks"]
    tempos = sorted(t["bpm"] for t in everything if t["bpm"])
    cutoff = tempos[len(tempos) // 2]
    tracks = owner.get(
        "/api/library", params={"limit": 500, "bpm_min": cutoff}
    ).json()["tracks"]
    assert tracks
    assert all(track["bpm"] >= cutoff for track in tracks)


def test_bpm_filter_excludes_tracks_without_a_tempo(
    owner: TestClient, web: WebFixture
) -> None:
    connection = sqlite3.connect(web.db_path)
    connection.execute("UPDATE songs SET bpm=NULL WHERE title='Diamond'")
    connection.commit()
    connection.close()
    titles = _titles(owner, bpm_min=1, bpm_max=999)
    assert "Diamond" not in titles


def test_search_and_bpm_compose(owner: TestClient) -> None:
    titles = _titles(owner, "a", bpm_min=1, bpm_max=999)
    assert isinstance(titles, list)


def test_library_date_label_falls_back_to_song_rollup(
    owner: TestClient, web: WebFixture
) -> None:
    connection = sqlite3.connect(web.db_path)
    connection.execute(
        """
        UPDATE bounces SET bounce_date=NULL
        WHERE song_id=(SELECT id FROM songs WHERE title='Diamond')
        """
    )
    connection.execute(
        "UPDATE songs SET last_date='2025-06-07' WHERE title='Diamond'"
    )
    connection.commit()
    connection.close()

    response = owner.get("/api/library", params={"limit": 500, "q": "Diamond"})
    assert response.status_code == 200
    track = response.json()["tracks"][0]
    assert track["title"] == "Diamond"
    assert track["date_label"] == "Jun 7, 2025"


def test_long_queries_are_still_refused(owner: TestClient) -> None:
    response = owner.get("/api/library", params={"limit": 10, "q": "x" * 200})
    assert response.status_code == 400
