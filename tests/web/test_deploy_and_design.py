from __future__ import annotations

import json
from pathlib import Path
import plistlib


def test_owner_plist_is_the_only_web_process_and_contains_no_secrets():
    owner = plistlib.loads(
        Path("ops/launchd/com.cr8.owner.plist").read_bytes()
    )
    assert owner["ProgramArguments"][1] == "cr8.web.owner.app:create_app"
    port_index = owner["ProgramArguments"].index("--port")
    assert owner["ProgramArguments"][port_index + 1] == "8080"
    assert "--no-server-header" in owner["ProgramArguments"]
    assert not Path("ops/launchd/com.cr8.guest.plist").exists()
    environment = owner["EnvironmentVariables"]
    assert "CR8_SECRET_FILE" in environment
    assert all(
        "KEY" not in key and "PASSWORD" not in key for key in environment
    )


def test_deploy_docs_document_one_app_on_one_port():
    docs = Path("ops/README.md").read_text(encoding="utf-8")
    # Public face is Next on 3100; API on loopback 8080. No guest app, no 8081.
    assert "127.0.0.1:3100" in docs and "127.0.0.1:8080" in docs
    assert "CR8_PUBLIC_ORIGIN" in docs or "CR8_DEPLOY_REMOTE" in docs
    assert "8081" not in docs
    assert "com.cr8.guest" not in docs
    assert "com.hareesh" not in docs
    scripts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("scripts").glob("*band-app*")
    )
    assert "tailscale " not in scripts
    assert "launchctl " not in scripts



def test_authenticated_app_javascript_budget_and_manifest():
    static = Path("cr8/web/common/static")
    app_files = (
        static / "vendor/htmx-2.0.9.min.js",
        static / "vendor/wavesurfer-7.11.min.js",
        static / "vendor/sortable-1.15.6.min.js",
        static / "playback.js",
        static / "owner.js",
    )
    assert sum(path.stat().st_size for path in app_files) < 300_000
    manifest = json.loads((static / "manifest.webmanifest").read_text())
    assert manifest["display"] == "browser"
    app_source = "\n".join(
        path.read_text()
        for path in (static / "playback.js", static / "owner.js")
    )
    assert "history.pushState" in app_source
    assert "AudioContext" in app_source
    assert "isIOS()" in app_source
    assert "mediaSession" in app_source


def test_ratified_tokens_and_reference_moves_are_present():
    owner = Path("cr8/web/common/static/owner.css").read_text()
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("cr8/web").rglob("*.html")
    )
    for token in (
        "#191919",
        "#242424",
        "#101010",
        "oklch(0.72 0.15 25)",
        "oklch(0.78 0.13 195)",
        "oklch(0.86 0.16 115)",
        "oklch(0.60 0.22 27)",
        "cubic-bezier(.32,0,.16,1)",
    ):
        assert token in owner
    assert "Instrument Sans" in owner
    assert "IBM Plex Mono" in owner
    assert "spec-grid" in templates
    assert "version-dot" in templates
    assert "unheard" in templates
    assert "stamp-hearted" not in templates
    assert "three-cards" not in owner
    assert "glass" not in owner.casefold()
    assert "#000" not in owner


def test_owner_library_has_a_real_three_region_desktop_shell():
    templates = Path("cr8/web/owner/templates/owner")
    base = (templates / "base.html").read_text(encoding="utf-8")
    library = (templates / "library.html").read_text(encoding="utf-8")
    rows = (templates / "fragments/library_rows.html").read_text(encoding="utf-8")
    css = Path("cr8/web/common/static/owner.css").read_text(encoding="utf-8")

    assert 'class="library-workspace"' in library
    assert 'class="filter-rail"' in library
    assert 'class="cr8-pane"' in library
    assert 'class="detail-panel"' in library
    assert "grid-template-columns:220px minmax(460px,1fr) 380px" in css
    assert ".main-library{max-width:none" in css
    assert "@media(max-width:1099px)" in css
    assert "@media(max-width:759px)" in css
    assert "height:72px" in css
    assert 'href="/tags"' in base
    assert 'href="/collections"' in base
    assert 'href="/members"' in base
    assert 'href="/activity"' in base
    assert 'href="/shares"' not in base
    assert '<article class="song-stack' in rows
    assert '<div class="song-stack' not in rows


def test_owner_motion_and_frequency_rules_are_wired():
    static = Path("cr8/web/common/static")
    css = (static / "owner.css").read_text(encoding="utf-8")
    owner_js = (static / "owner.js").read_text(encoding="utf-8")
    playback_js = (static / "playback.js").read_text(encoding="utf-8")
    base = Path("cr8/web/owner/templates/owner/base.html").read_text(
        encoding="utf-8"
    )
    compact_css = "".join(css.split())
    compact_js = "".join(owner_js.split())

    assert 'data-playing="false"' in base
    assert 'class="icon-play"' in base
    assert 'class="icon-pause"' in base
    assert "path.setAttribute" not in playback_js
    assert "--ease-icon-swap:cubic-bezier(0.2,0,0,1)" in compact_css
    icon_rules = compact_css[
        compact_css.index(".icon-swap{"):compact_css.index(".player-title{")
    ]
    assert "transition-" not in icon_rules
    assert "scale:0.25" in compact_css
    assert "filter:blur(4px)" in compact_css
    assert ".player-play:active{transform:none}" in compact_css
    assert "place-items:center;transition-duration:0ms" in compact_css

    assert "button:active,a:active{transform:scale(.97)}" in compact_css
    assert "animation:chip-fill" not in css
    assert "@keyframeschip-fill" not in compact_css
    assert ".chip[data-tag-filter],.tag-toggle{transition-duration:0ms}" in compact_css
    assert ".chip[data-tag-filter]:active,.tag-toggle:active{transform:none}" in compact_css
    assert ".heart.js-just-toggled{animation:heart-pop" in compact_css
    assert "pulseOnce(heart)" in compact_js

    assert (
        ".song-line{"
        "display:grid;"
    ) in compact_css
    assert "transition-property:background-color,box-shadow" in compact_css
    assert "transition-duration:var(--dur)" in compact_css
    assert (
        ".song-stack.is-instant-selection"
        ".song-line,"
        ".song-stack.is-instant-selection"
        ".song-row,"
        ".song-stack.is-instant-selection"
        ".row-detail{transition-duration:0ms}"
    ) in compact_css
    assert (
        "updateDetailFromRow(next,{instant:true,requestDetail:true})"
        in compact_js
    )

    assert (
        ".detail-preview-copy,.player-copy{"
        "transition-property:opacity;"
        "transition-duration:120ms;"
    ) in compact_css
    assert (
        ".detail-preview-copy.js-updating,.player-copy.js-updating"
        "{opacity:0.35}"
    ) in compact_css
    assert "withFade(document.querySelector(\".player-copy\")" in owner_js

    assert 'data-open="false" hidden' in base
    assert "transform-origin:bottomright" in compact_css
    assert "transition-property:opacity,scale" in compact_css
    assert "transition-duration:220ms" in compact_css
    assert '.player-extras[data-open="false"]{opacity:0;scale:0.95' in compact_css
    assert 'event.propertyName!=="opacity"' in compact_js

    assert "transition:none!important" not in compact_css
    assert "animation-duration:0.01ms!important" in compact_css
    assert "animation-iteration-count:1!important" in compact_css
    assert (
        "transition-property:opacity,color,background-color!important"
        in compact_css
    )
    assert "is-instant-toggle" in owner_js
