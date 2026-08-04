from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cr8.db import connect
from cr8.web.common.auth import create_member

from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}
ROOT = Path(__file__).parents[2]


def _set_keepers(web: WebFixture) -> None:
    connection = connect(web.db_path)
    try:
        for title, keeper in (
            ("Stayhere", 5),
            ("Diamond", 4),
            ("Skylinedrive", 2),
            ("Pensive Arpey", 0),
        ):
            connection.execute(
                "UPDATE songs SET keeper=? WHERE title=?", (keeper, title)
            )
    finally:
        connection.close()


def _keeper_scores(owner: TestClient, sort: str) -> list[int]:
    response = owner.get(
        "/api/library", params={"limit": 1000, "sort": sort}
    )
    assert response.status_code == 200, response.text
    return [int(track["keeper"]) for track in response.json()["tracks"]]


def test_keeper_min_composes_with_actor_specific_visibility(
    web: WebFixture, owner: TestClient
) -> None:
    _set_keepers(web)
    tracks = owner.get(
        "/api/library", params={"limit": 1000, "keeper_min": 4}
    ).json()["tracks"]
    assert {track["title"] for track in tracks} == {"Stayhere", "Diamond"}

    stayhere = next(track for track in tracks if track["title"] == "Stayhere")
    connection = connect(web.db_path)
    try:
        connection.execute(
            """
            INSERT INTO listen_progress(
              share_id, bounce_ulid, actor, state, heard_s, updated_at
            ) VALUES(0, ?, 'hareesh', 'heard', 60, '2026-08-03T00:00:00+00:00')
            """,
            (stayhere["bounce_ulid"],),
        )
    finally:
        connection.close()

    owner_unheard = owner.get(
        "/api/library",
        params={"limit": 1000, "keeper_min": 4, "unheard": "true"},
    )
    assert owner_unheard.status_code == 200
    assert [track["title"] for track in owner_unheard.json()["tracks"]] == [
        "Diamond"
    ]

    credentials = create_member(
        web.owner_settings,
        username="henry",
        display="Henry",
        password="keeper filter password",
    )
    owner.post("/logout", headers=CSRF, follow_redirects=False)
    signed_in = owner.post(
        "/login",
        data={"username": credentials.username, "password": credentials.password},
        follow_redirects=False,
    )
    assert signed_in.status_code in {200, 303}
    band_unheard = owner.get(
        "/api/library",
        params={"limit": 1000, "keeper_min": 4, "unheard": "true"},
    )
    assert band_unheard.status_code == 200
    assert {track["title"] for track in band_unheard.json()["tracks"]} == {
        "Stayhere",
        "Diamond",
    }


def test_keeper_sort_is_ascending_and_descending(
    web: WebFixture, owner: TestClient
) -> None:
    _set_keepers(web)
    assert _keeper_scores(owner, "keeper") == [0, 2, 4, 5]
    assert _keeper_scores(owner, "keeper-desc") == [5, 4, 2, 0]


def test_era_key_and_seeded_random_library_params_still_filter_and_sort(
    web: WebFixture, owner: TestClient,
) -> None:
    connection = connect(web.db_path)
    try:
        connection.execute(
            """
            UPDATE bounces SET bounce_date=NULL
            WHERE song_id=(SELECT id FROM songs WHERE title='Pensive Arpey')
            """
        )
        connection.execute(
            """
            UPDATE songs
            SET first_date=NULL, last_date=NULL,
                era_id=(SELECT id FROM eras WHERE name='undated')
            WHERE title='Pensive Arpey'
            """
        )
    finally:
        connection.close()

    tracks = owner.get("/api/library", params={"limit": 1000}).json()["tracks"]
    sample = tracks[0]

    era_response = owner.get(
        "/api/library", params={"limit": 1000, "era": sample["era_css"]}
    )
    assert era_response.status_code == 200
    era_tracks = era_response.json()["tracks"]
    assert era_tracks
    assert {track["era_css"] for track in era_tracks} == {sample["era_css"]}

    for era_value in ("undated", "unknown"):
        undated_response = owner.get(
            "/api/library", params={"limit": 1000, "era": era_value}
        )
        assert undated_response.status_code == 200
        undated_tracks = undated_response.json()["tracks"]
        assert [track["title"] for track in undated_tracks] == ["Pensive Arpey"]

    key_response = owner.get(
        "/api/library", params={"limit": 1000, "key": sample["key_canon"]}
    )
    assert key_response.status_code == 200
    key_tracks = key_response.json()["tracks"]
    assert key_tracks
    assert {track["key_canon"] for track in key_tracks} == {sample["key_canon"]}

    def seeded(seed: str) -> list[str]:
        response = owner.get(
            "/api/library",
            params={"limit": 1000, "sort": "random", "random_seed": seed},
        )
        assert response.status_code == 200
        return [track["song_ulid"] for track in response.json()["tracks"]]

    assert seeded("keeper-seed-a") == seeded("keeper-seed-a")
    assert seeded("keeper-seed-a") != seeded("keeper-seed-b")


