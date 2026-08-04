from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import WebFixture


def test_preview_art_requires_an_owner_session(web: WebFixture):
    response = web.owner.get(
        f"/art-preview/spectral/{web.bounce_ulids[0]}"
    )
    assert response.status_code == 401
    assert web.owner.get("/api/cover-previews").status_code == 401
    assert web.owner.get(f"/art-strip/{web.bounce_ulids[0]}").status_code == 401


def test_preview_art_serves_both_styles_without_changing_live_art(
    web: WebFixture, owner: TestClient
):
    bounce_ulid = web.bounce_ulids[0]
    live_path = next((web.mirror / "art").glob("*.jpg"))
    live_before = live_path.read_bytes()
    for style in ("spectral", "envelope"):
        destination = web.mirror / "art-preview" / style / f"{bounce_ulid}.jpg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = b"\xff\xd8" + style.encode() + b"\xff\xd9"
        destination.write_bytes(payload)
        response = owner.get(f"/art-preview/{style}/{bounce_ulid}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content == payload
    availability = owner.get("/api/cover-previews")
    assert availability.status_code == 200
    assert availability.json() == {
        "spectral": [bounce_ulid],
        "envelope": [bounce_ulid],
    }
    assert live_path.read_bytes() == live_before
    assert owner.get(f"/art-preview/spectral/{web.bounce_ulids[1]}").status_code == 404
    assert owner.get(f"/art-preview/unknown/{bounce_ulid}").status_code == 422


def test_art_strip_serves_owner_bytes_and_returns_404_when_absent(
    web: WebFixture, owner: TestClient
):
    bounce_ulid = web.bounce_ulids[0]
    destination = web.mirror / "art-strips" / f"{bounce_ulid}.jpg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = b"\xff\xd8spectral-strip\xff\xd9"
    destination.write_bytes(payload)

    response = owner.get(f"/art-strip/{bounce_ulid}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.content == payload
    assert owner.get(f"/art-strip/{web.bounce_ulids[1]}").status_code == 404
