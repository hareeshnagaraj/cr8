#!/bin/zsh
# Put the authenticated app on the public internet, then print what to send.
set -uo pipefail
[[ -f "$(cd "$(dirname "$0")/.." && pwd)/ops/env" ]] && source "$(cd "$(dirname "$0")/.." && pwd)/ops/env"

BASE="$HOME/Music/Catalog"
# Your own tailnet hostname. Derived from Tailscale so this file carries no
# machine-specific detail; override with CR8_HOST if you serve it elsewhere.
HOST="${CR8_HOST:-$(tailscale status --json 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' \
  2>/dev/null)}"
if [[ -z "$HOST" ]]; then
  echo "could not determine the tailnet hostname — set CR8_HOST=your-host.ts.net"
  exit 1
fi

echo "→ checking the app is running…"
if ! curl -s -m5 -o /dev/null "http://127.0.0.1:8080/login"; then
  echo "  app is down — starting it"
  cd "$BASE" || exit 1
  export CR8_BASE_DIR="$BASE" \
         CR8_DB_PATH="$BASE/catalog.db" \
         CR8_MIRROR_ROOT="$BASE/mirror" \
         CR8_SECRET_FILE="$BASE/secrets/owner-session.key" \
         CR8_COOKIE_SECURE=0
  # Loopback only: the Next app proxies to this, and nothing else should reach
  # it. No --proxy-headers either — RateLimitMiddleware decides for itself which
  # peers may speak for a client, and uvicorn rewriting scope["client"] from a
  # header first would hand that decision back to the caller.
  nohup "$BASE/.venv/bin/uvicorn" cr8.web.owner.app:create_app --factory \
    --host 127.0.0.1 --port 8080 \
    > "$BASE/logs/owner.log" 2>&1 &
  sleep 5
fi

echo "→ checking the web app is running…"
if ! curl -s -m5 -o /dev/null "http://127.0.0.1:3100/"; then
  echo "  web app is down — run scripts/web-restart.sh first"
  exit 1
fi

echo "→ pointing the public address at the real app…"
tailscale funnel --https=443 off  >/dev/null 2>&1
sleep 2
tailscale funnel --bg --https=443 http://127.0.0.1:3100 >/dev/null 2>&1

echo "→ waiting for it to come up…"
ok=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 4
  body=$(curl -s -m12 "https://$HOST/" 2>/dev/null)
  if print -r -- "$body" | grep -qi "password"; then ok="yes"; break; fi
done

echo
if [[ -n "$ok" ]]; then
  echo "✅  LIVE — send this:"
else
  echo "⚠️  not answering yet (TLS can take a minute). Try the link in ~60s:"
fi
echo
echo "    https://$HOST"
echo
echo "    username: henry"
echo "    password: $(cat "$BASE/secrets/henry-password.txt" 2>/dev/null || echo '(missing)')"
echo
echo "    your own login: $CR8_SMOKE_USER / (see secrets/owner-password.txt)"
echo
