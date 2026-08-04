"""Public links expose one expiring stream without exposing the catalog."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import sqlite3
from typing import Iterator
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from cr8.web.owner.app import create_app
from tests.web.conftest import WebFixture


HEADERS = {"X-CR8-Request": "1"}


def _mint(owner: TestClient, bounce_ulid: str, **extra: object) -> dict[str, str]:
    response = owner.post(
        "/api/shares",
        headers=HEADERS,
        json={"bounce_ulid": bounce_ulid, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _path(created: dict[str, str]) -> str:
    return urlparse(created["url"]).path


def _collection(
    owner: TestClient,
    web: WebFixture,
    *,
    name: str = "Night windows",
    bounce_ulids: list[str] | None = None,
) -> str:
    response = owner.post(
        "/api/collections",
        headers=HEADERS,
        data={
            "name": name,
            "source": "selection",
            "bounce_ulid": bounce_ulids or web.bounce_ulids[:2],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[1]


def _mint_collection(
    owner: TestClient, collection_ulid: str, **extra: object
) -> dict[str, str]:
    response = owner.post(
        "/api/shares",
        headers=HEADERS,
        json={"collection_ulid": collection_ulid, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


@contextmanager
def _public(web: WebFixture) -> Iterator[TestClient]:
    with TestClient(create_app(web.owner_settings)) as client:
        yield client


def test_mint_to_landing_returns_200(
    owner: TestClient, web: WebFixture
) -> None:
    bounce_ulid = web.bounce_ulids[0]
    created = _mint(owner, bounce_ulid)
    path = _path(created)
    raw_token = path.removeprefix("/s/")

    assert path.startswith("/s/")
    assert created["share_ulid"]
    expiry = datetime.fromisoformat(created["expires_at"])
    assert timedelta(hours=3, minutes=59) < expiry - datetime.now(UTC) <= timedelta(hours=4)

    connection = sqlite3.connect(web.db_path)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM shares WHERE ulid=?", (created["share_ulid"],)
    ).fetchone()
    connection.close()
    assert row is not None
    assert row["token_sha256"] != raw_token
    assert row["scope_mode"] == "frozen"
    assert json.loads(row["scope_json"]) == [bounce_ulid]
    assert row["allow_downloads"] == 0

    with _public(web) as public:
        landing = public.get(path)
    assert landing.status_code == 200
    assert "Stayhere" in landing.text
    assert f'src="{path}/audio"' in landing.text
    assert "login" not in landing.text.casefold()

    listed = owner.get("/api/shares", params={"bounce_ulid": bounce_ulid})
    assert listed.status_code == 200
    assert listed.json()["shares"] == [
        {
            "share_ulid": created["share_ulid"],
            "created_at": row["created_at"],
            "expires_at": created["expires_at"],
            "use_count": 1,
            "diverged": False,
        }
    ]


def test_single_track_landing_is_owner_personal_and_hides_catalog_metadata(
    owner: TestClient, web: WebFixture
) -> None:
    connection = sqlite3.connect(web.db_path)
    connection.execute(
        """
        INSERT INTO song_tags(song_id, dim, value, source, author, created_at)
        SELECT song_id, 'vibe', 'halflight', 'human', 'hareesh', ?
        FROM bounces WHERE public_id=?
        """,
        (datetime.now(UTC).isoformat(), web.bounce_ulids[0]),
    )
    connection.commit()
    connection.close()

    created = _mint(owner, web.bounce_ulids[0])
    path = _path(created)
    with _public(web) as public:
        landing = public.get(path)
        art = public.get(f"{path}/art")

    body = landing.text.casefold()
    assert landing.status_code == 200
    assert "hareesh sent you this" in body
    assert f'src="{path}/art"' in landing.text
    assert f'src="{path}/audio"' in landing.text
    assert 'class="play"' in landing.text
    assert 'type="range"' in landing.text
    assert "c minor" not in body
    assert "119 bpm" not in body
    assert "halflight" not in body
    assert "2026-07-29" not in body
    assert art.status_code == 200
    assert art.headers["content-type"] == "image/jpeg"


def test_collection_mint_freezes_scope_defaults_to_week_and_diverges(
    owner: TestClient, web: WebFixture
) -> None:
    collection_ulid = _collection(owner, web)
    original_scope = web.bounce_ulids[:2]
    created = _mint_collection(
        owner,
        collection_ulid,
        note="For the drive home.",
    )

    expiry = datetime.fromisoformat(created["expires_at"])
    remaining = expiry - datetime.now(UTC)
    assert timedelta(hours=167, minutes=59) < remaining <= timedelta(hours=168)

    connection = sqlite3.connect(web.db_path)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT s.*, c.ulid AS collection_ulid
        FROM shares AS s
        JOIN collections AS c ON c.id=s.landing_collection_id
        WHERE s.ulid=?
        """,
        (created["share_ulid"],),
    ).fetchone()
    connection.close()
    assert row is not None
    assert row["collection_ulid"] == collection_ulid
    assert row["label"] == "Night windows"
    assert row["note"] == "For the drive home."
    assert row["scope_mode"] == "frozen"
    assert json.loads(row["scope_json"]) == original_scope
    assert row["allow_downloads"] == 0

    before = owner.get(
        "/api/shares", params={"collection_ulid": collection_ulid}
    )
    assert before.status_code == 200
    assert before.json()["shares"][0]["diverged"] is False

    reordered = owner.post(
        f"/api/collections/{collection_ulid}/order",
        headers=HEADERS,
        data={"bounce_ulid": list(reversed(original_scope))},
    )
    assert reordered.status_code == 204
    after = owner.get(
        "/api/shares", params={"collection_ulid": collection_ulid}
    )
    assert after.status_code == 200
    assert after.json()["shares"][0]["diverged"] is True

    connection = sqlite3.connect(web.db_path)
    frozen = connection.execute(
        "SELECT scope_json FROM shares WHERE ulid=?",
        (created["share_ulid"],),
    ).fetchone()[0]
    connection.close()
    assert json.loads(frozen) == original_scope


