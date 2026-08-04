from pathlib import Path

import pytest

from cr8.keys import default_spellings, load_keymap, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bm", ("B minor", "10A")),
        ("bmin", ("B minor", "10A")),
        ("bminor", ("B minor", "10A")),
        ("f#m", ("F# minor", "11A")),
        ("ebm", ("D# minor", "2A")),
        ("cmaj", ("C major", "8B")),
        ("dropd", (None, None)),
        ("c", (None, None)),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw, default_spellings()) == expected


def test_yaml_covers_every_generated_spelling():
    keymap = load_keymap(Path(__file__).parents[1] / "keymap.yaml")
    assert keymap == default_spellings()
    assert len(keymap) == 102


def test_canonical_value_round_trips():
    assert normalize("F# minor", default_spellings()) == ("F# minor", "11A")
