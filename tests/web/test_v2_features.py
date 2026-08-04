from __future__ import annotations

from pathlib import Path
import re
import subprocess
import textwrap

from fastapi.testclient import TestClient

from cr8.db import connect

from conftest import SECOND_BOUNCE, WebFixture


CSRF = {"X-CR8-Request": "1"}
PLAYBACK = Path("cr8/web/common/static/playback.js")
OWNER_JS = Path("cr8/web/common/static/owner.js")


def test_every_tag_dimension_is_inline_and_batch_add_remove_handles_five_songs(
    web: WebFixture, owner: TestClient
):
    library = owner.get("/")
    for dim in ("status", "key", "vibe", "instr", "collab"):
        assert f'data-tag-dimension="{dim}"' in library.text
    assert "data-tag-input" in library.text
    assert 'class="column-sort"' not in library.text

    title_sorted = owner.get("/", params={"sort": "title"})
    assert title_sorted.status_code == 200
    titles = re.findall(
        r'<span class="row-title">([^<]+)</span>', title_sorted.text
    )
    assert titles == sorted(titles, key=str.casefold)

    added = owner.post(
        f"/songs/{web.song_ulids[0]}/tags/toggle",
        headers=CSRF,
        data={"dim": "vibe", "value": "nocturnal"},
    )
    assert added.status_code == 200
    assert 'value="nocturnal"' in added.text
    assert 'aria-pressed="true"' in added.text
    assert 'hx-select="unset"' in added.text
    assert "human" in added.text
    owner.post(
        f"/songs/{web.song_ulids[0]}/tags/toggle",
        headers=CSRF,
        data={"dim": "status", "value": "mixed"},
    )
    owner.post(
        f"/songs/{web.song_ulids[0]}/tags/toggle",
        headers=CSRF,
        data={"dim": "key", "value": "F minor"},
    )

    connection = connect(web.db_path)
    try:
        connection.execute(
            """
            INSERT INTO songs(slug, title, public_id)
            VALUES('fifth-song', 'Fifth Song', '01FIFTHSONG0000000000000000')
            """
        )
        fifth = "01FIFTHSONG0000000000000000"
        song_ulids = [*web.song_ulids, fifth]
    finally:
        connection.close()

    batch = owner.post(
        "/selection",
        headers=CSRF,
        data={
            "song_ulid": song_ulids,
            "tag_dim": "vibe",
            "tag_value": "road-trip",
            "tag_action": "add",
        },
    )
    assert batch.status_code == 200
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM song_tags
            WHERE dim='vibe' AND value='road-trip' AND source='human'
            """
        ).fetchone()[0] == 5
        song = connection.execute(
            """
            SELECT status, key_canon FROM songs WHERE public_id=?
            """,
            (web.song_ulids[0],),
        ).fetchone()
    finally:
        connection.close()
    assert (song["status"], song["key_canon"]) == ("mixed", "F minor")

    removed = owner.post(
        "/selection",
        headers=CSRF,
        data={
            "song_ulid": song_ulids,
            "tag_dim": "vibe",
            "tag_value": "road-trip",
            "tag_action": "remove",
        },
    )
    assert removed.status_code == 200
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM song_tags
            WHERE dim='vibe' AND value='road-trip'
            """
        ).fetchone()[0] == 0
    finally:
        connection.close()

    untagged = owner.get("/", params={"untagged": "true"})
    untagged_rows = untagged.text.split(
        '<div class="library" role="table" aria-label="Songs">', 1
    )[1].split('<div class="batchbar">', 1)[0]
    assert "Stayhere" not in untagged_rows
    assert 'aria-pressed="true"' in untagged.text

    source = OWNER_JS.read_text(encoding="utf-8")
    assert 'form.getAttribute("action")' in source
    assert "new FormData(form, event.submitter)" in source
    for contract in (
        '/^[1-9]$/.test(event.key)',
        'event.key.toLowerCase() === "t"',
        'event.key === " "',
        '["j", "k"].includes',
        'event.key === "/"',
    ):
        assert contract in source


