from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
import subprocess
import textwrap

from fastapi.testclient import TestClient

from cr8.db import connect

from conftest import SECOND_BOUNCE, WebFixture


CSRF = {"X-CR8-Request": "1"}
PLAYBACK = Path("cr8/web/common/static/playback.js")


def test_shared_queue_state_shuffle_repeat_reorder_and_auto_advance():
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const assert = require("assert");
        global.window = global;
        const values = new Map();
        global.sessionStorage = {{
          getItem(key) {{ return values.has(key) ? values.get(key) : null; }},
          setItem(key, value) {{ values.set(key, String(value)); }}
        }};
        vm.runInThisContext(fs.readFileSync({str(PLAYBACK)!r}, "utf8"));

        const Queue = global.CratePlayback.Queue;
        const ordered = ["a", "b", "c", "d"];
        const shuffled = new Queue({{storageKey: "shuffle"}});
        shuffled.replace(ordered, null, {{
          shuffle: true,
          random: (() => {{
            const picks = [0.1, 0.7, 0.2];
            return () => picks.shift();
          }})()
        }});
        assert.deepStrictEqual(
          shuffled.playQueue.map((item) => item.id).sort(),
          ordered
        );
        assert.strictEqual(new Set(shuffled.playQueue.map((item) => item.id)).size, 4);

        const queue = new Queue({{storageKey: "session"}});
        queue.replace(ordered, "b");
        assert.strictEqual(queue.current().id, "b");
        assert.strictEqual(queue.advance().id, "c");
        queue.reorder(3, 0);
        assert.strictEqual(queue.current().id, "c");
        assert.deepStrictEqual(queue.playQueue.map((item) => item.id), ["d", "a", "b", "c"]);

        const restored = new Queue({{storageKey: "session"}});
        assert.deepStrictEqual(restored.playQueue.map((item) => item.id), ["d", "a", "b", "c"]);
        assert.strictEqual(restored.current().id, "c");
        assert.strictEqual(restored.advance(), null);
        restored.setRepeat("all");
        assert.strictEqual(restored.advance().id, "d");
        assert.strictEqual(restored.retreat().id, "c");
        restored.replace(ordered, null, {{mode: "dig"}});
        assert.strictEqual(new Queue({{storageKey: "session"}}).mode, "dig");
        restored.cursor = 1;
        restored.reshuffle(() => 0.25);
        assert.strictEqual(restored.mode, "dig");
        assert.strictEqual(restored.current().id, "b");
        restored.setShuffle(false);
        assert.deepStrictEqual(
          restored.playQueue.map((item) => item.id),
          ordered
        );
        assert.strictEqual(restored.current().id, "b");
        assert.strictEqual(restored.advance().id, "c");
        restored.remove("c");
        assert.deepStrictEqual(
          new Queue({{storageKey: "session"}}).playQueue.map((item) => item.id),
          ["a", "b", "d"]
        );
        """
    )
    subprocess.run(["node", "-e", script], check=True, text=True)


def test_queue_dom_is_windowed_to_twenty_rows():
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const assert = require("assert");
        global.window = global;
        global.navigator = {{userAgent: "", platform: "", maxTouchPoints: 0}};
        const values = new Map();
        global.sessionStorage = {{
          getItem(key) {{ return values.has(key) ? values.get(key) : null; }},
          setItem(key, value) {{ values.set(key, String(value)); }}
        }};
        class Element {{
          constructor() {{
            this.children = [];
            this.dataset = {{}};
            this.className = "";
            this.textContent = "";
            this.classList = {{add() {{}}}};
          }}
          append(...items) {{ this.children.push(...items); }}
          appendChild(item) {{ this.children.push(item); }}
          replaceChildren(...items) {{ this.children = items; }}
          setAttribute() {{}}
        }}
        const list = new Element();
        const count = new Element();
        const root = {{
          dataset: {{playerKey: "window-test"}},
          querySelector(selector) {{
            if (selector === "[data-queue-view]") return list;
            if (selector === "[data-queue-count]") return count;
            return null;
          }},
          querySelectorAll() {{ return []; }},
          dispatchEvent() {{}}
        }};
        global.document = {{
          addEventListener() {{}},
          createElement() {{ return new Element(); }}
        }};
        const audio = {{
          addEventListener() {{}},
          getAttribute() {{ return null; }}
        }};
        vm.runInThisContext(fs.readFileSync({str(PLAYBACK)!r}, "utf8"));
        const player = global.CratePlayback.attach({{root, audio}});
        player.setQueue(
          Array.from({{length: 472}}, (_, index) => ({{
            id: "track-" + index,
            title: "Track " + index
          }})),
          236
        );
        assert.strictEqual(list.children.length, 20);
        assert.strictEqual(
          list.children.filter((row) =>
            row.children.some((child) => child.className === "queue-remove")
          ).length,
          20
        );
        assert.strictEqual(count.textContent, "472 queued");
        assert(Number(list.dataset.queueOffset) > 0);
        """
    )
    subprocess.run(["node", "-e", script], check=True, text=True)


