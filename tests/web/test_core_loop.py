from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

from fastapi.testclient import TestClient

from cr8.db import connect
from conftest import WebFixture


PLAYBACK = Path("cr8/web/common/static/playback.js")
CSRF = {"X-CR8-Request": "1"}


def test_out_of_order_track_fetch_cannot_retarget_the_player():
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const assert = require("assert");
        global.window = global;
        global.navigator = {{userAgent: "", platform: "", maxTouchPoints: 0}};
        global.sessionStorage = {{getItem() {{ return null; }}, setItem() {{}}}};
        global.document = {{
          addEventListener() {{}},
          querySelectorAll() {{ return []; }},
          createElement() {{ return {{}}; }}
        }};
        const pending = new Map();
        global.fetch = (url) => new Promise((resolve) => pending.set(url, resolve));
        const root = {{
          dataset: {{playerKey: "fetch-order"}},
          querySelector() {{ return null; }},
          querySelectorAll() {{ return []; }},
          dispatchEvent() {{}}
        }};
        const audio = {{
          paused: true,
          ended: false,
          source: "",
          addEventListener() {{}},
          getAttribute(name) {{ return name === "src" ? this.source : null; }},
          set src(value) {{ this.source = value; }},
          get src() {{ return this.source; }}
        }};
        vm.runInThisContext(fs.readFileSync({str(PLAYBACK)!r}, "utf8"));
        const rendered = [];
        const tagged = [];
        const player = global.CratePlayback.attach({{
          root,
          audio,
          render(track) {{ rendered.push(track.bounce_ulid); }},
          tags(track) {{ tagged.push(track.bounce_ulid); }}
        }});
        player.setQueue([
          {{id: "a", trackUrl: "/a", audioUrl: "/a.mp3"}},
          {{id: "b", trackUrl: "/b", audioUrl: "/b.mp3"}}
        ], "a");
        const first = player.playCurrent({{autoplay: false}});
        player.queue.advance();
        const second = player.playCurrent({{autoplay: false}});

        pending.get("/b")({{
          ok: true,
          async json() {{ return {{bounce_ulid: "b", title: "B"}}; }}
        }});
        second.then(() => {{
          pending.get("/a")({{
            ok: true,
            async json() {{ return {{bounce_ulid: "a", title: "A"}}; }}
          }});
          return first;
        }}).then((stale) => {{
          assert.strictEqual(stale, null);
          assert.strictEqual(player.currentTrack.bounce_ulid, "b");
          assert.deepStrictEqual(rendered, ["b"]);
          assert.deepStrictEqual(tagged, ["b"]);
        }}).catch((error) => {{
          process.nextTick(() => {{ throw error; }});
          process.exitCode = 1;
        }});
        """
    )
    subprocess.run(["node", "-e", script], check=True, text=True)


def test_transport_native_events_drive_state_row_and_live_announcement():
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const assert = require("assert");
        global.window = global;
        global.navigator = {{userAgent: "", platform: "", maxTouchPoints: 0}};
        global.sessionStorage = {{getItem() {{ return null; }}, setItem() {{}}}};
        const live = {{textContent: ""}};
        const toggle = {{
          dataset: {{}},
          label: "",
          setAttribute(name, value) {{ if (name === "aria-label") this.label = value; }}
        }};
        const rowClass = new Set();
        const stack = {{
          classList: {{
            add(value) {{ rowClass.add(value); }},
            remove(value) {{ rowClass.delete(value); }}
          }}
        }};
        const row = {{
          dataset: {{trackId: "track-1", songUlid: "song-1"}},
          current: null,
          matches(selector) {{ return selector === ".play-row"; }},
          closest() {{ return stack; }},
          setAttribute(name, value) {{ if (name === "aria-current") this.current = value; }},
          removeAttribute(name) {{ if (name === "aria-current") this.current = null; }}
        }};
        const root = {{
          dataset: {{playerKey: "transport", state: "paused"}},
          querySelector(selector) {{
            if (selector === "[data-player-live]") return live;
            return null;
          }},
          querySelectorAll(selector) {{
            return selector.includes("toggle") ? [toggle] : [];
          }},
          dispatchEvent() {{}}
        }};
        global.document = {{
          addEventListener() {{}},
          querySelectorAll(selector) {{
            if (selector === "[data-track-id]") return [row];
            if (selector === ".play-row[aria-current='true']") {{
              return row.current ? [row] : [];
            }}
            if (selector.includes(".is-playing")) return rowClass.has("is-playing") ? [stack] : [];
            return [];
          }},
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
        player.currentTrack = {{
          bounce_ulid: "track-1",
          song_ulid: "song-1",
          title: "Night Drive",
          version_label: "v3"
        }};

        audio.paused = false;
        listeners.play();
        assert.strictEqual(root.dataset.state, "playing");
        assert.strictEqual(toggle.label, "Pause");
        assert.strictEqual(row.current, "true");
        assert(rowClass.has("is-playing"));

        listeners.waiting();
        assert.strictEqual(root.dataset.state, "loading");
        listeners.playing();
        assert.strictEqual(root.dataset.state, "playing");
        player.announceTrack();
        assert.strictEqual(live.textContent, "Night Drive · v3");

        listeners.error();
        assert.strictEqual(root.dataset.state, "error");
        assert.strictEqual(live.textContent, "Playback error · Night Drive");
        audio.paused = true;
        listeners.pause();
        assert.strictEqual(root.dataset.state, "paused");
        assert.strictEqual(toggle.label, "Play");
        """
    )
    subprocess.run(["node", "-e", script], check=True, text=True)


