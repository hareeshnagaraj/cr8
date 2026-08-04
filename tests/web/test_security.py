from __future__ import annotations

from pathlib import Path
import sqlite3
import stat

import apsw
from fastapi.testclient import TestClient
import pytest

from cr8.db import SCHEMA_VERSION, connect
from cr8.web.common.queries import quoted_fts_literal
from cr8.web.common.runtime import RuntimeFloorError, check_runtime
from cr8.web.common.settings import SettingsError, read_secret
from conftest import WebFixture


CSRF = {"X-CR8-Request": "1"}


def test_session_secrets_require_exact_0600_mode(tmp_path: Path):
    secret = tmp_path / "session.key"
    secret.write_bytes(b"x" * 32)
    secret.chmod(0o600)
    assert read_secret(secret) == b"x" * 32
    secret.chmod(0o400)
    with pytest.raises(SettingsError, match="mode 0600"):
        read_secret(secret)


def test_ensure_secret_creates_missing_0600_file(tmp_path: Path):
    from cr8.web.common.settings import ensure_secret

    secret = tmp_path / "secrets" / "owner-session.key"
    payload = ensure_secret(secret)
    assert len(payload) >= 32
    assert secret.is_file()
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert ensure_secret(secret) == payload


def test_needs_setup_is_true_before_first_user(web: WebFixture):
    response = web.owner.get("/api/needs-setup")
    assert response.status_code == 200
    assert response.json() == {"needs_setup": True}


def test_needs_setup_is_false_after_setup(owner: TestClient):
    response = owner.get("/api/needs-setup")
    assert response.status_code == 200
    assert response.json() == {"needs_setup": False}


def test_schema_and_fts_are_migrated_without_dropping_share_data(
    web: WebFixture,
):
    connection = sqlite3.connect(web.db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        assert {
            "users",
            "shares",
            "sessions",
            "reactions",
            "listen_progress",
            "app_alerts",
            "songs_fts",
        } <= tables
        assert connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0] == str(SCHEMA_VERSION)
        assert connection.execute(
            "SELECT COUNT(*) FROM songs_fts"
        ).fetchone()[0] == 4
        share_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(shares)")
        }
        assert "kind" not in share_columns
        assert {"scope_mode", "allow_downloads"} <= share_columns
    finally:
        connection.close()


def test_legacy_share_rows_migrate_without_deleting_existing_data(
    web: WebFixture,
):
    connection = connect(web.db_path)
    try:
        connection.execute("DROP TABLE shares")
        connection.execute(
            """
            CREATE TABLE shares (
              id INTEGER PRIMARY KEY,
              ulid TEXT UNIQUE,
              label TEXT,
              token_sha256 TEXT UNIQUE NOT NULL,
              kind TEXT NOT NULL,
              scope_json TEXT NOT NULL,
              expires_at TEXT,
              max_uses INTEGER,
              use_count INTEGER DEFAULT 0,
              revoked_at TEXT,
              created_at TEXT,
              include_stems INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            INSERT INTO shares(
              ulid, label, token_sha256, kind, scope_json,
              expires_at, max_uses, created_at, include_stems
            ) VALUES(
              'legacy-share', 'EJ', 'digest', 'listen_through', '["old"]',
              NULL, 100, '2026-07-29T00:00:00+00:00', 1
            )
            """
        )
        connection.execute(
            "UPDATE meta SET value='0' WHERE key='web_schema_version'"
        )
    finally:
        connection.close()

    from cr8.web.common.database import migrate

    migrate(web.db_path)
    connection = connect(web.db_path)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(shares)")
        }
        migrated = connection.execute(
            """
            SELECT scope_mode, scope_json, include_stems, allow_downloads
            FROM shares WHERE ulid='legacy-share'
            """
        ).fetchone()
    finally:
        connection.close()
    assert "kind" not in columns
    assert migrated["scope_mode"] == "live"
    assert migrated["scope_json"] == "[]"
    assert migrated["include_stems"] == 1
    assert migrated["allow_downloads"] == 0


