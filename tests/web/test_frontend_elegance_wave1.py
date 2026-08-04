from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_cued_transport_starts_the_advertised_track_and_exports_truth() -> None:
    player = _read("web/components/PlayerProvider.tsx")
    state_type = _between(player, "type PlayerState = {", "};")
    step = _between(player, 'case "step": {', "\n    }\n  }\n}")
    toggle = _between(player, "const toggle = useCallback", "const seek = useCallback")

    assert "cued: boolean;" in state_type
    assert step.index("if (state.cued) return {...state, cued: false};") < step.index(
        "if (action.direction === 1)"
    )
    assert "if (snapshot.cued)" in toggle
    assert "audio.src.endsWith" not in toggle
    assert "cued: snapshot.cued" in player


def test_title_link_does_not_bubble_into_desktop_queue_replacement() -> None:
    row = _read("web/components/LibraryRow.tsx")
    css = _read("web/app/globals.css")
    title = _between(
        row,
        '<span className={`name${track.vibe_tags?.length ? " has-vibes" : ""}`}>',
        '<span className="row-meta num">',
    )
    mobile_link = _between(css, "  .row .name-link {", "  }")

    assert "onClick={compact ?" in row
    assert title.count("<Link") == 1
    assert "{compact ? (" not in title
    assert "onClick={(event) => event.stopPropagation()}" in title
    assert "pointer-events: none;" not in mobile_link


def test_letter_first_render_comes_from_the_server_cookie() -> None:
    layout = _read("web/app/layout.tsx")
    page = _read("web/app/page.tsx")
    letter = _read("web/components/Letter.tsx")

    assert 'import {cookies} from "next/headers";' in layout
    assert '(await cookies()).get("cr8_letter")?.value === "done"' in layout
    assert "<LetterDismissedProvider dismissed={letterDismissed}>" in layout
    assert "const letterDismissed = useLetterDismissed();" in page
    assert "<Letter dismissed={letterDismissed} />" in page
    assert "export function Letter({dismissed}: {dismissed: boolean})" in letter
    assert "useState(!dismissed)" in letter
    assert "useEffect" not in letter
    assert "localStorage" not in letter
    assert 'document.cookie.includes("cr8_letter=done")' not in letter


def test_optimistic_heart_and_assignment_actions_roll_back_on_failure() -> None:
    hearts = _read("web/hooks/useHearts.ts")
    for_you = _read("web/app/for-you/page.tsx")
    heart = _between(hearts, "const heart = useCallback(async (bounceUlid: string) => {", "}, [setBulkFeedback]);")
    close = _between(for_you, "async function close", "const list =")

    assert "const wasHearted = heartsRef.current.has(bounceUlid);" in heart
    assert "if (!response?.ok)" in heart
    assert "setBulkFeedback" in heart
    assert "const previousItems = items;" in close
    assert "if (!response?.ok)" in close
    assert "setItems(previousItems);" in close
    assert "setFeedback(" in close
    assert 'role="alert"' in for_you


def test_sorting_uses_one_option_list_and_one_toggle_helper() -> None:
    page = _read("web/app/page.tsx")
    filters = _read("web/components/FilterRail.tsx")

    assert "const COLUMNS" not in page
    assert page.count("const SORT_OPTIONS") == 1
    assert "column?: {cls: string};" in filters
    assert "export function nextSort(" in filters
    assert "SORT_OPTIONS.filter((option) => option.column)" in page
    assert "nextSort(sort, option)" in page
    assert "nextSort(sort, option)" in filters
    assert "(column.initial as string)" not in page


def test_bundled_cleanup_contracts_stay_honest() -> None:
    page = _read("web/app/page.tsx")
    shell = _read("web/components/Shell.tsx")
    inspector = _read("web/components/Inspector.tsx")
    eras = _read("web/lib/eras.ts")
    css = _read("web/app/globals.css")

    assert (page + shell + inspector + eras).count('fetch("/api/eras"') == 1
    assert "getEras()" in page
    assert "getEras()" in shell
    assert "getEras()" in inspector
    for dead_selector in (
        ".detail-head",
        ".detail-art",
        ".detail-section",
        ".sheet-grid",
        ".song-round",
        ".unheard-shift {",
    ):
        assert dead_selector not in css
    assert '<p className="unheard-shift">' not in page
    count_line = _between(page, '<p className="lib-count num">', "</p>")
    assert "never heard by anyone" in count_line
    assert "Take a shift?" in count_line
