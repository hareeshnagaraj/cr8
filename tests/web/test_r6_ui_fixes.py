from __future__ import annotations

from pathlib import Path
import re

from fastapi.testclient import TestClient

from cr8.db import connect

from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}
CSS_PATH = Path("cr8/web/common/static/owner.css")
JS_PATH = Path("cr8/web/common/static/owner.js")


def test_library_rows_are_dense_and_share_one_in_flow_action_tail(
    owner: TestClient,
):
    page = owner.get("/")
    assert page.status_code == 200
    assert page.text.count('class="row-tail"') == 1
    rows = page.text.split(
        '<div class="library" role="table" aria-label="Songs">', 1
    )[1].split('<div class="batchbar">', 1)[0]
    assert "data-track-id=" in rows
    assert "data-audio-url=" in rows
    assert "data-track-title=" in rows
    for heavy_field in (
        "data-track-url=",
        "data-song-ulid=",
        "data-detail-era=",
        "data-detail-key=",
        "hx-get=",
        'class="row-tail"',
    ):
        assert heavy_field not in rows

    css = CSS_PATH.read_text(encoding="utf-8")
    compact = "".join(css.split())
    tail_rule = css.split(".row-tail{", 1)[1].split("}", 1)[0]
    assert "position:absolute" not in tail_rule
    assert "display:flex" in tail_rule
    assert "width:308px" in tail_rule
    assert ".song-line:has(> .row-tail)" in css
    assert "grid-template-columns:40pxminmax(0,1fr)308px" in compact
    assert ".row-title{min-width:0" in compact
    assert "text-overflow:ellipsis" in compact
    assert "contain-intrinsic-size:044px" in compact
    assert ".row-detailspan{width:32px;height:32px" in compact


def test_detail_tags_wrap_and_controls_use_dense_design_b_scale(
    web: WebFixture,
    owner: TestClient,
):
    status = owner.post(
        f"/songs/{web.song_ulids[0]}/tags/toggle",
        headers=CSRF,
        data={"dim": "status", "value": "demo"},
    )
    assert status.status_code == 200
    assert re.search(r"demo\s*·\s*set by you", status.text)
    assert "↗" not in status.text
    assert "↑" not in status.text

    css = CSS_PATH.read_text(encoding="utf-8")
    compact = "".join(css.split())
    assert "grid-template-columns:220pxminmax(460px,1fr)380px" in compact
    assert ".detail-scroll{padding:22px16px36px;overflow-x:hidden}" in compact
    assert ".chips-wrap{flex-wrap:wrap;overflow:visible}" in compact
    assert "height:32px;min-height:32px" in compact
    assert 'font-family:"IBMPlexMono",monospace;font-size:13px' in compact
    assert ".keeper-chips{gap:0}" in compact
    assert ".tag-provenance{" in css


def test_tags_page_is_a_dense_table_with_fixed_rename_control(
    web: WebFixture,
    owner: TestClient,
):
    for index, song_ulid in enumerate(web.song_ulids[:2]):
        response = owner.post(
            f"/songs/{song_ulid}/tags/toggle",
            headers=CSRF,
            data={"dim": "vibe", "value": f"dense-{index}"},
        )
        assert response.status_code == 200
    page = owner.get("/tags")
    assert page.status_code == 200
    assert 'class="vocabulary-table"' in page.text
    assert 'role="columnheader"' in page.text
    assert 'class="tag-rename-input"' in page.text

    compact = "".join(CSS_PATH.read_text(encoding="utf-8").split())
    assert "height:40px;min-height:40px" in compact
    assert "grid-template-columns:220px88px64px" in compact
    assert "width:220px;height:32px" in compact


def test_bulk_bar_is_quiet_until_selection_and_cap_note_stays_hidden(
    owner: TestClient,
):
    page = owner.get("/")
    assert 'data-batch-actions hidden' in page.text
    note = re.search(
        r'<span class="batch-download-note mono"[^>]*hidden></span>',
        page.text,
    )
    assert note is not None
    source = JS_PATH.read_text(encoding="utf-8")
    assert "actions.hidden = checked.length === 0" in source
    assert "note.hidden = !exceeds" in source


def test_collection_contract_refuses_unconfirmed_whole_library_and_removes(
    web: WebFixture,
    owner: TestClient,
):
    queue = owner.get("/api/library-queue").json()["tracks"]
    queue_ids = [str(item["id"]) for item in queue]
    refused = owner.post(
        "/collections",
        headers=CSRF,
        data={
            "source": "queue",
            "name": "Everything accidentally",
            "bounce_ulid": queue_ids,
        },
        follow_redirects=False,
    )
    assert refused.status_code == 400
    confirmed = owner.post(
        "/collections",
        headers=CSRF,
        data={
            "source": "queue",
            "confirm_all": "true",
            "name": "Everything deliberately",
            "bounce_ulid": queue_ids,
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    collection_url = confirmed.headers["location"]
    collection_ulid = collection_url.rsplit("/", 1)[1]
    detail = owner.get(collection_url)
    assert f"ordered collection · {len(queue_ids)} songs" in detail.text
    assert "collection-remove" in detail.text

    removed = owner.post(
        f"/collections/{collection_ulid}/remove/{queue_ids[0]}",
        headers={**CSRF, "HX-Request": "true"},
    )
    assert removed.status_code == 200
    connection = connect(web.db_path)
    try:
        remaining = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM collection_items AS ci
                JOIN collections AS c ON c.id=ci.collection_id
                WHERE c.ulid=?
                """,
                (collection_ulid,),
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert remaining == len(queue_ids) - 1


def test_selection_collection_keeps_only_the_selected_songs(
    web: WebFixture,
    owner: TestClient,
):
    """The selection branch used to raise whenever songs WERE selected, so every
    collection made from a selection failed and the only ones that saved were
    whole-library queue collections."""
    library = owner.get("/")
    ulids = list(
        dict.fromkeys(re.findall(r'data-bounce-ulid="([^"]+)"', library.text))
    )
    assert len(ulids) >= 2, "fixture needs at least two bounces"
    picked = ulids[:2]

    created = owner.post(
        "/collections",
        data={
            "name": "just these two",
            "source": "selection",
            "bounce_ulid": picked,
        },
        headers={"X-CR8-Request": "1"},
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text

    detail = owner.get(created.headers["location"])
    assert f"collection · {len(picked)} songs" in detail.text
    assert f"collection · {len(ulids)} songs" not in detail.text
