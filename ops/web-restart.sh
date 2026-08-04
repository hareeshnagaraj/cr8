#!/bin/zsh
# Rebuild the Next app and restart whatever is serving it.
#
# Two ways this has gone wrong before, both now handled:
#
#   A pkill pattern that misses leaves the old server running against a rebuilt
#   .next directory, which serves 500s for every asset hash it no longer has -
#   that looked exactly like "the stylesheet is broken". So the restart happens
#   by port, and this refuses to continue if the port stays held.
#
#   Once the server became a LaunchAgent, killing it by port just made launchd
#   start it again a second later, and the script declared failure while the
#   app was in fact fine. If the agent owns the port, ask launchd to restart it
#   instead of fighting it.
set -uo pipefail

WEB="$HOME/Music/Catalog/web"
PORT=3100
LABEL="com.cr8.cr8-web"

cd "$WEB" || exit 1

echo "building..."
pnpm build 2>&1 | grep -E "✓|error|Error" | tail -4

# No pipe here on purpose: `launchctl list | grep -q` exits grep early, the
# left side takes SIGPIPE, and `set -o pipefail` turns that into a failure -
# so this branch was never taken and the script fought launchd instead.
if launchctl list "$LABEL" >/dev/null 2>&1; then
  echo "restarting $LABEL (launchd owns this port)"
  launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null
else
  for pid in $(lsof -ti:$PORT 2>/dev/null); do
    kill -9 "$pid" 2>/dev/null
  done
  sleep 1
  if lsof -ti:$PORT >/dev/null 2>&1; then
    echo "port $PORT is still bound; refusing to start a second server"
    exit 1
  fi
  nohup pnpm start > /tmp/next-prod.log 2>&1 &
fi

# Wait for it to answer rather than guessing at a sleep, because a kickstart
# and a cold start take noticeably different amounts of time.
code=000
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  sleep 1
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 3 "http://127.0.0.1:$PORT/" 2>/dev/null)
  [[ "$code" == "200" ]] && break
done

css=$(curl -s -m 5 "http://127.0.0.1:$PORT/" | grep -o '/_next/static/[^"]*\.css' | head -1)
csscode=$(curl -s -o /dev/null -w "%{http_code}" -m 5 "http://127.0.0.1:$PORT$css")

echo "page $code · stylesheet $csscode"
[[ "$code" == "200" && "$csscode" == "200" ]] || {
  echo "FAILED - check ~/Library/Logs/cr8-web.log or /tmp/next-prod.log"
  tail -5 "$HOME/Library/Logs/cr8-web.log" 2>/dev/null
  tail -5 /tmp/next-prod.log 2>/dev/null
  exit 1
}
HOST="${CR8_HOST:-$(tailscale status --json 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' \
  2>/dev/null)}"
echo "https://${HOST:-127.0.0.1:3100}"