def test_tag_vocabulary_rename_merge_and_delete_are_transactional(
    web: WebFixture, owner: TestClient
):
    connection = connect(web.db_path)
    try:
        song_id = int(
            connection.execute(
                "SELECT id FROM songs WHERE public_id=?",
                (web.song_ulids[0],),
            ).fetchone()[0]
        )
        connection.executemany(
            """
            INSERT INTO song_tags(
              song_id, dim, value, source, author, created_at
            ) VALUES(?, 'vibe', ?, 'human', ?, '2026-01-01')
            """,
            [
                (song_id, "old-name", "owner"),
                (song_id, "kept-name", "curator"),
            ],
        )
        connection.execute(
            """
            INSERT INTO reactions(
              bounce_ulid, song_id, actor, kind, dim, value, created_at
            ) VALUES(?, ?, 'owner', 'chip', 'vibe', 'old-name', '2026-01-01')
            """,
            (web.bounce_ulids[0], song_id),
        )
    finally:
        connection.close()

    page = owner.get("/tags")
    assert page.status_code == 200
    assert "old-name" in page.text
    merged = owner.post(
        "/tags/rewrite",
        headers=CSRF,
        data={
            "dim": "vibe",
            "value": "old-name",
            "replacement": "kept-name",
            "action": "rename",
        },
    )
    assert merged.status_code == 200
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM song_tags WHERE value='old-name'"
        ).fetchone()[0] == 0
        kept = connection.execute(
            """
            SELECT source, author FROM song_tags
            WHERE song_id=? AND dim='vibe' AND value='kept-name'
            """,
            (song_id,),
        ).fetchone()
        assert (kept["source"], kept["author"]) == ("human", "curator")
        assert connection.execute(
            """
            SELECT COUNT(*) FROM reactions
            WHERE value='kept-name' AND deleted_at IS NULL
            """
        ).fetchone()[0] == 1
    finally:
        connection.close()

    deleted = owner.post(
        "/tags/rewrite",
        headers=CSRF,
        data={
            "dim": "vibe",
            "value": "kept-name",
            "action": "delete",
        },
    )
    assert deleted.status_code == 200
    connection = connect(web.db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM song_tags WHERE value='kept-name'"
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM reactions
            WHERE value='kept-name' AND deleted_at IS NULL
            """
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_collections_create_reorder_filter_and_queue(
    web: WebFixture, owner: TestClient
):
    created = owner.post(
        "/selection",
        headers=CSRF,
        data={
            "song_ulid": web.song_ulids[:2],
            "collection_name": "Night drive",
            "action": "collection",
        },
        follow_redirects=False,
    )
    assert created.status_code == 200
    match = re.search(r"/collections/([A-Z0-9]+)", created.text)
    assert match is not None
    collection_ulid = match.group(1)
    collection_url = f"/collections/{collection_ulid}"
    detail = owner.get(collection_url)
    assert "Night drive" in detail.text
    assert "data-collection-list" in detail.text
    assert "data-collection-play" in detail.text

    reordered = owner.post(
        f"/collections/{collection_ulid}/order",
        headers=CSRF,
        data={
            "bounce_ulid": [web.bounce_ulids[1], SECOND_BOUNCE],
        },
    )
    assert reordered.status_code == 204
    connection = connect(web.db_path)
    try:
        order = [
            str(row["bounce_ulid"])
            for row in connection.execute(
                """
                SELECT ci.bounce_ulid
                FROM collection_items ci
                JOIN collections c ON c.id=ci.collection_id
                WHERE c.ulid=? ORDER BY ci.position
                """,
                (collection_ulid,),
            )
        ]
    finally:
        connection.close()
    assert order == [web.bounce_ulids[1], SECOND_BOUNCE]

    filtered = owner.post(
        "/collections",
        headers=CSRF,
        data={
            "source": "filter",
            "q": "Diamond",
            "name": "Only diamonds",
        },
        follow_redirects=False,
    )
    assert filtered.status_code == 303
    queued = owner.post(
        "/collections",
        headers=CSRF,
        data={
            "name": "Queue capture",
            "source": "queue",
            "bounce_ulid": web.bounce_ulids[:3],
        },
        follow_redirects=False,
    )
    assert queued.status_code == 303
    assert "ownerPlayer.queue.snapshot().playQueue" in OWNER_JS.read_text(
        encoding="utf-8"
    )

    owner_library = owner.get("/")
    assert "Night drive" in owner_library.text


def test_transport_glyph_label_and_row_state_follow_audio():
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const assert = require("assert");
        global.window = global;
        global.navigator = {{userAgent: "", platform: "", maxTouchPoints: 0}};
        global.sessionStorage = {{getItem() {{ return null; }}, setItem() {{}}}};
        const toggle = {{
          dataset: {{}},
          label: "",
          setAttribute(name, value) {{ if (name === "aria-label") this.label = value; }}
        }};
        const root = {{
          dataset: {{playerKey: "transport"}},
          querySelector() {{ return null; }},
          querySelectorAll(selector) {{
            return selector.includes("toggle") ? [toggle] : [];
          }},
          dispatchEvent() {{}}
        }};
        global.document = {{
          addEventListener() {{}},
          querySelectorAll() {{ return []; }},
          createElement() {{ return {{}}; }}
        }};
        const listeners = {{}};
        const audio = {{
          paused: true,
          ended: false,
          addEventListener(name, handler) {{ listeners[name] = handler; }},
          getAttribute() {{ return null; }}
        }};
        vm.runInThisContext(fs.readFileSync({str(PLAYBACK)!r}, "utf8"));
        const player = global.CratePlayback.attach({{root, audio}});
        player.currentTrack = {{bounce_ulid: "track-1"}};
        audio.paused = false;
        listeners.play();
        assert.strictEqual(toggle.label, "Pause");
        assert.strictEqual(toggle.dataset.playing, "true");
        assert.strictEqual(root.dataset.playing, "true");
        audio.paused = true;
        listeners.pause();
        assert.strictEqual(toggle.label, "Play");
        assert.strictEqual(toggle.dataset.playing, "false");
        assert.strictEqual(root.dataset.playing, "false");
        """
    )
    subprocess.run(["node", "-e", script], check=True, text=True)
    owner_template = Path(
        "cr8/web/owner/templates/owner/base.html"
    ).read_text(encoding="utf-8")
    assert 'data-player-action="toggle"' in owner_template
    assert 'aria-label="Play" data-playing="false"' in owner_template
    assert 'class="icon-play"' in owner_template
    assert 'class="icon-pause"' in owner_template
    assert "Play or pause" not in owner_template


