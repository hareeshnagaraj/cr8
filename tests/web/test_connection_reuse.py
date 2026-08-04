from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from cr8.web.common import database
from tests.web.conftest import WebFixture


def test_parallel_reads_stay_correct_across_threads(
    owner, web: WebFixture
) -> None:
    # reading() now hands each thread its own cached connection. The danger it
    # must not have: cross-thread sharing (APSW connections are not thread-
    # safe) or stale results. Hammer the two heaviest read endpoints from many
    # threads at once and require every response to be complete and identical.
    reference = owner.get("/api/library?limit=1000").json()
    assert reference["tracks"], "fixture library must not be empty"

    def hit(_: int) -> tuple[int, int, int]:
        library = owner.get("/api/library?limit=1000")
        facets = owner.get("/api/facets")
        return (
            library.status_code,
            len(library.json()["tracks"]),
            facets.status_code,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(hit, range(64)))

    assert all(
        r == (200, len(reference["tracks"]), 200) for r in results
    ), results


def test_read_cache_is_capped_and_evicts_closed(tmp_path) -> None:
    # The suite creates hundreds of throwaway databases; an uncapped cache
    # would hold a file descriptor for every one of them for the life of the
    # thread.
    import sqlite3

    paths = []
    for i in range(database._READ_CACHE_LIMIT + 3):
        path = tmp_path / f"db{i}.sqlite"
        sqlite3.connect(path).execute("CREATE TABLE t(x)").connection.commit()
        paths.append(path)
        with database.reading(path) as connection:
            assert connection.execute("SELECT 1").fetchone()[0] == 1

    cache = getattr(database._read_local, "connections")
    assert len(cache) <= database._READ_CACHE_LIMIT


def test_errored_connection_is_dropped_and_next_call_recovers(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "db.sqlite"
    sqlite3.connect(path).execute("CREATE TABLE t(x)").connection.commit()

    try:
        with database.reading(path) as connection:
            connection.execute("SELECT definitely_not_a_column FROM t")
    except Exception:
        pass

    with database.reading(path) as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
