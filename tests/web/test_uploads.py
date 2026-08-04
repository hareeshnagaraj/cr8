"""Uploads: the first write path into the crate from outside this machine.

The corpus is a read-only mirror another machine syncs, so nothing here may
land there. Everything else worth testing is a refusal: a file too large, a
name trying to escape its directory, a caller with no credentials, the same
bounce sent twice.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess

from fastapi.testclient import TestClient
import pytest

from cr8.web.common.auth import create_member
from cr8.tooling import find_tool
from cr8.web.owner.routes_upload import (
    MAX_UPLOAD_BYTES,
    VIDEO_EXTENSIONS,
    safe_filename,
)
from tests.web.conftest import WebFixture


HEADERS = {"X-CR8-Request": "1"}
FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")
requires_ffmpeg = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="ffmpeg and ffprobe required",
)


def _upload(client: TestClient, name: str, data: bytes, **kwargs: object):
    return client.post(
        "/api/upload",
        headers=HEADERS,
        files={"file": (name, data, "audio/wav")},
        **kwargs,
    )


def _fixture_video(path: Path, *, with_audio: bool) -> bytes:
    assert FFMPEG is not None
    command = [
        str(FFMPEG),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:r=10:d=2",
    ]
    if with_audio:
        command.extend(
            (
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100:duration=2",
            )
        )
    command.extend(("-c:v", "mpeg4", "-pix_fmt", "yuv420p"))
    if with_audio:
        command.extend(("-c:a", "aac", "-shortest"))
    else:
        command.append("-an")
    command.append(str(path))
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return path.read_bytes()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.wav", "passwd.wav"),
        ("..\\..\\windows\\evil.wav", "evil.wav"),
        ("/absolute/path/take.wav", "take.wav"),
        ("normal take.wav", "normal take.wav"),
        ("with(brackets)&stuff.wav", "with(brackets)&stuff.wav"),
        ("....wav", "wav"),
    ],
)
def test_filenames_cannot_escape_their_folder(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected


def test_a_very_long_name_is_truncated_but_keeps_its_extension() -> None:
    name = safe_filename("x" * 400 + ".wav")
    assert name.endswith(".wav")
    assert len(name) < 200


def test_upload_needs_credentials(web: WebFixture) -> None:
    response = _upload(web.owner, "take.wav", b"RIFF1234")
    assert response.status_code == 401


def test_a_signed_in_member_can_upload(owner: TestClient, web: WebFixture) -> None:
    response = _upload(owner, "fresh idea.wav", b"RIFF" + b"\0" * 2048)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["stored"] == 1

    landed = web.root / "drops" / "hareesh" / "fresh idea.wav"
    assert landed.is_file()
    assert landed.read_bytes().startswith(b"RIFF")


def test_uploads_land_outside_the_corpus(owner: TestClient, web: WebFixture) -> None:
    """The corpus is read-only. Nothing may be written into it, ever."""
    _upload(owner, "keepout.wav", b"RIFF" + b"\0" * 512)
    corpus_files = {path.name for path in web.root.rglob("keepout.wav")}
    assert corpus_files == {"keepout.wav"}
    landed = next(web.root.rglob("keepout.wav"))
    assert "drops" in landed.parts


def test_a_rejected_extension_is_refused(owner: TestClient, web: WebFixture) -> None:
    response = owner.post(
        "/api/upload",
        headers=HEADERS,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert not (web.root / "drops" / "hareesh" / "notes.txt").exists()


def test_video_fallback_extensions_are_explicit() -> None:
    assert VIDEO_EXTENSIONS == {".mp4", ".mov", ".webm", ".mkv"}


@requires_ffmpeg
def test_a_small_video_upload_extracts_only_its_audio(
    owner: TestClient,
    web: WebFixture,
    tmp_path: Path,
) -> None:
    source = tmp_path / "fixture.mp4"
    response = owner.post(
        "/api/upload",
        headers=HEADERS,
        files={
            "file": (
                "phone clip.mp4",
                _fixture_video(source, with_audio=True),
                "video/mp4",
            )
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()["files"][0]
    assert result["ok"] is True
    assert result["filename"] == "phone clip.m4a"

    landed = web.root / "drops" / "hareesh" / "phone clip.m4a"
    assert landed.is_file()
    assert not (web.root / "drops" / "hareesh" / "phone clip.mp4").exists()

    assert FFPROBE is not None
    probe = subprocess.run(
        (
            str(FFPROBE),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "csv=p=0",
            str(landed),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert "aac,audio" in probe.stdout
    assert ",video" not in probe.stdout


@requires_ffmpeg
def test_a_video_without_audio_reports_a_per_file_error(
    owner: TestClient,
    web: WebFixture,
    tmp_path: Path,
) -> None:
    source = tmp_path / "silent.mp4"
    response = owner.post(
        "/api/upload",
        headers=HEADERS,
        files={
            "file": (
                "silent.mp4",
                _fixture_video(source, with_audio=False),
                "video/mp4",
            )
        },
    )
    assert response.status_code == 400, response.text
    result = response.json()["files"][0]
    assert result["ok"] is False
    assert "audio track" in result["error"]
    assert not list((web.root / "drops" / "hareesh").glob("silent*"))


def test_an_empty_file_is_refused(owner: TestClient, web: WebFixture) -> None:
    response = _upload(owner, "empty.wav", b"")
    assert response.status_code == 400
    assert not (web.root / "drops" / "hareesh" / "empty.wav").exists()


def test_an_oversized_declaration_is_refused_before_the_body(
    owner: TestClient
) -> None:
    response = owner.post(
        "/api/upload",
        headers={**HEADERS, "Content-Length": str(MAX_UPLOAD_BYTES * 2)},
        files={"file": ("huge.wav", b"RIFF", "audio/wav")},
    )
    assert response.status_code == 413


def test_the_same_file_twice_is_a_quiet_no_op(
    owner: TestClient, web: WebFixture
) -> None:
    payload = b"RIFF" + b"\x01" * 4096
    first = _upload(owner, "take.wav", payload)
    assert first.json()["files"][0].get("duplicate_of") is None

    second = _upload(owner, "take-again.wav", payload)
    assert second.status_code == 201
    assert second.json()["files"][0]["duplicate_of"] == "take.wav"

    connection = sqlite3.connect(web.db_path)
    count = connection.execute("SELECT COUNT(*) FROM uploads").fetchone()[0]
    connection.close()
    assert count == 1


def test_two_people_uploading_the_same_name_do_not_collide(
    owner: TestClient, web: WebFixture
) -> None:
    password = create_member(
        web.owner_settings, username="henry", display="Henry"
    ).password
    _upload(owner, "bounce.wav", b"RIFF" + b"\x02" * 1024)

    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    owner.post(
        "/login",
        data={"username": "henry", "password": password},
        follow_redirects=False,
    )
    _upload(owner, "bounce.wav", b"RIFF" + b"\x03" * 1024)

    assert (web.root / "drops" / "hareesh" / "bounce.wav").is_file()
    assert (web.root / "drops" / "henry" / "bounce.wav").is_file()


def test_uploads_are_recorded_with_who_sent_them(
    owner: TestClient, web: WebFixture
) -> None:
    _upload(owner, "attributed.wav", b"RIFF" + b"\x04" * 1024)
    listing = owner.get("/api/uploads").json()["uploads"]
    assert listing
    assert listing[0]["filename"] == "attributed.wav"
    assert listing[0]["uploaded_by"] == "hareesh"
    assert listing[0]["source"] == "browser"
    # Nothing has ingested it yet, and that is a real state, not a failure.
    assert listing[0]["state"] == "pending"


def test_the_uploads_list_needs_a_session(web: WebFixture) -> None:
    assert web.owner.get("/api/uploads").status_code == 401


def test_an_upload_token_works_without_a_browser_session(
    owner: TestClient, web: WebFixture
) -> None:
    created = owner.post(
        "/api/admin/tokens",
        headers=HEADERS,
        json={"username": "hareesh", "label": "henry's mac"},
    )
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    response = owner.post(
        "/api/upload",
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        files={"file": ("from-watcher.wav", b"RIFF" + b"\x05" * 1024, "audio/wav")},
    )
    assert response.status_code == 201, response.text
    assert (web.root / "drops" / "hareesh" / "from-watcher.wav").is_file()


def test_a_revoked_token_stops_working(owner: TestClient, web: WebFixture) -> None:
    created = owner.post(
        "/api/admin/tokens", headers=HEADERS, json={"username": "hareesh"}
    ).json()
    token = created["token"]
    assert (
        owner.post(
            f"/api/admin/tokens/{created['ulid']}/revoke", headers=HEADERS
        ).status_code
        == 200
    )

    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    response = owner.post(
        "/api/upload",
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        files={"file": ("nope.wav", b"RIFF1234", "audio/wav")},
    )
    assert response.status_code == 401
    assert "revoked" in response.json()["detail"]


def test_a_made_up_token_is_refused(owner: TestClient) -> None:
    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    response = owner.post(
        "/api/upload",
        headers={**HEADERS, "Authorization": "Bearer not-a-real-token"},
        files={"file": ("nope.wav", b"RIFF1234", "audio/wav")},
    )
    assert response.status_code == 401


def test_token_management_is_admin_only(owner: TestClient, web: WebFixture) -> None:
    password = create_member(
        web.owner_settings, username="henry", display="Henry"
    ).password
    owner.post("/logout", headers=HEADERS, follow_redirects=False)
    owner.post(
        "/login",
        data={"username": "henry", "password": password},
        follow_redirects=False,
    )
    assert owner.get("/api/admin/tokens").status_code == 403
    assert owner.post("/api/admin/tokens", headers=HEADERS, json={}).status_code == 403


def test_several_files_in_one_request(owner: TestClient, web: WebFixture) -> None:
    response = owner.post(
        "/api/upload",
        headers=HEADERS,
        files=[
            ("file", ("one.wav", b"RIFF" + b"\x06" * 512, "audio/wav")),
            ("file", ("two.wav", b"RIFF" + b"\x07" * 512, "audio/wav")),
            ("file", ("three.txt", b"nope", "text/plain")),
        ],
    )
    assert response.status_code == 201
    body = response.json()
    assert body["stored"] == 2
    assert any(not entry["ok"] for entry in body["files"])