def test_library_rows_carry_distinct_visible_vibe_tags(
    web: WebFixture, owner: TestClient
) -> None:
    connection = connect(web.db_path)
    try:
        song = connection.execute(
            "SELECT id FROM songs WHERE title='Diamond'"
        ).fetchone()
        assert song is not None
        song_id = int(song["id"])
        connection.execute(
            """
            INSERT INTO song_tags(song_id, dim, value, source, created_at)
            VALUES(?, 'vibe', 'dreamy', 'human', '2026-08-03')
            """,
            (song_id,),
        )
        for actor, value, deleted_at in (
            ("henry", "nocturnal", None),
            ("hareesh", "dreamy", None),
            ("hareesh:audit:undo", "hidden", None),
            ("hareesh", "deleted", "2026-08-03"),
        ):
            connection.execute(
                """
                INSERT INTO reactions(
                  bounce_ulid, song_id, actor, kind, dim, value,
                  created_at, deleted_at
                ) VALUES('', ?, ?, 'chip', 'vibe', ?, '2026-08-03', ?)
                """,
                (song_id, actor, value, deleted_at),
            )
    finally:
        connection.close()

    response = owner.get(
        "/api/library", params={"limit": 1000, "q": "Diamond"}
    )
    assert response.status_code == 200
    track = response.json()["tracks"][0]
    assert track["title"] == "Diamond"
    assert track["vibe_tags"] == ["dreamy", "nocturnal"]


def test_next_library_exposes_the_full_audit_surface() -> None:
    page = (ROOT / "web/app/page.tsx").read_text(encoding="utf-8")
    row = (ROOT / "web/components/LibraryRow.tsx").read_text(encoding="utf-8")
    filters = (ROOT / "web/components/FilterRail.tsx").read_text(encoding="utf-8")
    track_type = (ROOT / "web/components/PlayerProvider.tsx").read_text(
        encoding="utf-8"
    )
    song = (ROOT / "web/app/songs/[ulid]/page.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web/app/globals.css").read_text(encoding="utf-8")

    assert 'key: "keeper", label: "Keeper"' in page
    assert 'key: "random", label: "Random"' in page
    assert 'key: "ears", label: "Ears", asc: "ears", desc: "ears-desc", column: {cls: ""}' in page
    assert 'aria-label="Select songs"' in page
    assert "onTogglePick(track.bounce_ulid)" in row
    assert "track.vibe_tags.slice(0, 3)" in row
    assert "Number(track.keeper) >= 1 ? `k${track.keeper}`" in row
    # FACETS table is the single source of query params + labels.
    assert 'param: "keeper_min"' in filters
    assert 'param: "era"' in filters
    assert 'param: "key"' in filters
    assert 'params.set(facet.param, value)' in filters
    assert 'label: "Keeper"' in filters
    assert 'label: "Era"' in filters
    assert 'label: "Key"' in filters
    assert "searchable" in filters
    assert "keeper?: number | null;" in track_type
    assert "track.status" in song
    assert "`keeper ${track.keeper}`" in song
    assert ".lib-body.is-select-mode .row .pick" in css
    assert ".lib-body.is-select-mode .row-art { display: none; }" in css
    assert ".row-vibes" in css
