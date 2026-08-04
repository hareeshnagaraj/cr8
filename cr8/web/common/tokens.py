"""One implementation of "here is a secret link, and here is what it may do".

Invite links and upload tokens are the same object wearing different hats: a
secret shown once, stored only as a keyed digest, redeemable until it expires,
runs out of uses, or is revoked. Writing that twice is how one copy ends up
comparing digests with == and the other forgetting to check revoked_at, so it
lives here and both callers use it.

The digest is HMAC over the app's session secret, the same construction used
for session cookies: a stolen database gives an attacker hashes it cannot mint
tokens from without also having the key file.

    mint() ──▶ raw token (shown to a person exactly once)
       │
       └─ digest() ──▶ token_sha256 column (all we keep)

    redeem: digest(candidate) ──▶ row lookup ──▶ check_state(row, now)
                                                     │
                            active / expired / exhausted / revoked
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import secrets
from typing import Any


TOKEN_BYTES = 32


class TokenError(ValueError):
    """A token that cannot be used, with a reason safe to show a person."""


@dataclass(frozen=True)
class TokenState:
    usable: bool
    reason: str


def mint() -> str:
    """A fresh URL-safe secret. Returned once and never stored in this form."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def digest(raw_token: str, secret: bytes) -> str:
    return hmac.new(
        secret, raw_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def matches(raw_token: str, stored_digest: str, secret: bytes) -> bool:
    """Constant-time comparison — a plain == leaks its answer in the timing."""
    return hmac.compare_digest(digest(raw_token, secret), stored_digest)


def _parse(when: Any) -> datetime | None:
    if not when:
        return None
    try:
        parsed = datetime.fromisoformat(str(when))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def check_state(row: Any, *, now: datetime | None = None) -> TokenState:
    """Why a token may or may not be used right now.

    Expects a row carrying revoked_at, expires_at, max_uses and use_count.
    Order matters for the message a person sees: revoked is a deliberate act
    and should say so even if the token also happens to have expired.
    """
    moment = now or datetime.now(UTC)
    if row is None:
        return TokenState(False, "unknown")
    if _value(row, "revoked_at"):
        return TokenState(False, "revoked")
    expires = _parse(_value(row, "expires_at"))
    if expires is not None and expires <= moment:
        return TokenState(False, "expired")
    max_uses = _value(row, "max_uses")
    if max_uses is not None:
        used = int(_value(row, "use_count") or 0)
        if used >= int(max_uses):
            return TokenState(False, "exhausted")
    return TokenState(True, "active")


def _value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)