def test_runtime_floors_refuse_vulnerable_versions(monkeypatch):
    check_runtime()
    monkeypatch.setattr(
        "cr8.web.common.runtime.metadata.version", lambda _name: "0.49.0"
    )
    with pytest.raises(RuntimeFloorError, match="CVE-2025-62727"):
        check_runtime()
    monkeypatch.setattr(
        "cr8.web.common.runtime.metadata.version", lambda _name: "1.3.1"
    )
    monkeypatch.setattr(
        "cr8.web.common.runtime.apsw.sqlitelibversion", lambda: "3.53.1"
    )
    with pytest.raises(RuntimeFloorError, match="CVE-2026-11822"):
        check_runtime()


def test_templates_do_not_use_the_jinja_safe_filter():
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("cr8/web").rglob("*.html")
    )
    assert "|safe" not in templates.replace(" ", "")


def test_csrf_rejects_authenticated_posts_without_the_app_header(
    web: WebFixture, owner: TestClient
):
    assert owner.post("/logout").status_code == 403
    assert owner.post(
        f"/reactions/{web.bounce_ulids[0]}/heart"
    ).status_code == 403


def test_first_run_setup_is_a_plain_form_post_without_the_app_header(
    web: WebFixture,
):
    # The setup page is a bare template: the browser submits a plain form with
    # no way to attach X-CR8-Request. CSRF must let it through, like /login.
    response = web.owner.post(
        "/setup",
        data={
            "username": "hareesh",
            "display": "Hareesh",
            "password": "correct horse battery staple",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert web.owner.get("/api/needs-setup").json() == {"needs_setup": False}


def test_media_containment_rejects_symlink_and_poisoned_row(
    web: WebFixture, owner: TestClient
):
    first = web.bounce_ulids[0]
    track = web.mirror / "tracks" / f"{first}.mp3"
    outside = web.root / "outside.mp3"
    outside.write_bytes(b"secret")
    track.unlink()
    track.symlink_to(outside)
    assert owner.get(f"/m/{first}").status_code == 404

    connection = connect(web.db_path)
    try:
        connection.execute(
            """
            UPDATE mirror_files SET mirror_relpath='../outside.mp3'
            WHERE bounce_id=(SELECT id FROM bounces WHERE public_id=?)
            """,
            (web.bounce_ulids[1],),
        )
    finally:
        connection.close()
    assert owner.get(f"/m/{web.bounce_ulids[1]}").status_code == 404


def test_range_header_caps(web: WebFixture, owner: TestClient):
    response = owner.get(
        f"/m/{web.bounce_ulids[0]}",
        headers={"Range": "bytes=0-1,2-3,4-5,6-7,8-9"},
    )
    assert response.status_code == 416
    response = owner.get(
        f"/m/{web.bounce_ulids[0]}",
        headers={"Range": "bytes=" + "1" * 220},
    )
    assert response.status_code == 416


def test_fts_queries_are_quoted_length_capped_and_generic_on_error(
    web: WebFixture, owner: TestClient, monkeypatch
):
    assert quoted_fts_literal('Stayhere" OR Diamond') == '"Stayhere"" OR Diamond"'
    response = owner.get("/", params={"q": "Stayhere"})
    assert response.status_code == 200 and "Stayhere" in response.text
    response = owner.get("/", params={"q": "Stayhere OR Diamond"})
    assert response.status_code == 200
    assert 'aria-label="Play Stayhere"' not in response.text
    assert 'aria-label="Play Diamond"' not in response.text
    response = owner.get("/", params={"q": "x" * 121})
    assert response.status_code == 400
    assert "Search could not be completed." in response.text
    assert "fts5" not in response.text.casefold()

    original_fetch_all = __import__(
        "cr8.web.common.queries", fromlist=["fetch_all"]
    ).fetch_all

    def fail(connection, sql, parameters=()):
        if "songs_search MATCH" in sql:
            raise apsw.SQLError("sensitive fts detail")
        return original_fetch_all(connection, sql, parameters)

    monkeypatch.setattr("cr8.web.common.queries.fetch_all", fail)
    response = owner.get("/", params={"q": "Stayhere"})
    assert response.status_code == 400
    assert "sensitive" not in response.text
