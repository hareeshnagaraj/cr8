"""Authentication and account-management primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from starlette.requests import Request

from ...db import utc_now
from .database import Row, fetch_one, mutate, reading
from .settings import AppSettings
from .text import clean_text


PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=2)
SESSION_DAYS = 30
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,39}")
PASSWORD_ALPHABET = (
    "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
)


class AuthError(ValueError):
    """A deliberately generic authentication failure."""


@dataclass(frozen=True)
class UserSession:
    session_id: int
    user_id: int
    username: str
    display: str
    role: str = "band"

    @property
    def is_admin(self) -> bool:
        return self.role == "owner"


@dataclass(frozen=True)
class MemberCredentials:
    user_id: int
    username: str
    display: str
    password: str


def _session_digest(raw_sid: str, secret: bytes) -> str:
    return hmac.new(secret, raw_sid.encode("utf-8"), hashlib.sha256).hexdigest()


def _new_username(username: str) -> str:
    normalized = clean_text(username, limit=40).casefold()
    if USERNAME.fullmatch(normalized) is None:
        raise AuthError(
            "username must be 2–40 lowercase letters, numbers, dots, dashes, "
            "or underscores"
        )
    return normalized


def user_exists(settings: AppSettings) -> bool:
    with reading(settings.db_path) as connection:
        return fetch_one(connection, "SELECT id FROM users LIMIT 1") is not None


def create_owner(
    settings: AppSettings,
    *,
    username: str,
    display: str,
    password: str,
) -> int:
    username = _new_username(username)
    display = clean_text(display, limit=80)
    if not display or len(password) < 12:
        raise AuthError("owner credentials do not meet the minimum")

    def insert(connection: Any) -> int:
        if fetch_one(connection, "SELECT id FROM users LIMIT 1"):
            raise AuthError("the app is already configured")
        connection.execute(
            """
            INSERT INTO users(
              username, display, role, password_hash, created_at
            ) VALUES(?, ?, 'owner', ?, ?)
            """,
            (username, display or username, PASSWORDS.hash(password), utc_now()),
        )
        return int(connection.last_insert_rowid())

    return mutate(settings.db_path, insert)


def create_member(
    settings: AppSettings,
    *,
    username: str,
    display: str,
    password: str | None = None,
    role: str = "band",
) -> MemberCredentials:
    normalized_username = _new_username(username)
    normalized_display = clean_text(display, limit=80)
    if not normalized_display:
        raise AuthError("display name is required")
    if role not in {"owner", "band"}:
        raise AuthError("unknown role")
    generated_password = password or "".join(
        secrets.choice(PASSWORD_ALPHABET) for _ in range(16)
    )
    if len(generated_password) < 12:
        raise AuthError("password does not meet the minimum")

    def insert(connection: Any) -> int:
        if fetch_one(
            connection,
            "SELECT id FROM users WHERE username=?",
            (normalized_username,),
        ):
            raise AuthError("username already exists")
        connection.execute(
            """
            INSERT INTO users(
              username, display, role, password_hash, created_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                normalized_username,
                normalized_display,
                role,
                PASSWORDS.hash(generated_password),
                utc_now(),
            ),
        )
        return int(connection.last_insert_rowid())

    user_id = mutate(settings.db_path, insert)
    return MemberCredentials(
        user_id=user_id,
        username=normalized_username,
        display=normalized_display,
        password=generated_password,
    )


def authenticate_user(
    settings: AppSettings, username: str, password: str
) -> Row:
    with reading(settings.db_path) as connection:
        user = fetch_one(
            connection,
            """
            SELECT * FROM users
            WHERE username=?
            """,
            (clean_text(username, limit=40).casefold(),),
        )
    encoded = str(user["password_hash"]) if user is not None else "$argon2id$v=19$"
    try:
        valid = user is not None and PASSWORDS.verify(encoded, password)
    except (InvalidHashError, VerifyMismatchError):
        valid = False
    if not valid:
        raise AuthError("invalid credentials")
    return user


def create_user_session(
    settings: AppSettings, user_id: int
) -> tuple[str, UserSession]:
    raw_sid = secrets.token_urlsafe(32)
    digest = _session_digest(raw_sid, settings.session_secret)
    now = utc_now()

    def insert(connection: Any) -> int:
        connection.execute(
            """
            INSERT INTO sessions(
              sid_sha256, user_id, created_at, last_seen
            ) VALUES(?, ?, ?, ?)
            """,
            (digest, user_id, now, now),
        )
        return int(connection.last_insert_rowid())

    session_id = mutate(settings.db_path, insert)
    with reading(settings.db_path) as connection:
        user = fetch_one(connection, "SELECT * FROM users WHERE id=?", (user_id,))
    assert user is not None
    return raw_sid, UserSession(
        session_id=session_id,
        user_id=user_id,
        username=str(user["username"]),
        display=str(user["display"]),
        role=str(user["role"]),
    )


def user_session(
    request: Request, settings: AppSettings
) -> UserSession | None:
    raw_sid = request.cookies.get(settings.cookie_name)
    if not raw_sid:
        return None
    digest = _session_digest(raw_sid, settings.session_secret)
    cutoff = (datetime.now(UTC) - timedelta(days=SESSION_DAYS)).isoformat()
    with reading(settings.db_path) as connection:
        row = fetch_one(
            connection,
            """
            SELECT se.id AS session_id, u.id AS user_id,
                   u.username, u.display, u.role
            FROM sessions AS se
            JOIN users AS u ON u.id=se.user_id
            WHERE se.sid_sha256=? AND se.created_at>=?
            """,
            (digest, cutoff),
        )
    if row is None:
        return None
    return UserSession(
        session_id=int(row["session_id"]),
        user_id=int(row["user_id"]),
        username=str(row["username"]),
        display=str(row["display"]),
        role=str(row["role"]),
    )


def destroy_session(settings: AppSettings, raw_sid: str | None) -> None:
    if not raw_sid:
        return
    digest = _session_digest(raw_sid, settings.session_secret)
    mutate(
        settings.db_path,
        lambda connection: connection.execute(
            "DELETE FROM sessions WHERE sid_sha256=?", (digest,)
        ),
    )
