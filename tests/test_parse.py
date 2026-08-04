from datetime import date, datetime

import pytest

from cr8.keys import default_spellings
from cr8.parse import is_project_internal, parse_name


MTIME = datetime(2026, 7, 29, 12, 0)
TODAY = date(2026, 7, 29)


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        (
            "1-13-24-drownme-bm",
            {"date": "2024-01-13", "title_tokens": ["drownme"], "key_raw": "bm", "parse_branch": "B1"},
        ),
        (
            "1-15-26-idontwannaneedu-f#maj-v2",
            {"date": "2026-01-15", "key_raw": "f#maj", "version": 2},
        ),
        (
            "5-20-24-backingtrack-take1-novox",
            {"mixrole": "novox", "title_tokens": ["backingtrack", "take1"]},
        ),
        (
            "7-29-26-stayhere-cm-v2",
            {"key_raw": "cm", "version": 2},
        ),
        (
            "12_8_23-jine-fmin-132",
            {"date": "2023-12-08", "title_tokens": ["jine"], "key_raw": "fmin", "bpm": 132},
        ),
        (
            "7-21-dropc#jam-vox",
            {"parse_branch": "B3", "title_tokens": ["jam"], "tunings": ["dropc#"], "mixrole": "vox"},
        ),
        (
            "diamond-11-20-25-v2",
            {"parse_branch": "B2", "date": "2025-11-20", "title_tokens": ["diamond"], "version": 2},
        ),
        (
            "jine-fmin-125",
            {
                "parse_branch": "B4",
                "date": "2026-07-29",
                "date_source": "mtime",
                "key_raw": "fmin",
                "bpm": 125,
            },
        ),
        (
            "709-26-skylinedrive",
            {"date": "2026-07-09", "title_tokens": ["skylinedrive"]},
        ),
        (
            "9--26-25-dm-jam",
            {"date": "2025-09-26", "key_raw": "dm", "title_tokens": ["jam"]},
        ),
        ("12-7-2025-something", {"date": "2025-12-07"}),
        (
            "1-14-24-gtarjam2=verterae",
            {"title_tokens": ["gtarjam2", "verterae"]},
        ),
        (
            "01 Get It Right (feat. Rohiit)",
            {"parse_branch": "B4", "key_raw": None, "bpm": None, "title_tokens": ["01 Get It Right (feat. Rohiit)"]},
        ),
        ("tmp63024", {"title_tokens": ["tmp63024"]}),
        ("leaving", {"title_tokens": ["leaving"]}),
    ],
)
def test_real_corpus_filenames(stem, expected):
    result = parse_name(
        stem,
        mtime=MTIME,
        keymap=default_spellings(),
        known_collabs=["henry", "rohiit"],
        today=TODAY,
    )
    for field, value in expected.items():
        assert getattr(result, field) == value


def test_no_year_uses_previous_year_after_mtime_month_day():
    parsed = parse_name(
        "12-8-jine",
        mtime=datetime(2025, 2, 1),
        keymap=default_spellings(),
        today=date(2026, 1, 1),
    )
    assert parsed.date == "2024-12-08"


def test_month_day_swap_is_suspect():
    parsed = parse_name(
        "13-2-24-song",
        mtime=datetime(2024, 2, 14),
        keymap=default_spellings(),
        today=TODAY,
    )
    assert parsed.date == "2024-02-13"
    assert parsed.date_suspect


def test_no_date_pattern_uses_mtime_date():
    parsed = parse_name(
        "bulk-corpus-song",
        mtime=MTIME,
        keymap=default_spellings(),
        today=TODAY,
    )
    assert parsed.parse_branch == "B4"
    assert parsed.date == "2026-07-29"
    assert parsed.date_source == "mtime"
    assert not parsed.date_suspect


def test_ambiguous_glued_date_uses_mtime_date():
    parsed = parse_name(
        "111-26-ambiguous",
        mtime=MTIME,
        keymap=default_spellings(),
        today=date(2026, 12, 31),
    )
    assert parsed.parse_branch == "B4"
    assert parsed.date == "2026-07-29"
    assert parsed.date_source == "mtime"
    assert parsed.date_suspect


@pytest.mark.parametrize(
    "stem",
    [
        "tracking 0017 [2025-08-15 101703]",
        "Freeze NewKick [2022-03-28 202258]",
        "Consolidate bass",
        "Reverse guitar",
        "Crop sample",
    ],
)
def test_project_internal_classifier(stem):
    assert is_project_internal(stem)


def test_samples_imported_is_project_internal():
    assert is_project_internal("kick", "A Project/Samples/Imported/kick.wav")
