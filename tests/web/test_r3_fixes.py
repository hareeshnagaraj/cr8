from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from cr8.db import connect
from cr8.paths import archive_relpath
from cr8.web.common.downloads import bounce_download_asset

from conftest import SONGS, WebFixture


CSRF = {"X-CR8-Request": "1"}


def test_owner_downloads_original_and_mp3_with_human_names_and_ranges(
    web: WebFixture, owner: TestClient
):
    bounce_ulid = web.bounce_ulids[0]
    source = web.root / "Stayhere.wav"
    original = owner.get(
        f"/download/{bounce_ulid}", params={"format": "original"}
    )
    assert original.status_code == 200
    assert original.content == source.read_bytes()
    assert original.headers["content-disposition"] == (
        'attachment; filename="Stayhere.wav"'
    )
    assert original.headers["accept-ranges"] == "bytes"

    ranged = owner.get(
        f"/download/{bounce_ulid}",
        params={"format": "original"},
        headers={"Range": "bytes=4-11"},
    )
    assert ranged.status_code == 206
    assert ranged.content == source.read_bytes()[4:12]
    assert ranged.headers["content-range"] == (
        f"bytes 4-11/{source.stat().st_size}"
    )

    rendition = owner.get(
        f"/download/{bounce_ulid}", params={"format": "mp3"}
    )
    assert rendition.status_code == 200
    assert rendition.content == (
        web.mirror / "tracks" / f"{bounce_ulid}.mp3"
    ).read_bytes()
    assert rendition.headers["content-disposition"] == (
        'attachment; filename="stayhere.mp3"'
    )


def test_original_download_resolves_an_archive_qualified_source(
    web: WebFixture,
):
    archive = web.root / "2021-New-Projects"
    archive.mkdir()
    source = archive / "Diamond.wav"
    source.write_bytes(b"archive-original")
    relpath = archive_relpath(archive, source)
    connection = connect(web.db_path)
    try:
        connection.execute(
            "UPDATE files SET relpath=? WHERE relpath='Diamond.wav'",
            (relpath,),
        )
    finally:
        connection.close()

    settings = replace(web.owner_settings, archive_roots=(archive,))
    asset = bounce_download_asset(
        settings,
        web.bounce_ulids[1],
        format="original",
    )
    assert asset.path == source
    assert asset.filename == "Diamond.wav"


def test_owner_download_rejects_a_catalog_path_outside_the_corpus(
    web: WebFixture, owner: TestClient
):
    outside = web.root.parent / "outside.wav"
    outside.write_bytes(b"outside")
    connection = connect(web.db_path)
    try:
        connection.execute(
            "UPDATE files SET relpath='../outside.wav' WHERE relpath='Diamond.wav'"
        )
    finally:
        connection.close()
    response = owner.get(
        f"/download/{web.bounce_ulids[1]}",
        params={"format": "original"},
    )
    assert response.status_code == 404


def test_selection_download_is_a_streamed_valid_zip_of_originals(
    web: WebFixture, owner: TestClient
):
    response = owner.get(
        "/download/selection",
        params={"ulids": ",".join(web.bounce_ulids[:2])},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-cr8-included-count"] == "2"
    assert response.headers["x-cr8-trimmed-count"] == "0"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["Stayhere.wav", "Diamond.wav"]
        assert archive.read("Stayhere.wav") == (
            web.root / "Stayhere.wav"
        ).read_bytes()
        assert archive.read("Diamond.wav") == (
            web.root / "Diamond.wav"
        ).read_bytes()


def test_owner_stem_download_reads_the_archive_with_a_human_name(
    web: WebFixture, owner: TestClient
):
    stem_ulid = "01ARZ3NDEKTSV4RRFFQ69G5FC0"
    bounce_ulid = web.bounce_ulids[0]
    archive = web.root / "stems" / bounce_ulid / "vocals.flac"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"fLaC-vocals")
    connection = connect(web.db_path)
    try:
        bounce_id = int(
            connection.execute(
                "SELECT id FROM bounces WHERE public_id=?", (bounce_ulid,)
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO stem_runs(
              bounce_id, recipe, model_a, src_relpath, src_sha256,
              separator_version, ok
            ) VALUES(?, 'default-v1', 'model', 'Stayhere.wav', 'src',
                     'test', 1)
            """,
            (bounce_id,),
        )
        run_id = int(
            connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO stems(
              public_id, run_id, bounce_id, kind, archive_relpath,
              archive_sha256
            ) VALUES(?, ?, ?, 'vocals', ?, 'archive')
            """,
            (
                stem_ulid,
                run_id,
                bounce_id,
                archive.relative_to(web.root).as_posix(),
            ),
        )
    finally:
        connection.close()

    response = owner.get(f"/download/stem/{stem_ulid}")
    assert response.status_code == 200
    assert response.content == archive.read_bytes()
    assert response.headers["content-disposition"] == (
        'attachment; filename="stayhere-vocals.flac"'
    )


def test_download_controls_dig_mode_and_cursor_contract_render(
    web: WebFixture, owner: TestClient
):
    library = owner.get("/")
    assert "Download selected" in library.text
    assert "data-original-size" in library.text
    assert 'data-stop-dig hidden' in library.text
    assert "tag playing" in library.text
    assert "?format=original" in library.text

    detail = owner.get(f"/songs/{web.song_ulids[0]}")
    assert "original · wav" in detail.text
    assert "mp3 · 320" in detail.text

    source = Path("cr8/web/common/static/owner.js").read_text(
        encoding="utf-8"
    )
    assert 'event.key.toLowerCase() === "d"' in source
    assert "updateDetailFromTrack(track)" in source
    assert "followPlayback: true" in source
    assert "isPlayingBounce(track.bounce_ulid)" in source
    assert "requestDetail: true" in source
    assert 'row.scrollIntoView({block: "center"})' in source
    assert "digging · " in source
    assert "stopDigging()" in source


def test_collection_source_is_explicit_and_filter_can_capture_current_library(
    web: WebFixture, owner: TestClient
):
    refused = owner.post(
        "/collections",
        headers=CSRF,
        data={"name": "Whole current shelf"},
        follow_redirects=False,
    )
    assert refused.status_code == 400
    response = owner.post(
        "/collections",
        headers=CSRF,
        data={"name": "Whole current shelf", "source": "filter"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    connection = connect(web.db_path)
    try:
        count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM collection_items AS ci
                JOIN collections AS c ON c.id=ci.collection_id
                WHERE c.name='Whole current shelf'
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert count == len(SONGS)


def test_selected_filters_use_neutral_fill_without_accent_rails():
    owner_css = Path("cr8/web/common/static/owner.css").read_text(
        encoding="utf-8"
    )
    rail_rule = owner_css.split(
        '.rail-filter[aria-pressed="true"]{', 1
    )[1].split("}", 1)[0]
    assert "box-shadow" not in rail_rule
    assert "--era-" not in rail_rule
    assert "font-weight:650" in rail_rule
    assert ".button.danger{box-shadow" not in owner_css