def test_app_loads_the_shared_preserved_player(
    web: WebFixture, owner: TestClient
):
    owner_page = owner.get("/")
    assert owner_page.status_code == 200
    assert owner_page.text.index("/static/playback.js") < owner_page.text.index(
        "/static/owner.js"
    )
    assert (
        'id="crate-player" class="player-dock" data-empty="true" '
        'data-player-key="owner" hx-preserve'
    ) in owner_page.text
    assert 'data-player-action="previous"' in owner_page.text
    assert 'data-player-action="next"' in owner_page.text

def test_shared_engine_owns_media_session_and_plain_audio_path():
    source = PLAYBACK.read_text(encoding="utf-8")
    assert "previoustrack" in source
    assert "nexttrack" in source
    assert 'addEventListener("ended"' in source
    assert "queue.advance()" in source
    assert "new global.MediaMetadata" in source
    assert "global.WaveSurfer.create" in source
    assert "createMediaElementSource" in source
    assert "isIOS()" in source
    assert "<audio" in Path(
        "cr8/web/owner/templates/owner/base.html"
    ).read_text(encoding="utf-8")


def test_queue_view_renders_with_sortable_remove_controls(
    web: WebFixture, owner: TestClient
):
    owner_page = owner.get("/")
    assert 'data-queue-view aria-label="Playback queue"' in owner_page.text
    assert owner_page.text.index("sortable-1.15.6.min.js") < owner_page.text.index(
        "/static/playback.js"
    )

    source = PLAYBACK.read_text(encoding="utf-8")
    assert "global.Sortable.create" in source
    assert 'draggable: ".queue-item"' in source
    assert "player.queue.reorder(" in source
    assert "offset + event.oldIndex" in source
    assert "offset + event.newIndex" in source
    assert "removeQueueItem" in source
    assert "remove.dataset.queueRemove = item.id" in source


def test_every_playable_library_row_is_a_real_button(
    web: WebFixture, owner: TestClient
):
    owner_page = owner.get("/")
    assert owner_page.text.count('<button class="song-row play-row"') == len(
        web.bounce_ulids
    )
    assert '<div class="song-row' not in owner_page.text
    assert '<article class="song-row' not in owner_page.text

    owner_rows = Path(
        "cr8/web/owner/templates/owner/fragments/library_rows.html"
    ).read_text(encoding="utf-8")
    assert '<div class="song-row' not in owner_rows
    assert '<button class="song-row play-row"' in owner_rows


def test_dig_prefers_unplayed_then_least_played_across_library(
    web: WebFixture, owner: TestClient
):
    scope = [SECOND_BOUNCE, *web.bounce_ulids[1:]]
    assert {
        item["id"] for item in owner.get("/api/dig").json()["tracks"]
    } == set(scope)
    initial = owner.get("/api/dig").json()["tracks"]
    assert all(item["reason"].startswith("never played") for item in initial)

    for bounce_ulid, plays in ((scope[0], 2), (scope[1], 1)):
        for _ in range(plays):
            response = owner.post(
                f"/progress/{bounce_ulid}",
                headers=CSRF,
                data={
                    "state": "unheard",
                    "heard_s": "1",
                    "started": "true",
                },
            )
            assert response.status_code == 204

    updated = owner.get("/api/dig").json()["tracks"]
    ordered = [item["id"] for item in updated]
    assert set(ordered[:2]) == set(scope[2:])
    assert ordered[-2:] == [scope[1], scope[0]]
    reasons = {item["id"]: item["reason"] for item in updated}
    assert reasons[scope[2]].startswith("never played")
    assert reasons[scope[0]] == "no vibe yet"

    connection = connect(web.db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM playback_events"
        ).fetchone()[0] == 3
    finally:
        connection.close()


