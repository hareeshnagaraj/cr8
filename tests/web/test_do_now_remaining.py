from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cr8.db import connect
from cr8.web.common.database import migrate
from cr8.web.common.text import era_css, era_for_date
from conftest import WebFixture


OWNER_JS = Path("cr8/web/common/static/owner.js")
PLAYBACK_JS = Path("cr8/web/common/static/playback.js")
BASE = Path("cr8/web/owner/templates/owner/base.html")
CSRF = {"X-CR8-Request": "1"}


def test_owner_queue_uses_the_complete_filtered_result_without_restarting(
    web: WebFixture, owner: TestClient
):
    full = owner.get("/api/library-queue")
    filtered = owner.get("/api/library-queue", params={"q": "Diamond"})
    assert full.status_code == 200
    assert len(full.json()["tracks"]) == len(web.song_ulids)
    assert [item["title"] for item in filtered.json()["tracks"]] == ["Diamond"]

    owner_source = OWNER_JS.read_text(encoding="utf-8")
    playback_source = PLAYBACK_JS.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8")
    assert '"/api/library-queue" + window.location.search' in owner_source
    assert "querySelectorAll(\".library .play-row[data-track-url]\")" not in (
        owner_source
    )
    reshuffle = playback_source[
        playback_source.index("Player.prototype.reshuffle"):
        playback_source.index("Player.prototype.removeQueueItem")
    ]
    assert "playCurrent" not in reshuffle
    assert 'data-player-mode' in base
    assert 'data-player-position' in base
    assert 'data-player-reveal' in base
    assert 'event.key === "L"' in owner_source


def test_dig_gestures_live_in_the_topbar_and_prioritize_untagged_filters(
    web: WebFixture, owner: TestClient
):
    page = owner.get("/")
    topbar = page.text[page.text.index('<header class="topbar">'):]
    topbar = topbar[:topbar.index("</header>")]
    assert "Shuffle everything" in topbar
    assert ">Dig<" in topbar
    assert "Dig untagged" in topbar
    assert f"shuffle these {len(web.song_ulids)}" in page.text

    filtered = owner.get("/api/dig", params={"q": "Diamond"})
    assert [item["title"] for item in filtered.json()["tracks"]] == ["Diamond"]

    tagged = owner.post(
        f"/songs/{web.song_ulids[0]}/tags/toggle",
        headers=CSRF,
        data={"dim": "vibe", "value": "dreamy"},
    )
    assert tagged.status_code == 200
    dig = owner.get("/api/dig").json()["tracks"]
    untagged = owner.get(
        "/api/dig", params={"untagged": "true"}
    ).json()["tracks"]
    assert len(untagged) == len(web.song_ulids) - 1
    assert all("no vibe yet" in item["reason"] for item in untagged)
    assert dig[-1]["title"] == "Stayhere"


def test_filter_rail_counts_multiselects_and_preserves_scoped_swaps(
    web: WebFixture, owner: TestClient
):
    for song_ulid, value in (
        (web.song_ulids[0], "dreamy"),
        (web.song_ulids[1], "warm"),
        (web.song_ulids[2], "dreamy"),
    ):
        response = owner.post(
            f"/songs/{song_ulid}/tags/toggle",
            headers=CSRF,
            data={"dim": "vibe", "value": value},
        )
        assert response.status_code == 200

    union = owner.get(
        "/",
        params=[("vibe", "dreamy"), ("vibe", "warm")],
    )
    assert union.status_code == 200
    union_rows = union.text.split(
        '<div class="library" role="table" aria-label="Songs">', 1
    )[1].split('<div class="batchbar">', 1)[0]
    assert "Stayhere" in union_rows
    assert "Diamond" in union_rows
    assert "Skylinedrive" in union_rows
    assert "Pensive Arpey" not in union_rows
    assert "vibe: dreamy" in union.text
    assert "vibe: warm" in union.text

    narrowed = owner.get(
        "/",
        params=[
            ("vibe", "dreamy"),
            ("vibe", "warm"),
            ("era", "nova1"),
        ],
    )
    rows = narrowed.text.split(
        '<div class="library" role="table" aria-label="Songs">', 1
    )[1].split('<div class="batchbar">', 1)[0]
    assert "Diamond" in rows
    assert "Stayhere" not in rows
    assert "Skylinedrive" not in rows

    page = owner.get("/")
    assert 'data-facet-group="status"' not in page.text
    assert page.text.count("— untagged —") == 4
    assert 'hx-target="#library-results"' in page.text
    assert 'data-multi-url=' in page.text
    assert "scrollbar-gutter:stable" in Path(
        "cr8/web/common/static/owner.css"
    ).read_text(encoding="utf-8")