def test_collection_share_redirects_members_and_renders_album_for_outsiders(
    owner: TestClient, web: WebFixture
) -> None:
    collection_ulid = _collection(owner, web, name="Night windows")
    created = _mint_collection(
        owner,
        collection_ulid,
        ttl_hours=24,
        note="Two songs, in this order.",
    )
    path = _path(created)

    member = owner.get(path, follow_redirects=False)
    assert member.status_code == 302
    assert member.headers["location"] == f"/collections/{collection_ulid}"

    with _public(web) as public:
        landing = public.get(path)
        second = public.get(f"{path}/audio", params={"i": 1})
        art = public.get(f"{path}/art", params={"i": 0})

    assert landing.status_code == 200
    assert "Night windows" in landing.text
    assert "Two songs, in this order." in landing.text
    assert "Stayhere" in landing.text
    assert "Diamond" in landing.text
    assert "C minor" not in landing.text
    assert f'data-audio-base="{path}/audio"' in landing.text
    assert f'src="{path}/audio?i=0"' in landing.text
    assert "audio.addEventListener(\"ended\"" in landing.text
    assert second.status_code == 200
    assert second.content == (
        web.mirror / "tracks" / f"{web.bounce_ulids[1]}.mp3"
    ).read_bytes()
    assert art.status_code == 200
    assert art.headers["content-type"] == "image/jpeg"


def test_expired_collection_share_uses_the_existing_denial(
    owner: TestClient, web: WebFixture
) -> None:
    collection_ulid = _collection(owner, web)
    created = _mint_collection(owner, collection_ulid)
    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    connection = sqlite3.connect(web.db_path)
    connection.execute(
        "UPDATE shares SET expires_at=? WHERE ulid=?",
        (expired_at, created["share_ulid"]),
    )
    connection.commit()
    connection.close()

    with _public(web) as public:
        landing = public.get(_path(created))
        stream = public.get(f"{_path(created)}/audio", params={"i": 0})
    assert landing.status_code == 410
    assert "this link has expired" in landing.text.casefold()
    assert stream.status_code == 410


def test_collection_share_refuses_an_unavailable_track_by_title(
    owner: TestClient, web: WebFixture
) -> None:
    collection_ulid = _collection(owner, web)
    connection = sqlite3.connect(web.db_path)
    connection.execute(
        """
        DELETE FROM mirror_files
        WHERE bounce_id=(SELECT id FROM bounces WHERE public_id=?)
        """,
        (web.bounce_ulids[1],),
    )
    connection.commit()
    connection.close()

    response = owner.post(
        "/api/shares",
        headers=HEADERS,
        json={"collection_ulid": collection_ulid},
    )
    assert response.status_code == 409
    assert "Diamond" in response.text


