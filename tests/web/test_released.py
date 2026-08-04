from __future__ import annotations

from pathlib import Path
import re

from fastapi.testclient import TestClient

from cr8.db import connect
from cr8.web.common.queries import LibraryFilter, library_songs

from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}
RELEASE_URL = "https://open.spotify.com/track/example"


def _release_first_song(web: WebFixture, owner: TestClient) -> set[str]:
    response = owner.post(
        "/selection",
        headers=CSRF,
        data={
            "song_ulid": web.song_ulids[0],
            "action": "released",
            "released_url": RELEASE_URL,
        },
    )
    assert response.status_code == 200
    connection = connect(web.db_path)
    try:
        song = connection.execute(
            """
            SELECT id, status, released_url, human_touched
            FROM songs
            WHERE public_id=?
            """,
            (web.song_ulids[0],),
        ).fetchone()
        bounce_ulids = {
            str(row["public_id"])
            for row in connection.execute(
                "SELECT public_id FROM bounces WHERE song_id=?",
                (int(song["id"]),),
            )
        }
    finally:
        connection.close()
    assert tuple(song)[1:] == ("released", RELEASE_URL, 1)
    return bounce_ulids


def _ids(response) -> set[str]:
    assert response.status_code == 200
    return {str(item["id"]) for item in response.json()["tracks"]}


def test_released_batch_action_defaults_and_opt_in(
    web: WebFixture, owner: TestClient
):
    released_bounces = _release_first_song(web, owner)

    default_owner = library_songs(web.owner_settings, LibraryFilter())
    released_owner = library_songs(
        web.owner_settings, LibraryFilter(status="released")
    )
    assert web.song_ulids[0] not in {
        str(item["song_ulid"]) for item in default_owner
    }
    assert [str(item["song_ulid"]) for item in released_owner] == [
        web.song_ulids[0]
    ]

    default_page = owner.get("/")
    assert "Stayhere" not in default_page.text
    assert "released-filter" in default_page.text
    assert default_page.text.rfind(">released<") > default_page.text.rfind(
        ">finished<"
    )
    assert (
        f'href="/songs/{web.song_ulids[0]}"'
        not in owner.get("/?q=Stayhere").text
    )
    assert "Stayhere" not in owner.get("/triage").text
    assert owner.get("/shares").status_code == 404
    assert released_bounces.isdisjoint(_ids(owner.get("/api/dig")))
    assert released_bounces.isdisjoint(_ids(owner.get("/api/library-queue")))

    released_page = owner.get("/?status=released")
    assert released_page.status_code == 200
    assert "Stayhere" in released_page.text
    assert re.search(
        r'class="rail-filter released-filter"[^>]+aria-pressed="true"',
        released_page.text,
    )
    assert any(
        f'data-shuffle-this-seed data-track-id="{bounce_ulid}"'
        in released_page.text
        for bounce_ulid in released_bounces
    )
    assert all(
        f'data-shuffle-all-seed data-track-id="{bounce_ulid}"'
        not in released_page.text
        for bounce_ulid in released_bounces
    )
    assert released_bounces & _ids(
        owner.get("/api/library-queue?status=released")
    )

def test_detail_templates_round_bpm_and_format_date_range(
    web: WebFixture, owner: TestClient
):
    connection = connect(web.db_path)
    connection.execute(
        """
        UPDATE songs
        SET bpm=119.1, first_date='2026-07-27', last_date='2026-07-29',
            status='released'
        WHERE public_id=?
        """,
        (web.song_ulids[0],),
    )
    connection.close()

    owner_page = owner.get(f"/songs/{web.song_ulids[0]}")
    assert owner_page.status_code == 200
    assert "119.1" not in owner_page.text
    assert re.search(r'<div class="spec-v">119</div>', owner_page.text)
    assert "2026-07-27" not in owner_page.text
    assert "2026-07-29" not in owner_page.text
    assert "Jul 27 – Jul 29" in owner_page.text
    assert '<span class="released-badge">released</span>' in owner_page.text

    template_root = Path(__file__).parents[2] / "cr8" / "web"
    for relative in ("owner/templates/owner/song.html",):
        source = (template_root / relative).read_text(encoding="utf-8")
        assert "|round(1)" not in source
        assert "song.first_date or" not in source
        assert "|round|int" in source
        assert "|display_date_range" in source