def test_transport_template_keeps_both_instant_glyphs_and_live_region():
    base = Path(
        "cr8/web/owner/templates/owner/base.html"
    ).read_text(encoding="utf-8")
    rows = Path(
        "cr8/web/owner/templates/owner/fragments/library_rows.html"
    ).read_text(encoding="utf-8")
    library = Path(
        "cr8/web/owner/templates/owner/library.html"
    ).read_text(encoding="utf-8")
    css = Path("cr8/web/common/static/owner.css").read_text(encoding="utf-8")

    assert 'class="icon-play"' in base
    assert 'class="icon-pause"' in base
    assert "data-player-live" in base
    assert 'data-state="paused"' in base
    assert "display:none" not in css[css.index(".icon-swap"):css.index(".player-title")]
    assert "transition-property:opacity,scale,filter" not in css
    assert "margin-left:2px" in css
    assert "data-row-play" in library
    assert "data-row-play" not in rows


def test_batch_and_metadata_writes_return_fragments_without_navigation(
    web: WebFixture, owner: TestClient
):
    batch = owner.post(
        "/selection",
        headers=CSRF,
        data={
            "song_ulid": web.song_ulids[:2],
            "tag_dim": "vibe",
            "tag_value": "continuous",
        },
        follow_redirects=False,
    )
    assert batch.status_code == 200
    assert "<html" not in batch.text.casefold()
    assert 'hx-swap-oob="innerHTML"' in batch.text
    assert 'hx-swap-oob="outerHTML:' in batch.text

    edited = owner.post(
        f"/songs/{web.song_ulids[0]}/edit",
        headers=CSRF,
        data={"status": "mixed", "collab": "ej"},
        follow_redirects=False,
    )
    assert edited.status_code == 200
    assert "<html" not in edited.text.casefold()
    assert "Metadata committed." in edited.text

    owner_js = Path(
        "cr8/web/common/static/owner.js"
    ).read_text(encoding="utf-8")
    base = Path(
        "cr8/web/owner/templates/owner/base.html"
    ).read_text(encoding="utf-8")
    library = Path(
        "cr8/web/owner/templates/owner/library.html"
    ).read_text(encoding="utf-8")
    more = Path(
        "cr8/web/owner/templates/owner/fragments/more.html"
    ).read_text(encoding="utf-8")
    assert "document.write" not in owner_js
    assert "location.assign" not in owner_js
    assert "historyCacheSize = 0" in owner_js
    assert "hx-preserve" in base
    assert 'hx-post="/selection"' in library
    assert f'hx-post="/songs/{{{{ track.song_ulid }}}}/edit"' in more