def test_dig_summary_and_reason_strata_are_crate_scoped(
    web: WebFixture, owner: TestClient
):
    old_play = "2024-03-12T10:00:00+00:00"
    today = datetime.now(UTC).replace(microsecond=0).isoformat()
    dormant_bounce = web.bounce_ulids[1]
    never_played_bounce = web.bounce_ulids[2]
    dug_today_bounce = web.bounce_ulids[3]

    connection = connect(web.db_path)
    try:
        tagged_song_ids = [
            int(
                connection.execute(
                    "SELECT id FROM songs WHERE public_id=?", (song_ulid,)
                ).fetchone()[0]
            )
            for song_ulid in web.song_ulids[1:3]
        ]
        connection.executemany(
            """
            INSERT INTO song_tags(
              song_id, dim, value, source, author, created_at
            ) VALUES(?, 'vibe', 'dreamy', 'human', 'owner', ?)
            """,
            [(song_id, today) for song_id in tagged_song_ids],
        )
        connection.executemany(
            """
            INSERT INTO playback_events(
              share_id, bounce_ulid, actor, started_at
            ) VALUES(?, ?, ?, ?)
            """,
            [
                (0, dormant_bounce, "hareesh", old_play),
                (0, dug_today_bounce, "share:1:guest", today),
                (0, dug_today_bounce, "hareesh", today),
                # Opening an older version still means its song has been
                # opened, while today's dent counts the distinct bounce.
                (0, web.bounce_ulids[0], "share:1:guest", today),
                # A play outside the owner group changes neither group-wide
                # never-played nor today's dent.
                (9, SECOND_BOUNCE, "share:9:guest", today),
            ],
        )
    finally:
        connection.close()

    payload = owner.get("/api/dig").json()
    assert payload["dig_summary"] == {
        "total": 4,
        "never_played": 1,
        "showing": 4,
        "dug_today": 2,
    }

    queue = {item["id"]: item for item in payload["tracks"]}
    assert queue[SECOND_BOUNCE]["dig_reason"] == "untagged"
    assert queue[SECOND_BOUNCE]["dig_reason_label"] == "NO VIBE YET"
    assert queue[never_played_bounce]["dig_reason"] == "never_played"
    assert queue[never_played_bounce]["dig_reason_label"] == "NEVER PLAYED"
    assert queue[dormant_bounce]["dig_reason"] == "dormant"
    assert queue[dormant_bounce]["dig_reason_label"] == "LAST HEARD MAR 2024"

    details = {item["bounce_ulid"]: item for item in payload["details"]}
    assert details[SECOND_BOUNCE]["dig_reason"] == "untagged"
    assert details[never_played_bounce]["dig_reason"] == "never_played"
    assert details[dormant_bounce]["dig_reason"] == "dormant"

    filtered = owner.get("/api/dig", params={"q": "Diamond"}).json()
    assert filtered["dig_summary"] == {
        "total": 4,
        "never_played": 1,
        "showing": 1,
        "dug_today": 2,
    }

def test_shuffle_everything_shuffle_this_and_dig_gestures_render(
    web: WebFixture, owner: TestClient
):
    library = owner.get("/")
    assert "Shuffle everything" in library.text
    assert ">Dig<" in library.text
    assert "Dig untagged" in library.text
    assert f"shuffle these {len(web.song_ulids)}" in library.text
    assert library.text.count("data-shuffle-all-seed") == 1
    assert library.text.count("data-dig-seed") == 1
    assert library.text.count("data-dig-untagged-seed") == 1
    template = Path("cr8/web/owner/templates/owner/library.html").read_text(
        encoding="utf-8"
    )
    assert "song.reason or ''" in template
    assert "song.dig_reason or ''" not in template
    assert len(owner.get("/api/library-queue").json()["tracks"]) == len(
        web.bounce_ulids
    )
    key_queue = owner.get(
        "/api/library-queue", params={"key": "10A"}
    ).json()["tracks"]
    assert {item["title"] for item in key_queue} == {
        "Diamond",
        "Pensive Arpey",
    }
    filtered = owner.get("/", params={"unheard": "true"})
    assert f"shuffle these {len(web.bounce_ulids)}" in filtered.text


