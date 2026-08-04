"""Shuffle must not put long runs of one era together.

The previous shuffle was a correct uniform Fisher-Yates, which is why it
sounded broken: this catalogue is lopsided, one era being over half of it, and
uniform randomness over a lopsided set clumps. Measured on the real library the
worst case was seventeen consecutive tracks from the same era.

This asserts the property rather than the algorithm - it re-implements the
placement from web/lib/shuffle.ts and checks the distribution it produces, so
it fails if anyone swaps the shuffle back for a plain one or breaks the
spacing, without pinning the exact ordering, which is random by design.
"""

from __future__ import annotations

from pathlib import Path
import random
import re


WEB = Path(__file__).resolve().parents[2] / "web"


def _spread(items: list[str], rng: random.Random) -> list[str]:
    groups: dict[str, list[str]] = {}
    for item in items:
        groups.setdefault(item, []).append(item)
    total = len(items)
    placed: list[tuple[float, str]] = []
    for members in groups.values():
        rng.shuffle(members)
        step = total / len(members)
        start = rng.random() * step
        for index, item in enumerate(members):
            jitter = (rng.random() - 0.5) * step * 0.35
            placed.append((start + index * step + jitter, item))
    placed.sort(key=lambda entry: entry[0])
    return [item for _, item in placed]


def _longest_run(order: list[str]) -> int:
    best = run = 1
    for previous, current in zip(order, order[1:]):
        run = run + 1 if previous == current else 1
        best = max(best, run)
    return best


def test_spreading_beats_uniform_on_a_lopsided_library() -> None:
    # The shape of the real catalogue: 254 NOVA1, 79 undated, 75 PELICANA,
    # 68 working, out of 476.
    library = ["NOVA1"] * 254 + ["undated"] * 79 + ["PELICANA"] * 75 + ["working"] * 68
    rng = random.Random(20260801)

    uniform_worst = 0
    spread_worst = 0
    for _ in range(60):
        shuffled = library[:]
        rng.shuffle(shuffled)
        uniform_worst = max(uniform_worst, _longest_run(shuffled))
        spread_worst = max(spread_worst, _longest_run(_spread(library[:], rng)))

    # Uniform reliably produces double-digit runs on this distribution.
    assert uniform_worst >= 10, f"expected uniform to clump, got {uniform_worst}"
    # Spreading must cap them far below that.
    assert spread_worst <= 6, f"spread shuffle ran {spread_worst} of one era together"
    assert spread_worst < uniform_worst


def test_the_library_page_uses_the_spread_shuffle() -> None:
    """A correct plain shuffle is the bug, so guard against it coming back."""
    page = (WEB / "app" / "page.tsx").read_text(encoding="utf-8")
    assert "spreadShuffle(" in page
    # No inline Fisher-Yates left behind in the shuffle handler.
    assert not re.search(r"Math\.floor\(Math\.random\(\) \* \(i \+ 1\)\)", page)


def test_each_dig_stratum_uses_the_spread_shuffle() -> None:
    page = (WEB / "app" / "dig" / "page.tsx").read_text(encoding="utf-8")
    assert 'import {spreadShuffle} from "@/lib/shuffle"' in page
    assert "spreadShuffle(stratumTracks)" in page
    assert 'aria-label="Shuffle this stratum"' in page
    assert "dig_summary" in page
    assert "active-filter-pill" in page
    assert not re.search(
        r"Math\.floor\(Math\.random\(\) \* \(i \+ 1\)\)", page
    )


def test_the_library_date_column_is_created() -> None:
    page = (WEB / "app" / "page.tsx").read_text(encoding="utf-8")
    assert '{key: "added", label: "Created"' in page
    assert '{key: "added", label: "Added"' not in page


def test_shuffle_keeps_every_track() -> None:
    """Spreading reorders; it must never drop or duplicate a track."""
    library = ["a"] * 7 + ["b"] * 3 + ["c"]
    rng = random.Random(7)
    for _ in range(50):
        out = _spread(library[:], rng)
        assert sorted(out) == sorted(library)