def test_derived_facets_backfill_idempotently_and_undated_is_neutral(
    web: WebFixture, owner: TestClient
):
    assert era_for_date(None) == ("undated", "unknown")
    assert era_for_date("not-a-date") == ("undated", "unknown")
    assert era_css("undated") == "unknown"
    assert era_css("NOVA1") == "nova1"
    assert era_css("working") == "working"

    eras_payload = owner.get("/api/eras").json()
    assert isinstance(eras_payload, list)
    by_name = {row["name"]: row for row in eras_payload}
    assert set(by_name) == {"PELICANA", "NOVA1", "working", "undated"}
    assert by_name["undated"] == {
        "name": "undated",
        "css": "unknown",
        "color": "rgba(255,255,255,.14)",
    }
    assert by_name["NOVA1"]["css"] == "nova1"
    assert by_name["working"]["color"] == "oklch(0.86 0.16 115)"

    connection = connect(web.db_path)
    try:
        connection.execute(
            """
            INSERT INTO songs(slug, title, status, public_id)
            VALUES('unknown-date', 'Unknown Date', 'demo',
                   '01ARZ3NDEKTSV4RRFFQ69G5FB4')
            """
        )
        connection.execute(
            "UPDATE meta SET value='0' WHERE key='web_schema_version'"
        )
    finally:
        connection.close()
    migrate(web.db_path)

    connection = connect(web.db_path)
    try:
        eras = {
            row["name"]: (row["date_start"], row["date_end"], row["color"])
            for row in connection.execute(
                "SELECT name, date_start, date_end, color FROM eras"
            )
        }
        assert set(eras) == {"PELICANA", "NOVA1", "working", "undated"}
        assert eras["undated"][2] == "rgba(255,255,255,.14)"
        assert connection.execute(
            """
            SELECT e.name FROM songs s JOIN eras e ON e.id=s.era_id
            WHERE s.slug='unknown-date'
            """
        ).fetchone()[0] == "undated"
        use_rows = connection.execute(
            """
            SELECT song_id, value, source FROM song_tags
            WHERE dim='use' ORDER BY song_id, value
            """
        ).fetchall()
        assert len({row["value"] for row in use_rows}) >= 10
        assert {
            row["source"] for row in use_rows
        } <= {
            "derived-key",
            "derived-bpm",
            "derived-era",
            "derived-duration",
            "derived-version",
            "derived-stems",
        }
        first = use_rows[0]
        song_ulid = connection.execute(
            "SELECT public_id FROM songs WHERE id=?", (first["song_id"],)
        ).fetchone()[0]
        value = first["value"]
        before = connection.execute(
            "SELECT COUNT(*) FROM song_tags"
        ).fetchone()[0]
    finally:
        connection.close()

    migrate(web.db_path)
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM song_tags"
        ).fetchone()[0] == before
    finally:
        connection.close()

    panel = owner.get(f"/songs/{song_ulid}/tag-panel")
    assert "provenance-derived-" in panel.text
    promoted = owner.post(
        f"/songs/{song_ulid}/tags/toggle",
        headers=CSRF,
        data={"dim": "use", "value": value},
    )
    assert promoted.status_code == 200
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            """
            SELECT source FROM song_tags
            WHERE song_id=(SELECT id FROM songs WHERE public_id=?)
              AND dim='use' AND value=?
            """,
            (song_ulid, value),
        ).fetchone()[0] == "human"
    finally:
        connection.close()

    css = Path("cr8/web/common/static/owner.css").read_text(encoding="utf-8")
    assert "--era-unknown:rgba(255,255,255,.14)" in css
    assert 'text-decoration-style:dotted' in css