def test_owner_library_composes_key_heart_sort_and_window_filters(
    web: WebFixture, owner: TestClient
):
    owner.post(
        f"/reactions/{web.bounce_ulids[1]}/heart",
        headers=CSRF,
    )
    filtered = owner.get(
        "/",
        params={
            "key": "10A",
            "hearted": "true",
            "sort": "shortest",
        },
    )
    rows = filtered.text.split(
        '<div class="library" role="table" aria-label="Songs">', 1
    )[1].split(
        '<div class="batchbar">', 1
    )[0]
    assert 'data-track-title="Diamond"' in rows
    assert 'data-track-title="Pensive Arpey"' not in rows
    for label in ("hearted by me", "10A", "shortest"):
        assert re.search(
            rf'aria-pressed="true"[^>]*>\s*<span>{re.escape(label)}</span>',
            filtered.text,
        )
    assert "shuffle these 1" in filtered.text


def test_library_windows_forty_eight_rows_and_appends_the_next_page(
    web: WebFixture,
    owner: TestClient,
):
    connection = connect(web.db_path)
    try:
        for index in range(125):
            song_ulid = f"01SONG{index:020d}"
            bounce_ulid = f"01BAND{index:020d}"
            connection.execute(
                """
                INSERT INTO songs(
                  slug, title, status, public_id, first_date, last_date,
                  key_canon, key_camelot, bpm
                ) VALUES(?, ?, 'demo', ?, ?, ?, 'C minor', '5A', 120)
                """,
                (
                    f"window-{index}",
                    f"Window {index:02d}",
                    song_ulid,
                    f"2026-06-{(index % 28) + 1:02d}",
                    f"2026-06-{(index % 28) + 1:02d}",
                ),
            )
            song_id = int(
                connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO bounces(
                  public_id, song_id, source_stem, bounce_date, version
                ) VALUES(?, ?, ?, '2026-06-01', 1)
                """,
                (bounce_ulid, song_id, f"window-{index}"),
            )
            bounce_id = int(
                connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO files(
                  relpath, layer, ext, duration_s, bounce_id, parse_status
                ) VALUES(?, 'curated', '.wav', 60, ?, 'parsed')
                """,
                (f"Window-{index}.wav", bounce_id),
            )
            connection.execute(
                """
                INSERT INTO mirror_files(bounce_id, mirror_relpath)
                VALUES(?, ?)
                """,
                (bounce_id, f"tracks/{bounce_ulid}.mp3"),
            )
    finally:
        connection.close()

    owner_first = owner.get("/")
    assert owner_first.text.count('<button class="song-row play-row"') == 48
    assert owner_first.text.count('class="row-tail"') == 1
    assert 'class="row-versions"' not in owner_first.text
    assert len(owner_first.content) < 120_000
    owner_more = owner.get(
        "/",
        params={"sort": "newest", "offset": 48},
        headers={"HX-Request": "true"},
    )
    assert owner_more.status_code == 200
    assert owner_more.text.count('<button class="song-row play-row"') == 48
    owner_last = owner.get(
        "/",
        params={"sort": "newest", "offset": 96},
        headers={"HX-Request": "true"},
    )
    assert owner_last.text.count('<button class="song-row play-row"') == 33
    assert "<!doctype html>" not in owner_more.text


def test_tag_chips_become_frequency_sorted_shuffle_filters(
    web: WebFixture, owner: TestClient
):
    scope = web.bounce_ulids[:3]
    for bounce_ulid, value in (
        (scope[0], "dreamy"),
        (scope[1], "dreamy"),
        (scope[0], "heavy"),
        (web.bounce_ulids[3], "dreamy"),
    ):
        tagged = owner.post(
            f"/reactions/{bounce_ulid}/chip",
            headers=CSRF,
            data={"value": value},
        )
        assert tagged.status_code == 200
        assert "data-tag-filter" in tagged.text

    owner_queue = owner.get(
        "/api/tag-queue", params={"dim": "vibe", "value": "dreamy"}
    )
    assert owner_queue.status_code == 200
    assert {item["title"] for item in owner_queue.json()["tracks"]} == {
        "Stayhere",
        "Diamond",
        "Pensive Arpey",
    }

    owner_library = owner.get("/")
    assert 'data-filter-dim="vibe"' in owner_library.text
    assert "/api/tag-queue?" in Path(
        "cr8/web/common/static/owner.js"
    ).read_text(encoding="utf-8")
