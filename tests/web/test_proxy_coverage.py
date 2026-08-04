"""Every path the browser fetches must be reachable through the Next server.

This has now been the same bug three times. Only the Next app on :3100 is
exposed publicly - FastAPI listens on loopback and the tunnel has no route to
it - so a path missing from next.config.ts is a 404 in production and nowhere
else. It works on the developer's machine, where :8080 is reachable directly.

It is silent every time, because the callers fire and forget: listening was
never recorded for the whole port, and every triage verdict was discarded while
the queue advanced and the next track played as if it had saved.

So this asserts the rule rather than the instances: find every fetch() in the
web app, and require a matching rewrite for any path that is not already under
/api. A new page that fetches a new prefix fails here, at the point where it is
cheap to notice.
"""

from __future__ import annotations

from pathlib import Path
import re


WEB = Path(__file__).resolve().parents[2] / "web"

# fetch(`/foo/${x}`) and fetch("/foo") - captures the first path segment.
FETCH = re.compile(r"""fetch\(\s*[`'"](/[a-z][a-z0-9_-]*)""", re.IGNORECASE)
# {source: "/foo/:path*", ...}
REWRITE = re.compile(r"""source:\s*["'](/[a-z][a-z0-9_-]*)""", re.IGNORECASE)


def _sources() -> list[Path]:
    return [
        path
        for directory in ("app", "components")
        for path in (WEB / directory).rglob("*.tsx")
    ] + [
        path
        for directory in ("app", "components")
        for path in (WEB / directory).rglob("*.ts")
    ]


def test_every_fetched_path_is_proxied_to_the_python_app() -> None:
    config = (WEB / "next.config.ts").read_text(encoding="utf-8")
    proxied = {match.group(1) for match in REWRITE.finditer(config)}
    assert "/api" in proxied, "sanity: the rewrite list did not parse"

    missing: dict[str, list[str]] = {}
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for match in FETCH.finditer(text):
            prefix = match.group(1)
            if prefix == "/api" or prefix in proxied:
                continue
            missing.setdefault(prefix, []).append(
                str(path.relative_to(WEB))
            )

    assert not missing, (
        "these paths are fetched by the browser but have no rewrite in "
        "next.config.ts, so they 404 in production:\n"
        + "\n".join(
            f"  {prefix}  <- {', '.join(sorted(set(files)))}"
            for prefix, files in sorted(missing.items())
        )
    )


def test_the_guard_would_actually_catch_a_missing_rewrite() -> None:
    """A test that cannot fail is worse than no test, so prove it fails."""
    config = 'rewrites() { return [{source: "/api/:path*"}]; }'
    proxied = {match.group(1) for match in REWRITE.finditer(config)}
    sample = 'await fetch(`/triage/${id}`, {method: "POST"});'
    found = [
        match.group(1)
        for match in FETCH.finditer(sample)
        if match.group(1) != "/api" and match.group(1) not in proxied
    ]
    assert found == ["/triage"]


def test_cover_previews_are_proxied_to_the_python_app() -> None:
    config = (WEB / "next.config.ts").read_text(encoding="utf-8")
    proxied = {match.group(1) for match in REWRITE.finditer(config)}
    assert "/art-preview" in proxied


def test_art_strips_are_proxied_to_the_python_app() -> None:
    config = (WEB / "next.config.ts").read_text(encoding="utf-8")
    proxied = {match.group(1) for match in REWRITE.finditer(config)}
    assert "/art-strip" in proxied


def test_upload_proxy_accepts_the_advertised_512_mb_limit() -> None:
    config = (WEB / "next.config.ts").read_text(encoding="utf-8")
    assert "proxyClientMaxBodySize: 512 * 1024 * 1024" in config
    assert "middlewareClientMaxBodySize" not in config


def test_public_share_landing_and_audio_are_proxied() -> None:
    config = (WEB / "next.config.ts").read_text(encoding="utf-8")
    assert '{source: "/s/:path*", destination: `${API}/s/:path*`}' in config
