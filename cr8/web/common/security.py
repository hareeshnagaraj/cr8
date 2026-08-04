"""ASGI security headers, CSRF, Range caps, and request throttling."""

from __future__ import annotations

from collections import defaultdict, deque
import hashlib
from http.cookies import CookieError, SimpleCookie
import threading
import time
from typing import Any

from .settings import AppSettings


Headers = list[tuple[bytes, bytes]]


def _header_map(scope: dict[str, Any]) -> dict[str, str]:
    return {
        key.decode("latin-1").casefold(): value.decode("latin-1")
        for key, value in scope.get("headers", ())
    }


async def _plain(
    send: Any, status: int, body: str, extra_headers: Headers | None = None
) -> None:
    payload = body.encode("utf-8")
    headers: Headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"content-length", str(len(payload)).encode("ascii")),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send(
        {"type": "http.response.start", "status": status, "headers": headers}
    )
    await send({"type": "http.response.body", "body": payload})


class SecurityHeadersMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in {b"server", b"x-powered-by"}
                ]
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-frame-options", b"DENY"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, secure_send)


class CSRFMiddleware:
    # Pre-session endpoints: there is no authenticated state for a cross-site POST
    # to abuse, and these pages are served from a bare template that loads no JS,
    # so they cannot send the custom header. Requiring it here made login
    # impossible (403 on every attempt). Cookies remain SameSite=Lax, which is the
    # actual CSRF defence for login. Setup is the same shape — a plain form POST
    # before any session exists — and it hard-stops once the first user is created.
    EXEMPT_PATHS = frozenset({"/login", "/setup"})

    def __init__(self, app: Any) -> None:
        self.app = app

    def _exempt(self, path: str) -> bool:
        return path in self.EXEMPT_PATHS

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            if not self._exempt(scope.get("path", "")):
                headers = _header_map(scope)
                marked = (
                    headers.get("x-cr8-request") == "1"
                    or headers.get("x-crate-request") == "1"
                )
                if not marked:
                    await _plain(send, 403, "request rejected")
                    return
        await self.app(scope, receive, send)


class RangeLimitMiddleware:
    def __init__(
        self, app: Any, *, maximum_length: int = 200, maximum_ranges: int = 4
    ) -> None:
        self.app = app
        self.maximum_length = maximum_length
        self.maximum_ranges = maximum_ranges

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            value = _header_map(scope).get("range")
            if value and (
                len(value) > self.maximum_length
                or value.count(",") + 1 > self.maximum_ranges
            ):
                await _plain(send, 416, "range rejected")
                return
        await self.app(scope, receive, send)


class _Window:
    def __init__(self) -> None:
        self.values: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()
        self._calls = 0

    def allow(self, key: str, *, limit: int, seconds: float) -> bool:
        now = time.monotonic()
        with self.lock:
            # Keys were never evicted - one permanent entry per client IP or
            # session hash ever seen. Harmless at band scale, a real leak at
            # thousands; sweep the emptied ones on a cheap cadence.
            self._calls += 1
            if self._calls % 1024 == 0:
                horizon = now - seconds
                for stale in [
                    k for k, v in self.values.items()
                    if not v or v[-1] <= horizon
                ]:
                    del self.values[stale]
            values = self.values[key]
            while values and values[0] <= now - seconds:
                values.popleft()
            if len(values) >= limit:
                return False
            values.append(now)
            return True


class RateLimitMiddleware:
    def __init__(self, app: Any, *, settings: AppSettings) -> None:
        self.app = app
        self.settings = settings
        self.ip = _Window()

    # Forwarded headers are attacker-controlled unless the connection itself
    # comes from a proxy we run. Every public path terminates locally — the Next
    # app on 3100, or cloudflared — so only a loopback peer may claim to speak
    # for someone else. Without a forwarded address, that trusted hop uses a
    # hashed session cookie so direct users do not all share its bucket.
    TRUSTED_PEERS = frozenset({"127.0.0.1", "::1", "localhost"})

    def _ip(self, scope: dict[str, Any], headers: dict[str, str]) -> str:
        client = scope.get("client")
        peer = str(client[0]) if client else "unknown"
        if peer not in self.TRUSTED_PEERS:
            return peer
        # A session outranks the forwarded address: behind Cloudflare the
        # forwarded IP always exists, so keying it first put a whole NAT'd
        # household - the band at one house, QA and the owner's phone on one
        # router - into a single shared bucket, and a burst from one device
        # 429'd everyone. Cookieless traffic still buckets by IP, which is
        # the population credential-stuffing actually comes from.
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except CookieError:
            cookie = SimpleCookie()
        session = cookie.get(self.settings.cookie_name)
        if session and session.value:
            digest = hashlib.sha256(session.value.encode("utf-8")).hexdigest()
            return f"sess:{digest[:16]}"
        forwarded = headers.get("cf-connecting-ip", "").strip()
        if not forwarded:
            forwarded = headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
        return peer

    # Media serving is exempt: a library screen shows dozens of covers and the
    # covers harness shows ~2000, so counting images against the API budget
    # rate-limits the app into an empty page after any image-heavy view. These
    # paths are session-gated reads of local files — the limiter's job is API
    # abuse, not art.
    EXEMPT_PREFIXES = (
        "/static/", "/art/", "/art-preview/", "/art-strip/", "/peaks/", "/m/"
    )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or str(scope.get("path", "")).startswith(
            self.EXEMPT_PREFIXES
        ):
            await self.app(scope, receive, send)
            return
        headers = _header_map(scope)
        ip = self._ip(scope, headers)
        if not self.ip.allow(
            ip, limit=self.settings.ip_requests_per_minute, seconds=60
        ):
            await _plain(send, 429, "rate limit reached", [(b"retry-after", b"60")])
            return
        await self.app(scope, receive, send)
