#!/bin/zsh
# Restart the Python app the same way every time.
#
# Doing this by hand is how you end up with an old process serving new code and
# an afternoon spent debugging a fix that was never running. Kill by port, prove
# the port is free, start, prove it answers.
set -uo pipefail

BASE="${0:A:h:h}"
PORT=8080

# Invite links are generated against this. Without it they point at 127.0.0.1 -
# the address the app sees from behind its own proxy - and are useless to the
# person receiving them.
#
# The domain is the answer whenever it is actually serving. If a Cloudflare
# Access policy is ever put in front of it, invitees will bounce off the
# allow-list before reaching the join page, and this should be pointed back at
# the tailnet host for as long as that policy is on.
HOST="${CR8_PUBLIC_BASE_URL:-}"
if [[ -z "$HOST" ]]; then
  if curl -sf -o /dev/null -m 8 https://cr8.li/login 2>/dev/null; then
    HOST="https://cr8.li"
  else
    TAILNET=$(tailscale status --json 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' \
      2>/dev/null)
    [[ -n "$TAILNET" ]] && HOST="https://$TAILNET"
  fi
fi

PID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null)
if [[ -n "$PID" ]]; then
  kill $PID 2>/dev/null
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.5
    lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1 || break
  done
fi
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT is still held — refusing to start a second app"
  exit 1
fi

cd "$BASE" || exit 1
# The app is served over HTTPS on a public domain, so the session cookie says
# so. Browsers treat localhost as a secure context, so this does not break
# signing in at 127.0.0.1 during development.
#
# Keep this block free of comments between the continuations: a comment after a
# trailing backslash ends the export, and the variables below it are silently
# never set.
export CR8_BASE_DIR="$BASE" \
       CR8_DB_PATH="$BASE/catalog.db" \
       CR8_MIRROR_ROOT="$BASE/mirror" \
       CR8_SECRET_FILE="$BASE/secrets/owner-session.key" \
       CR8_COOKIE_SECURE="${CR8_COOKIE_SECURE:-1}" \
       CR8_PUBLIC_BASE_URL="$HOST"

# Loopback only. The Next app in front of it is the public face; nothing else
# should be able to reach the API directly.
nohup "$BASE/.venv/bin/uvicorn" cr8.web.owner.app:create_app --factory \
  --host 127.0.0.1 --port $PORT \
  > "$BASE/logs/owner.log" 2>&1 &

for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  code=$(curl -s -o /dev/null -w '%{http_code}' -m3 "http://127.0.0.1:$PORT/healthz" 2>/dev/null)
  [[ "$code" == "200" ]] && break
done

if [[ "$code" != "200" ]]; then
  echo "app did not come up — last lines of logs/owner.log:"
  tail -5 "$BASE/logs/owner.log"
  exit 1
fi

echo "api 200 · invite links point at ${HOST:-(this host)}"
