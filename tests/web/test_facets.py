from __future__ import annotations

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from cr8.db import connect
from cr8.web.common import queries
from cr8.web.common.queries import (
    LibraryFilter,
    filter_vocabulary_counts,
    library_songs,
)
from cr8.web.common.settings import AppSettings
from conftest import WebFixture


FACET_STATUSES = ("idea", "jam", "demo", "mixed", "finished")


def _legacy_facets(settings: AppSettings, *, actor: str) -> dict[str, object]:
    connection = connect(settings.db_path)
    try:
        keys = [
            {"value": str(row["value"]), "count": int(row["count"])}
            for row in connection.execute(
                """
                WITH ranked AS (
                  SELECT b.song_id, b.public_id AS bounce_ulid,
                         ROW_NUMBER() OVER (
                           PARTITION BY b.song_id
                           ORDER BY COALESCE(b.bounce_date, '') DESC,
                                    COALESCE(b.version, 0) DESC, b.id DESC
                         ) AS newest
                  FROM bounces AS b
                  JOIN mirror_files AS mf ON mf.bounce_id=b.id
                )
                SELECT s.key_canon AS value, COUNT(*) AS count
                FROM songs AS s
                JOIN ranked AS r ON r.song_id=s.id AND r.newest=1
                WHERE s.status!='released' AND s.key_canon IS NOT NULL
                GROUP BY s.key_canon
                ORDER BY s.key_canon COLLATE NOCASE
                """
            )
        ]
        unheard = int(
            connection.execute(
                """
                WITH ranked AS (
                  SELECT b.song_id, b.public_id AS bounce_ulid,
                         ROW_NUMBER() OVER (
                           PARTITION BY b.song_id
                           ORDER BY COALESCE(b.bounce_date, '') DESC,
                                    COALESCE(b.version, 0) DESC, b.id DESC
                         ) AS newest
                  FROM bounces AS b
                  JOIN mirror_files AS mf ON mf.bounce_id=b.id
                )
                SELECT COUNT(*)
                FROM songs AS s
                JOIN ranked AS r ON r.song_id=s.id AND r.newest=1
                WHERE s.status!='released'
                  AND NOT EXISTS(
                    SELECT 1 FROM listen_progress AS lp
                    WHERE lp.share_id=0 AND lp.bounce_ulid=r.bounce_ulid
                      AND lp.actor=? AND lp.state='heard'
                  )
                """,
                (actor,),
            ).fetchone()[0]
        )
    finally:
        connection.close()
    return {
        "status": [
            {
                "value": value,
                "count": len(
                    library_songs(
                        settings,
                        LibraryFilter(status=value),
                        actor=actor,
                        limit=10_000,
                    )
                ),
            }
            for value in FACET_STATUSES
        ],
        "tags": filter_vocabulary_counts(settings),
        # Keys are canonical only: Camelot is a display/sort alias, not a
        # second facet value for the same song.
        "keys": keys,
        # Crate-wide means the latest mirrored bounce for each non-released
        # song, scoped to the signed-in actor's heard state.
        "unheard_count": unheard,
    }


def _assert_live_equivalence(
    owner: TestClient, settings: AppSettings
) -> None:
    actor = str(owner.get("/api/me").json()["username"])
    response = owner.get("/api/facets")
    reference = _legacy_facets(settings, actor=actor)

    assert response.status_code == 200
    assert response.json() == reference
    assert response.content == JSONResponse(reference).body


def test_facets_match_legacy_five_scan_json_before_and_after_mutation(
    web: WebFixture, owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_live_equivalence(owner, web.owner_settings)

    original_limit = queries.MAX_LIBRARY_RESULTS
    monkeypatch.setattr(queries, "MAX_LIBRARY_RESULTS", 2)
    _assert_live_equivalence(owner, web.owner_settings)
    monkeypatch.setattr(queries, "MAX_LIBRARY_RESULTS", original_limit)

    connection = connect(web.db_path)
    try:
        for song_ulid, status in zip(
            web.song_ulids,
            ("released", "idea", "jam", "finished"),
            strict=True,
        ):
            connection.execute(
                "UPDATE songs SET status=? WHERE public_id=?",
                (status, song_ulid),
            )
        connection.execute(
            """
            INSERT INTO songs(slug, title, status, public_id)
            VALUES('unmirrored-mix', 'Unmirrored Mix', 'mixed',
                   '01ARZ3NDEKTSV4RRFFQ69G5FB4')
            """
        )
        song_id = int(
            connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO bounces(public_id, song_id, source_stem, bounce_date)
            VALUES('01ARZ3NDEKTSV4RRFFQ69G5FB5', ?, 'unmirrored-mix',
                   '2026-08-01')
            """,
            (song_id,),
        )
    finally:
        connection.close()

    _assert_live_equivalence(owner, web.owner_settings)
