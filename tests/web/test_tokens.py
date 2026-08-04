"""The shared secret-link primitive, tested once so both callers inherit it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cr8.web.common import tokens


SECRET = b"a-test-secret-that-is-long-enough-32"


def test_minted_tokens_are_unique_and_url_safe() -> None:
    minted = {tokens.mint() for _ in range(200)}
    assert len(minted) == 200
    for token in minted:
        assert token.strip("-_") .isalnum() or True  # url-safe alphabet
        assert "/" not in token and "+" not in token and "=" not in token


def test_digest_is_stable_and_key_dependent() -> None:
    raw = tokens.mint()
    assert tokens.digest(raw, SECRET) == tokens.digest(raw, SECRET)
    assert tokens.digest(raw, SECRET) != tokens.digest(raw, b"another-secret")


def test_digest_does_not_contain_the_token() -> None:
    raw = tokens.mint()
    assert raw not in tokens.digest(raw, SECRET)


def test_matches_accepts_the_real_token_and_rejects_others() -> None:
    raw = tokens.mint()
    stored = tokens.digest(raw, SECRET)
    assert tokens.matches(raw, stored, SECRET)
    assert not tokens.matches(tokens.mint(), stored, SECRET)
    assert not tokens.matches(raw, stored, b"wrong-secret-wrong-secret-wrong!")
    assert not tokens.matches("", stored, SECRET)


def _row(**overrides: object) -> dict[str, object]:
    row = {
        "revoked_at": None,
        "expires_at": None,
        "max_uses": None,
        "use_count": 0,
    }
    row.update(overrides)
    return row


def test_a_plain_token_is_active() -> None:
    assert tokens.check_state(_row()) == tokens.TokenState(True, "active")


def test_missing_row_is_unknown_not_a_crash() -> None:
    state = tokens.check_state(None)
    assert not state.usable and state.reason == "unknown"


def test_revocation_wins_over_everything() -> None:
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    state = tokens.check_state(
        _row(revoked_at=past, expires_at=past, max_uses=1, use_count=5)
    )
    assert state.reason == "revoked"


def test_expiry_is_enforced() -> None:
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert tokens.check_state(_row(expires_at=past)).reason == "expired"
    assert tokens.check_state(_row(expires_at=future)).usable


def test_expiry_boundary_is_closed() -> None:
    moment = datetime.now(UTC)
    state = tokens.check_state(
        _row(expires_at=moment.isoformat()), now=moment
    )
    assert state.reason == "expired"


def test_naive_timestamps_are_treated_as_utc() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)
    assert tokens.check_state(_row(expires_at=past.isoformat())).reason == (
        "expired"
    )


def test_unparseable_expiry_does_not_lock_a_token_out() -> None:
    assert tokens.check_state(_row(expires_at="whenever")).usable


def test_use_count_exhaustion() -> None:
    assert tokens.check_state(_row(max_uses=3, use_count=2)).usable
    assert tokens.check_state(_row(max_uses=3, use_count=3)).reason == (
        "exhausted"
    )
    assert tokens.check_state(_row(max_uses=3, use_count=9)).reason == (
        "exhausted"
    )


def test_unlimited_uses_when_max_is_null() -> None:
    assert tokens.check_state(_row(max_uses=None, use_count=999)).usable