def test_song_scoped_tag_toggle_round_trips_filters_and_audits(
    web: WebFixture, owner: TestClient
):
    song_ulid = web.song_ulids[0]
    added = owner.post(
        f"/songs/{song_ulid}/tags/toggle",
        headers=CSRF,
        data={"dim": "vibe", "value": "midnight"},
    )
    assert added.status_code == 200
    assert 'value="midnight"' in added.text
    assert 'aria-pressed="true"' in added.text
    filtered = owner.get(
        "/", params={"dim": "vibe", "value": "midnight"}
    )
    assert f'id="song-stack-{song_ulid}"' in filtered.text

    same_song_other_version = owner.get(
        f"/songs/{song_ulid}/tag-panel"
    )
    assert 'value="midnight"' in same_song_other_version.text
    assert 'aria-pressed="true"' in same_song_other_version.text

    removed = owner.post(
        f"/songs/{song_ulid}/tags/toggle",
        headers=CSRF,
        data={"dim": "vibe", "value": "midnight"},
    )
    assert removed.status_code == 200
    assert 'value="midnight"' not in removed.text
    filtered = owner.get(
        "/", params={"dim": "vibe", "value": "midnight"}
    )
    assert f'id="song-stack-{song_ulid}"' not in filtered.text

    connection = connect(web.db_path)
    try:
        song_id = int(
            connection.execute(
                "SELECT id FROM songs WHERE public_id=?", (song_ulid,)
            ).fetchone()[0]
        )
        assert connection.execute(
            """
            SELECT COUNT(*) FROM song_tags
            WHERE song_id=? AND dim='vibe' AND value='midnight'
            """,
            (song_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM reactions
            WHERE song_id=? AND kind='chip'
              AND actor LIKE 'hareesh:audit:%'
            """,
            (song_id,),
        ).fetchone()[0] == 2
        schema = str(
            connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type='table' AND name='song_tags'
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert "'use'" in schema
    assert "'problem'" in schema

    owner_js = Path(
        "cr8/web/common/static/owner.js"
    ).read_text(encoding="utf-8")
    assert 'chip.getAttribute("aria-pressed") !== "true"' not in owner_js


def test_cursor_detail_fragment_is_complete_and_creates_first_vibe(
    web: WebFixture, owner: TestClient
):
    song_ulid = web.song_ulids[1]
    detail = owner.get(f"/fragments/detail/{song_ulid}")
    assert detail.status_code == 200
    assert f'data-song-ulid="{song_ulid}"' in detail.text
    assert "Diamond" in detail.text
    assert 'class="rail detail-version-rail"' in detail.text
    assert "versions · 1" in detail.text
    assert "song-stems" in detail.text
    for dim in ("vibe", "instr", "collab", "status", "keeper", "key"):
        assert f'data-tag-dimension="{dim}"' in detail.text
    assert "hx-post" in detail.text
    assert "data-tag-input" in detail.text

    created = owner.post(
        f"/songs/{song_ulid}/tags/toggle",
        headers=CSRF,
        data={"dim": "vibe", "value": "first-light"},
    )
    assert created.status_code == 200
    assert 'value="first-light"' in created.text
    assert 'aria-pressed="true"' in created.text
    library = owner.get("/")
    assert "first-light" in library.text
    assert 'class="filter-count">1<' in library.text

    rows = Path(
        "cr8/web/owner/templates/owner/fragments/library_rows.html"
    ).read_text(encoding="utf-8")
    queries = Path(
        "cr8/web/common/queries.py"
    ).read_text(encoding="utf-8")
    assert 'hx-get="/fragments/detail/{{ song.song_ulid }}"' not in rows
    assert 'hx-target="#detail-panel"' not in rows
    assert "data-detail-era" not in rows
    assert "data-detail-versions" not in rows
    assert "focus delay:120ms" not in rows
    owner_js = Path(
        "cr8/web/common/static/owner.js"
    ).read_text(encoding="utf-8")
    assert "if (document.activeElement !== row)" in owner_js
    assert '"/fragments/detail/" + encodeURIComponent(songUlid)' in owner_js
    assert '{target: "#detail-panel", swap: "outerHTML"}' in owner_js
    assert "detailTags.innerHTML = tags.innerHTML" not in Path(
        "cr8/web/common/static/owner.js"
    ).read_text(encoding="utf-8")
    assert "DEFAULT_CHIPS" not in queries


def test_row_cursor_inspects_and_explicit_activation_plays():
    rows = Path(
        "cr8/web/owner/templates/owner/fragments/library_rows.html"
    ).read_text(encoding="utf-8")
    library = Path(
        "cr8/web/owner/templates/owner/library.html"
    ).read_text(encoding="utf-8")
    owner_js = Path(
        "cr8/web/common/static/owner.js"
    ).read_text(encoding="utf-8")
    playback_js = PLAYBACK.read_text(encoding="utf-8")
    css = Path(
        "cr8/web/common/static/owner.css"
    ).read_text(encoding="utf-8")
    compact = "".join(css.split())

    assert "data-cursor-row" not in rows
    assert 'tabindex="{{ 0 if cursor else -1 }}"' in rows
    assert 'class="select-song"' in rows and 'tabindex="-1"' in rows
    assert "row-tail" not in rows
    assert "data-row-play" in library
    assert "row-heart" in library
    assert "data-row-tag" in library
    assert "data-row-queue-add" in library
    assert "row-overflow" in library
    assert 'class="row-versions"' not in rows
    assert "data-track-url" not in rows
    assert "data-song-ulid" not in rows

    assert 'document.addEventListener("dblclick"' in owner_js
    assert 'event.key === "Enter"' in owner_js
    assert "playCursorRow(cursorRow)" in owner_js
    assert '"/api/tracks/" + encodeURIComponent(id)' in playback_js
    assert "attachRowTail(row)" in owner_js

    assert "height:44px" in compact
    assert "content-visibility:auto" in compact
    assert "contain-intrinsic-size:044px" in compact
    assert "scroll-margin-block:88px104px" in compact
    assert "background:rgba(255,255,255,.045)" in compact
    assert "background:rgba(255,255,255,.92)" in compact
    assert "box-shadow:inset0002pxrgba(255,255,255,.92)" in compact