def test_login_and_player_regression_contracts_are_visible_in_css():
    css = Path("cr8/web/common/static/owner.css").read_text(encoding="utf-8")
    assert "margin:min(7vh,56px) auto 0" in css
    auth_input = css[css.index(".auth-form input{"):css.index(
        ".auth-form input:focus-visible"
    )]
    assert "background:linear-gradient" in auth_input
    assert "box-shadow:inset 0 1px" in auth_input
    assert ".auth-form input:focus-visible" in css
    player = css[css.index(".player-dock{"):css.index(
        '.player-dock[data-empty="true"]'
    )]
    assert "height:72px" in player
    assert ".tag-row>.chips{min-width:0;flex:1}" in css


def test_narrow_owner_rows_expand_full_detail(
    web: WebFixture, owner: TestClient
):
    detail = owner.get(f"/songs/{web.song_ulids[0]}/row-detail")
    assert detail.status_code == 200
    assert 'class="row-detail-expand ' in detail.text
    assert "version rail" in detail.text
    for dim in ("status", "key", "vibe", "instr", "collab"):
        assert f'data-tag-dimension="{dim}"' in detail.text

    owner_source = OWNER_JS.read_text(encoding="utf-8")
    assert 'window.matchMedia("(max-width: 1099px)")' in owner_source
    assert '"/row-detail"' in owner_source
