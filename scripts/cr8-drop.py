#!/usr/bin/env python3
"""Watch a folder and send new bounces to the crate.

Point this at wherever you export from Ableton. Anything audio that lands there
gets uploaded once and remembered, so you can leave it running and forget about
it. Standard library only: no pip install, no virtualenv, nothing to maintain.

    cr8-drop.py --url https://... --token <token> --watch ~/Music/Bounces

    cr8-drop.py --install    # run it every few minutes via launchd

Get a token from the crate's admin page. It only permits uploading, it is
recorded against your name, and it can be revoked from that same page.

The URL should be the tailnet address rather than the public domain: Cloudflare
caps request bodies at 100 MB on the free plan, which a wav bounce clears
easily, and the tailnet path has no such cap.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid


AUDIO = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a"}
LEDGER = Path.home() / ".cr8-drop.json"
SETTLE_SECONDS = 20
LABEL = "com.cr8.drop"


def load_ledger(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_ledger(path: Path, ledger: dict[str, str]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1, sort_keys=True))
    tmp.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def post(url: str, token: str, path: Path) -> tuple[bool, str]:
    """Multipart by hand, because the standard library has no client for it."""
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    body = head + path.read_bytes() + tail

    request = urllib.request.Request(
        url.rstrip("/") + "/api/upload",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
            # The app refuses writes without this; it is what stops another
            # site posting here on your behalf.
            "X-CR8-Request": "1",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200]
        return False, f"HTTP {error.code}: {detail}"
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return False, str(error)

    entry = (payload.get("files") or [{}])[0]
    if not entry.get("ok"):
        return False, str(entry.get("error", "refused"))
    if entry.get("duplicate_of"):
        return True, f"already in the crate as {entry['duplicate_of']}"
    return True, "sent"


def sweep(watch: Path, url: str, token: str, ledger_path: Path, *, verbose: bool) -> int:
    ledger = load_ledger(ledger_path)
    sent = 0
    now = time.time()
    for path in sorted(watch.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in AUDIO:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        # Ableton writes a bounce incrementally. Uploading it mid-write sends a
        # truncated file that looks fine until you play it.
        if now - stat.st_mtime < SETTLE_SECONDS:
            if verbose:
                print(f"  waiting for {path.name} to finish writing")
            continue
        key = str(path.resolve())
        digest = sha256(path)
        if ledger.get(key) == digest:
            continue
        ok, message = post(url, token, path)
        print(f"  {path.name}: {message}")
        if ok:
            ledger[key] = digest
            save_ledger(ledger_path, ledger)
            sent += 1
    return sent


def install(url: str, token: str, watch: Path, interval: int) -> None:
    """A launchd job so this keeps running without a terminal open."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>{script}</string>
    <string>--url</string><string>{url}</string>
    <string>--token</string><string>{token}</string>
    <string>--watch</string><string>{watch}</string>
    <string>--once</string>
  </array>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{Path.home()}/Library/Logs/cr8-drop.log</string>
  <key>StandardErrorPath</key><string>{Path.home()}/Library/Logs/cr8-drop.log</string>
</dict>
</plist>
"""
    plist_path.write_text(plist)
    os.chmod(plist_path, 0o600)
    print(f"wrote {plist_path}")
    print(f"start it with:  launchctl load {plist_path}")
    print(f"stop it with:   launchctl unload {plist_path}")
    print(f"log:            ~/Library/Logs/cr8-drop.log")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="the crate's address")
    parser.add_argument("--token", required=True, help="an upload token")
    parser.add_argument("--watch", required=True, type=Path, help="folder to watch")
    parser.add_argument("--once", action="store_true", help="sweep once and exit")
    parser.add_argument("--interval", type=int, default=180, help="seconds between sweeps")
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--install", action="store_true", help="write a launchd job")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    watch = args.watch.expanduser().resolve()
    if not watch.is_dir():
        print(f"not a folder: {watch}", file=sys.stderr)
        return 1

    if args.install:
        install(args.url, args.token, watch, args.interval)
        return 0

    print(f"watching {watch} -> {args.url}")
    while True:
        try:
            sent = sweep(watch, args.url, args.token, args.ledger, verbose=args.verbose)
            if sent and args.verbose:
                print(f"  sent {sent}")
        except KeyboardInterrupt:
            return 0
        except Exception as error:  # keep running; a bad file is not fatal
            print(f"  sweep failed: {error}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
