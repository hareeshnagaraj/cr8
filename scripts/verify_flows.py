#!/usr/bin/env python3
"""Walk the new flows against the running app, the way a person would.

Unit tests use an in-process app with a fresh database. This talks to the real
thing over the real proxy, which is where the interesting failures live: a
route the web server never forwards, a link that points at 127.0.0.1, a page
that renders without its stylesheet.

    scripts/verify_flows.py            # against http://127.0.0.1:3100
    scripts/verify_flows.py https://...

Creates and removes its own throwaway account. Never touches existing data.
"""

from __future__ import annotations

import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:3100"
ROOT = Path(__file__).resolve().parent.parent
# Cloudflare turns away the default Python user agent with a 403 before the
# request ever reaches the tunnel, so checking the live domain means looking
# like a browser. This is also why the upload watcher targets the tailnet
# address rather than cr8.li.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
WRITE = {"X-CR8-Request": "1", "User-Agent": BROWSER_UA}

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if ok:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
        failures.append(label)


class Session:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirect(),
        )

    def request(
        self, method: str, path: str, *, data: object = None, form: dict | None = None
    ) -> tuple[int, str]:
        url = path if path.startswith("http") else BASE + path
        body = None
        headers = dict(WRITE)
        if form is not None:
            body = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=20) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")

    def json(self, method: str, path: str, **kwargs: object) -> tuple[int, dict]:
        status, text = self.request(method, path, **kwargs)  # type: ignore[arg-type]
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            return status, {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def main() -> int:
    print(f"verifying {BASE}\n")

    print("signed out")
    anon = Session()
    status, _ = anon.request("GET", "/api/library?limit=1")
    check("library needs a session", status == 401, f"got {status}")
    status, login_html = anon.request("GET", "/login")
    check("login page renders", status == 200 and "password" in login_html.lower())
    css = ""
    for fragment in login_html.split('href="'):
        if fragment.startswith("/static/") and ".css" in fragment.split('"')[0]:
            css = fragment.split('"')[0]
            break
    check("login page names a stylesheet", bool(css), "no /static css link")
    if css:
        status, body = anon.request("GET", css)
        check("stylesheet is served", status == 200 and len(body) > 1000, f"{status}")

    print("\nsigned in as the owner")
    password = (ROOT / "secrets" / "owner-password.txt").read_text().strip()
    owner = Session()
    status, _ = owner.request(
        "POST", "/login", form={"username": "hareesh", "password": password}
    )
    check("owner can sign in", status in {200, 303}, f"got {status}")
    status, me = owner.json("GET", "/api/me")
    check("owner is an admin", status == 200 and me.get("is_admin") is True, str(me))
    status, roster = owner.json("GET", "/api/members")
    check("member roster loads", status == 200 and roster.get("members"), str(status))

    print("\ninvites")
    status, created = owner.json(
        "POST",
        "/api/admin/invites",
        data={"label": "flow check", "role": "band", "max_uses": 1, "expires_days": 1},
    )
    check("invite is created", status == 201, str(created)[:120])
    join_url = created.get("join_url", "")
    check(
        "join link is reachable by a person",
        bool(join_url) and "127.0.0.1" not in join_url and "//" in join_url,
        join_url or "missing",
    )
    token = join_url.rsplit("/join/", 1)[-1]
    ulid = created.get("invite", {}).get("ulid", "")

    status, preview = anon.json("GET", f"/api/join/{token}")
    check("a stranger can check the invite", status == 200 and preview.get("usable"))
    status, page = anon.request("GET", f"/join/{token}")
    check("the join page renders signed out", status == 200 and len(page) > 500, f"{status}")

    print("\nclaiming it")
    newcomer = Session()
    status, claimed = newcomer.json(
        "POST",
        "/api/join",
        data={
            "token": token,
            "username": "flowcheck",
            "display": "Flow Check",
            "password": "a-long-enough-password",
        },
    )
    check("the invite creates an account", status == 201, str(claimed)[:120])
    check("and it is not an admin", claimed.get("role") == "band", str(claimed))
    status, _ = newcomer.request("GET", "/api/admin/invites")
    check("a member cannot manage invites", status == 403, f"got {status}")
    status, _ = newcomer.request("GET", "/api/library?limit=1")
    check("but can use the library", status == 200, f"got {status}")
    status, again = newcomer.json(
        "POST",
        "/api/join",
        data={"token": token, "username": "second", "password": "a-long-enough-password"},
    )
    check("a spent invite is refused", status in {404, 409}, f"got {status}")

    print("\nhomework")
    status, library = owner.json("GET", "/api/library?limit=1")
    rows = library if isinstance(library, list) else library.get("tracks", [])
    bounce = rows[0]["bounce_ulid"] if rows else ""
    check("library returns a track to send", bool(bounce))
    status, sent = owner.json(
        "POST",
        "/api/assignments",
        data={"bounce_ulids": [bounce], "to": "flowcheck", "note": "the bridge"},
    )
    check("a track can be put on someone's plate", status == 201 and sent.get("created") == 1, str(sent)[:120])
    status, plate = newcomer.json("GET", "/api/assignments")
    items = plate.get("assignments", [])
    check("it shows up on their plate", len(items) == 1, str(plate)[:120])
    if items:
        check("with the note and the sender", items[0]["note"] == "the bridge" and items[0]["assigned_by"] == "hareesh")
    status, count = newcomer.json("GET", "/api/assignments/count")
    check("the badge counts it", count.get("pending") == 1, str(count))

    status, _ = newcomer.request(
        "POST", f"/progress/{bounce}", form={"state": "heard", "heard_s": "90"}
    )
    check("listening is recorded through the web app", status == 204, f"got {status}")
    status, plate = newcomer.json("GET", "/api/assignments")
    items = plate.get("assignments", [])
    check("listening does not clear the card", len(items) == 1, str(plate)[:120])
    if items:
        check("but it is marked listened", items[0]["state"] == "heard", items[0]["state"])
        status, _ = newcomer.json("POST", f"/api/assignments/{items[0]['ulid']}/done")
        check("an explicit tap finishes it", status == 200, f"got {status}")
        status, plate = newcomer.json("GET", "/api/assignments")
        check("and then the plate is clear", not plate.get("assignments"))

    print("\npages")
    for path in ["/", "/for-you", "/admin", "/collections", "/activity", "/triage"]:
        status, body = owner.request("GET", path)
        check(f"{path} renders", status == 200 and len(body) > 400, f"{status}")

    print("\ncleaning up")
    if ulid:
        owner.json("POST", f"/api/admin/invites/{ulid}/revoke")
    status, _ = owner.json("POST", "/api/admin/members/flowcheck/remove")
    check("the throwaway account is removed", status == 200, f"got {status}")
    status, roster = owner.json("GET", "/api/admin/members")
    names = [m["username"] for m in roster.get("members", [])]
    check("and is gone from the roster", "flowcheck" not in names, str(names))

    print()
    if failures:
        print(f"{len(failures)} of {checks} checks failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print(f"all {checks} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
