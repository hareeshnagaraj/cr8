from __future__ import annotations

import re

from fastapi.testclient import TestClient

from cr8.db import connect

from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}


def test_authenticated_app_smoke(web: WebFixture, owner: TestClient):
    library = owner.get("/")
    assert library.status_code == 200
    assert (
        "Move the cursor, hear the song, and tag what your ear tells you."
        in library.text
    )
    assert "versions · 2" in library.text
    assert "data-track-url" in library.text
    assert re.search(
        r'<button class="song-row play-row"[^>]+data-audio-url="/m/[^"]+"',
        library.text,
    )
    assert library.text.count('class="row-tail"') == 1
    assert re.search(
        r'class="detail-action" href="/songs/[^"]+"',
        library.text,
    )
    assert owner.get(f"/api/tracks/{web.bounce_ulids[0]}").status_code == 200

    assert (
        owner.post(
            f"/reactions/{web.bounce_ulids[0]}/heart", headers=CSRF
        ).status_code
        == 200
    )
    assert (
        owner.post(
            f"/reactions/{web.bounce_ulids[0]}/chip",
            headers=CSRF,
            data={"value": "dreamy"},
        ).status_code
        == 200
    )
    for bounce, value in zip(
        web.bounce_ulids[:3], ("gem", "keep", "archive"), strict=True
    ):
        assert (
            owner.post(
                f"/triage/{bounce}", headers=CSRF, data={"value": value}
            ).status_code
            == 200
        )

    first = web.bounce_ulids[0]
    note_response = owner.post(
        f"/reactions/{first}/note",
        headers=CSRF,
        data={"note": "Love the chorus\u0007"},
    )
    assert note_response.status_code == 201
    assert "hareesh" in note_response.text

    connection = connect(web.db_path)
    note = connection.execute(
        """
        SELECT value, actor FROM reactions
        WHERE kind='note' AND deleted_at IS NULL
        """
    ).fetchone()
    connection.close()
    assert "\u0007" not in note["value"]
    assert note["actor"] == "hareesh"

    activity = owner.get("/activity")
    assert activity.status_code == 200
    assert "Love the chorus" in activity.text
    assert "hareesh" in activity.text