def test_session_undo_restores_bulk_tags_chip_history_keeper_and_heart(
    web: WebFixture, owner: TestClient
):
    extra_ulids = [f"undo-song-{index:02d}" for index in range(8)]
    connection = connect(web.db_path)
    try:
        for index, song_ulid in enumerate(extra_ulids):
            connection.execute(
                """
                INSERT INTO songs(slug, title, status, public_id)
                VALUES(?, ?, 'demo', ?)
                """,
                (f"undo-{index}", f"Undo {index}", song_ulid),
            )
    finally:
        connection.close()
    selected = [*web.song_ulids, *extra_ulids]
    bulk = owner.post(
        "/selection",
        headers=CSRF,
        data={
            "song_ulid": selected,
            "tag_dim": "vibe",
            "tag_value": "night-drive",
        },
    )
    assert bulk.status_code == 200
    assert "tagged 12 songs night-drive" in bulk.text
    assert 'hx-post="/undo"' in bulk.text
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM song_tags
            WHERE dim='vibe' AND value='night-drive'
            """
        ).fetchone()[0] == 12
    finally:
        connection.close()

    undone = owner.post("/undo", headers=CSRF)
    assert undone.status_code == 200
    assert "Undid tagged 12 songs night-drive." in undone.text
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM song_tags
            WHERE dim='vibe' AND value='night-drive'
            """
        ).fetchone()[0] == 0
    finally:
        connection.close()

    song_ulid = web.song_ulids[0]
    for _ in range(2):
        assert owner.post(
            f"/songs/{song_ulid}/tags/toggle",
            headers=CSRF,
            data={"dim": "vibe", "value": "mistake"},
        ).status_code == 200
    assert owner.post("/undo", headers=CSRF).status_code == 200
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            """
            SELECT source FROM song_tags
            WHERE song_id=(SELECT id FROM songs WHERE public_id=?)
              AND dim='vibe' AND value='mistake'
            """,
            (song_ulid,),
        ).fetchone()[0] == "human"
    finally:
        connection.close()
    assert owner.post("/undo", headers=CSRF).status_code == 200
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM song_tags
            WHERE song_id=(SELECT id FROM songs WHERE public_id=?)
              AND dim='vibe' AND value='mistake'
            """,
            (song_ulid,),
        ).fetchone()[0] == 0
    finally:
        connection.close()

    assert owner.post(
        f"/songs/{song_ulid}/tags/toggle",
        headers=CSRF,
        data={"dim": "keeper", "value": "5"},
    ).status_code == 200
    owner.post("/undo", headers=CSRF)
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            "SELECT keeper FROM songs WHERE public_id=?", (song_ulid,)
        ).fetchone()[0] == 0
    finally:
        connection.close()

    bounce_ulid = web.bounce_ulids[0]
    assert owner.post(
        f"/reactions/{bounce_ulid}/heart", headers=CSRF
    ).status_code == 200
    owner.post("/undo", headers=CSRF)
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM reactions
            WHERE bounce_ulid=? AND actor='hareesh' AND kind='heart'
              AND deleted_at IS NULL
            """,
            (bounce_ulid,),
        ).fetchone()[0] == 0
    finally:
        connection.close()

    owner_source = OWNER_JS.read_text(encoding="utf-8")
    assert 'event.key.toLowerCase() === "z"' in owner_source
    assert 'event.key.toLowerCase() === "u"' in owner_source
    assert "30000" in owner_source