def test_collection_share_only_accepts_v1_ttls(
    owner: TestClient, web: WebFixture
) -> None:
    collection_ulid = _collection(owner, web)
    for ttl in (4, 48, "168", True, None):
        response = owner.post(
            "/api/shares",
            headers=HEADERS,
            json={"collection_ulid": collection_ulid, "ttl_hours": ttl},
        )
        assert response.status_code == 400


def test_public_audio_stream_returns_200_and_supports_range(
    owner: TestClient, web: WebFixture
) -> None:
    bounce_ulid = web.bounce_ulids[0]
    path = _path(_mint(owner, bounce_ulid)) + "/audio"
    source = web.mirror / "tracks" / f"{bounce_ulid}.mp3"

    with _public(web) as public:
        streamed = public.get(path)
        ranged = public.get(path, headers={"Range": "bytes=2-7"})

    assert streamed.status_code == 200
    assert streamed.content == source.read_bytes()
    assert streamed.headers["accept-ranges"] == "bytes"
    assert "attachment" not in streamed.headers.get("content-disposition", "")
    assert ranged.status_code == 206
    assert ranged.content == source.read_bytes()[2:8]
    assert ranged.headers["content-range"] == f"bytes 2-7/{source.stat().st_size}"
    assert "attachment" not in ranged.headers.get("content-disposition", "")


def test_revoke_turns_off_landing_and_stream(
    owner: TestClient, web: WebFixture
) -> None:
    bounce_ulid = web.bounce_ulids[0]
    created = _mint(owner, bounce_ulid)
    path = _path(created)

    revoked = owner.post(
        f"/api/shares/{created['share_ulid']}/revoke", headers=HEADERS
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"]
    listed = owner.get("/api/shares", params={"bounce_ulid": bounce_ulid})
    assert listed.status_code == 200
    assert listed.json()["shares"] == []

    with _public(web) as public:
        landing = public.get(path)
        stream = public.get(f"{path}/audio")
    assert landing.status_code == 410
    assert "this link was turned off" in landing.text.casefold()
    assert stream.status_code == 410


def test_expired_share_shows_expired_message(
    owner: TestClient, web: WebFixture
) -> None:
    bounce_ulid = web.bounce_ulids[0]
    created = _mint(owner, bounce_ulid)
    path = _path(created)
    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    connection = sqlite3.connect(web.db_path)
    connection.execute(
        "UPDATE shares SET expires_at=? WHERE ulid=?",
        (expired_at, created["share_ulid"]),
    )
    connection.commit()
    connection.close()

    with _public(web) as public:
        landing = public.get(path)
        stream = public.get(f"{path}/audio")
    assert landing.status_code == 410
    assert "this link has expired" in landing.text.casefold()
    assert stream.status_code == 410
    assert owner.get(
        "/api/shares", params={"bounce_ulid": bounce_ulid}
    ).json()["shares"] == []


def test_unknown_share_matches_revoked_public_denial(
    owner: TestClient, web: WebFixture
) -> None:
    created = _mint(owner, web.bounce_ulids[0])
    owner.post(
        f"/api/shares/{created['share_ulid']}/revoke", headers=HEADERS
    )
    with _public(web) as public:
        revoked = public.get(_path(created))
        unknown = public.get("/s/not-a-real-token")
    assert unknown.status_code == revoked.status_code == 410
    assert unknown.text == revoked.text
    assert "turned off" in unknown.text.casefold()


def test_share_management_requires_a_session(web: WebFixture) -> None:
    with _public(web) as public:
        created = public.post(
            "/api/shares",
            headers=HEADERS,
            json={"bounce_ulid": web.bounce_ulids[0]},
        )
        listed = public.get(
            "/api/shares", params={"bounce_ulid": web.bounce_ulids[0]}
        )
        revoked = public.post(
            "/api/shares/01ARZ3NDEKTSV4RRFFQ69G5FAV/revoke",
            headers=HEADERS,
        )
    assert created.status_code == 401
    assert listed.status_code == 401
    assert revoked.status_code == 401
