from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from cr8.db import connect
from cr8.web.common.settings import AppSettings
from cr8.web.owner.app import create_app as create_owner_app


SONGS = (
    (
        "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "01ARZ3NDEKTSV4RRFFQ69G5FAW",
        "Stayhere",
        "2026-07-29",
        1,
    ),
    (
        "01ARZ3NDEKTSV4RRFFQ69G5FAX",
        "01ARZ3NDEKTSV4RRFFQ69G5FAY",
        "Diamond",
        "2025-11-17",
        1,
    ),
    (
        "01ARZ3NDEKTSV4RRFFQ69G5FAZ",
        "01ARZ3NDEKTSV4RRFFQ69G5FB0",
        "Skylinedrive",
        "2026-07-09",
        1,
    ),
    (
        "01ARZ3NDEKTSV4RRFFQ69G5FB1",
        "01ARZ3NDEKTSV4RRFFQ69G5FB2",
        "Pensive Arpey",
        "2023-12-08",
        1,
    ),
)
SECOND_BOUNCE = "01ARZ3NDEKTSV4RRFFQ69G5FB3"


@dataclass
class WebFixture:
    root: Path
    db_path: Path
    mirror: Path
    owner_settings: AppSettings
    owner: TestClient

    @property
    def bounce_ulids(self) -> list[str]:
        return [item[1] for item in SONGS]

    @property
    def song_ulids(self) -> list[str]:
        return [item[0] for item in SONGS]


def _build_catalog(root: Path) -> tuple[Path, Path]:
    db_path = root / "catalog.db"
    mirror = root / "mirror"
    for name in ("tracks", "peaks", "art"):
        (mirror / name).mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    try:
        for index, (song_ulid, bounce_ulid, title, when, version) in enumerate(
            SONGS, start=1
        ):
            connection.execute(
                """
                INSERT INTO songs(
                  slug, title, status, public_id, first_date, last_date,
                  key_canon, key_camelot, bpm
                ) VALUES(?, ?, 'demo', ?, ?, ?, ?, ?, ?)
                """,
                (
                    title.casefold().replace(" ", "-"),
                    title,
                    song_ulid,
                    when,
                    when,
                    "C minor" if index % 2 else "B minor",
                    "5A" if index % 2 else "10A",
                    118.0 + index,
                ),
            )
            song_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            connection.execute(
                """
                INSERT INTO bounces(
                  public_id, song_id, source_stem, bounce_date, version
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (bounce_ulid, song_id, title.casefold(), when, version),
            )
            bounce_id = int(
                connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO files(
                  relpath, layer, ext, duration_s, bounce_id, parse_status
                ) VALUES(?, 'curated', '.wav', ?, ?, 'parsed')
                """,
                (f"{title}.wav", 60.0 + index, bounce_id),
            )
            (root / f"{title}.wav").write_bytes(
                b"RIFF" + bytes([index]) * 1024
            )
            relpath = f"tracks/{bounce_ulid}.mp3"
            connection.execute(
                """
                INSERT INTO mirror_files(bounce_id, mirror_relpath)
                VALUES(?, ?)
                """,
                (bounce_id, relpath),
            )
            (mirror / relpath).write_bytes(b"ID3" + bytes([index]) * 256)
            (mirror / "peaks" / f"{bounce_ulid}.json").write_text(
                '{"version":2,"data":[-1,1,-4,5,-8,9]}',
                encoding="utf-8",
            )
            (mirror / "art" / f"{song_ulid}.jpg").write_bytes(
                b"\xff\xd8" + bytes([index]) * 64 + b"\xff\xd9"
            )

        song_id = int(
            connection.execute(
                "SELECT id FROM songs WHERE public_id=?", (SONGS[0][0],)
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO bounces(
              public_id, song_id, source_stem, bounce_date, version
            ) VALUES(?, ?, 'stayhere-v2', '2026-07-30', 2)
            """,
            (SECOND_BOUNCE, song_id),
        )
        bounce_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO files(
              relpath, layer, ext, duration_s, bounce_id, parse_status
            ) VALUES('Stayhere-v2.wav', 'curated', '.wav', 66, ?, 'parsed')
            """,
            (bounce_id,),
        )
        (root / "Stayhere-v2.wav").write_bytes(b"RIFF-v2" * 256)
        connection.execute(
            """
            INSERT INTO mirror_files(bounce_id, mirror_relpath)
            VALUES(?, ?)
            """,
            (bounce_id, f"tracks/{SECOND_BOUNCE}.mp3"),
        )
        (mirror / "tracks" / f"{SECOND_BOUNCE}.mp3").write_bytes(b"ID3v2")
        (mirror / "peaks" / f"{SECOND_BOUNCE}.json").write_text(
            '{"version":2,"data":[-1,1]}', encoding="utf-8"
        )
    finally:
        connection.close()
    (mirror / ".crate_mirror_sentinel").write_text("cr8 mirror\n", encoding="utf-8")
    return db_path, mirror


@pytest.fixture
def web(tmp_path: Path) -> WebFixture:
    db_path, mirror = _build_catalog(tmp_path)
    owner_settings = AppSettings(
        "owner",
        tmp_path,
        db_path,
        mirror,
        b"owner-secret-" * 4,
        cookie_secure=False,
    )
    with TestClient(create_owner_app(owner_settings)) as owner:
        yield WebFixture(
            root=tmp_path,
            db_path=db_path,
            mirror=mirror,
            owner_settings=owner_settings,
            owner=owner,
        )


@pytest.fixture
def owner(web: WebFixture) -> TestClient:
    response = web.owner.post(
        "/setup",
        headers={"X-CR8-Request": "1"},
        data={
            "username": "hareesh",
            "display": "Hareesh",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200
    return web.owner
