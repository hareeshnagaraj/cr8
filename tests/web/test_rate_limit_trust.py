"""Who is allowed to say where a request came from.

The rate limiter keyed on X-Forwarded-For no matter who sent it, so anyone
reaching the app could hand it a new value per request and never fill a bucket.
That is not a rate limit. Forwarded headers now only count when the connection
itself comes from a proxy we run.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest

from cr8.web.common.security import RateLimitMiddleware
from tests.web.conftest import WebFixture


def _limiter() -> RateLimitMiddleware:
    settings = SimpleNamespace(cookie_name="crate_owner_sid")
    return RateLimitMiddleware(app=None, settings=settings)


def _scope(peer: str | None) -> dict[str, Any]:
    return {"type": "http", "client": (peer, 12345) if peer else None}


def test_untrusted_peer_cannot_claim_another_address() -> None:
    limiter = _limiter()
    key = limiter._ip(
        _scope("100.64.0.9"),
        {
            "x-forwarded-for": "9.9.9.9",
            "cf-connecting-ip": "8.8.8.8",
            "cookie": "crate_owner_sid=attacker-chosen",
        },
    )
    assert key == "100.64.0.9"


def test_untrusted_peer_gets_the_same_key_however_it_lies() -> None:
    """The whole point: varying the header must not vary the bucket."""
    limiter = _limiter()
    keys = {
        limiter._ip(_scope("100.64.0.9"), {"x-forwarded-for": f"10.0.0.{n}"})
        for n in range(20)
    }
    assert keys == {"100.64.0.9"}


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "localhost"])
def test_local_proxy_may_forward_a_client(peer: str) -> None:
    limiter = _limiter()
    assert limiter._ip(_scope(peer), {"x-forwarded-for": "203.0.113.7"}) == (
        "203.0.113.7"
    )


def test_cloudflare_header_wins_over_forwarded_for() -> None:
    """Behind the tunnel, CF-Connecting-IP is the one Cloudflare controls;
    X-Forwarded-For can carry whatever the original caller appended."""
    limiter = _limiter()
    key = limiter._ip(
        _scope("127.0.0.1"),
        {
            "cf-connecting-ip": "203.0.113.7",
            "x-forwarded-for": "9.9.9.9",
        },
    )
    assert key == "203.0.113.7"


def test_first_forwarded_hop_is_used() -> None:
    limiter = _limiter()
    key = limiter._ip(
        _scope("127.0.0.1"),
        {
            "x-forwarded-for": "203.0.113.7, 10.0.0.1",
        },
    )
    assert key == "203.0.113.7"


def test_local_proxy_uses_distinct_session_buckets_without_forwarding() -> None:
    limiter = _limiter()
    first = limiter._ip(
        _scope("127.0.0.1"), {"cookie": "crate_owner_sid=first-session"}
    )
    second = limiter._ip(
        _scope("127.0.0.1"), {"cookie": "crate_owner_sid=second-session"}
    )

    assert first == "sess:" + hashlib.sha256(b"first-session").hexdigest()[:16]
    assert second == "sess:" + hashlib.sha256(b"second-session").hexdigest()[:16]
    assert first != second
    assert "first-session" not in first
    assert "second-session" not in second


def test_local_proxy_without_headers_falls_back_to_itself() -> None:
    limiter = _limiter()
    assert limiter._ip(_scope("127.0.0.1"), {}) == "127.0.0.1"


def test_missing_client_is_not_a_crash() -> None:
    limiter = _limiter()
    assert limiter._ip(_scope(None), {}) == "unknown"


def test_rotating_the_header_still_hits_the_limit(web: WebFixture) -> None:
    """End to end: an untrusted caller cannot outrun the limiter by lying."""
    settings = web.owner_settings
    limit = settings.ip_requests_per_minute
    with TestClient(
        web.owner.app, client=("100.64.0.9", 55555)
    ) as client:
        last = None
        for index in range(limit + 5):
            last = client.get(
                "/healthz", headers={"X-Forwarded-For": f"10.0.0.{index % 250}"}
            )
            if last.status_code == 429:
                break
        assert last is not None
        assert last.status_code == 429
        assert last.headers["retry-after"] == "60"


def test_a_session_outranks_the_shared_household_ip() -> None:
    # Behind Cloudflare every request carries cf-connecting-ip, so keying it
    # first put a NAT'd household - the whole band at one house - into one
    # bucket, and a burst from one device 429'd everyone. Sessions are the
    # finer identity; cookieless traffic still buckets by address.
    limiter = _limiter()
    with_session = limiter._ip(
        _scope("127.0.0.1"),
        {"cf-connecting-ip": "203.0.113.7", "cookie": "crate_owner_sid=abc"},
    )
    other_session = limiter._ip(
        _scope("127.0.0.1"),
        {"cf-connecting-ip": "203.0.113.7", "cookie": "crate_owner_sid=xyz"},
    )
    cookieless = limiter._ip(
        _scope("127.0.0.1"), {"cf-connecting-ip": "203.0.113.7"}
    )
    assert with_session.startswith("sess:")
    assert other_session.startswith("sess:")
    assert with_session != other_session
    assert cookieless == "203.0.113.7"
