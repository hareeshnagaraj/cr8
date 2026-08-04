import csv
from pathlib import Path

import pytest

from cr8.csvio import export_csv, import_csv
from cr8.db import connect, transaction
from cr8.review import set_song


def _song(connection):
    with transaction(connection):
        cursor = connection.execute(
            "INSERT INTO songs(slug, title) VALUES('drownme', 'Drown Me')"
        )
    return int(cursor.lastrowid)


def test_set_provenance_and_tag_vocab(fixture_config, tmp_path):
    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        song_id = _song(connection)
        set_song(
            connection,
            config,
            str(song_id),
            ["status=finished", "key=f#m", "+vibe=dreamy", "+collab=henry"],
            allow_new=True,
            author="tester",
        )
        row = connection.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        assert row["human_touched"] == 1
        assert row["status"] == "finished"
        assert (row["key_canon"], row["key_camelot"], row["key_source"]) == (
            "F# minor",
            "11A",
            "human",
        )
        assert connection.execute(
            "SELECT source FROM song_tags WHERE song_id=? AND dim='vibe'",
            (song_id,),
        ).fetchone()[0] == "human"
        with pytest.raises(ValueError, match="unknown instr"):
            set_song(
                connection,
                config,
                str(song_id),
                ["+instr=hangdrum"],
                author="tester",
            )
    finally:
        connection.close()


def test_csv_round_trip_and_dry_run(fixture_config, tmp_path):
    config, _ = fixture_config
    connection = connect(tmp_path / "catalog.db")
    try:
        song_id = _song(connection)
        set_song(
            connection,
            config,
            str(song_id),
            ["key=bm", "+vibe=dreamy", "+instr=guitar", "+collab=solo"],
            allow_new=True,
            author="tester",
        )
        csv_path = tmp_path / "songs.csv"
        assert export_csv(connection, config, csv_path) == 1
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
        rows[0]["status"] = "released"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        dry = import_csv(connection, config, csv_path, dry_run=True, author="tester")
        assert dry.songs_changed == 1
        assert connection.execute(
            "SELECT status FROM songs WHERE id=?", (song_id,)
        ).fetchone()[0] == "demo"
        applied = import_csv(connection, config, csv_path, author="tester")
        assert applied.songs_changed == 1
        assert connection.execute(
            "SELECT status FROM songs WHERE id=?", (song_id,)
        ).fetchone()[0] == "released"
    finally:
        connection.close()
