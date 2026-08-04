#!/bin/zsh
# Hand cr8.li from this laptop to the machine that should be serving it.
#
#   scripts/cutover.sh --check     # is the far side ready? changes nothing
#   scripts/cutover.sh             # do it
#   scripts/cutover.sh --rollback  # bring it back here
#
# The design goal is no lost writes, not no downtime. Tags, hearts and homework
# live in catalog.db, and anything written here after the database was copied
# would be lost silently - which is the worst kind of loss, because nobody
# notices until they look for a tag that is gone.
#
# So the order is: stop writing here, take a fresh snapshot, send it, then move
# the connector. The gap between the last write and the far side serving is the
# downtime, and it is about a minute.
set -uo pipefail
[[ -f "$(cd "$(dirname "$0")/.." && pwd)/ops/env" ]] && source "$(cd "$(dirname "$0")/.." && pwd)/ops/env"

APP="${0:A:h:h}"
REMOTE="${CR8_REMOTE:-${CR8_DEPLOY_REMOTE:?set CR8_DEPLOY_REMOTE}}"
RAPP="${CR8_REMOTE_APP:-${CR8_DEPLOY_APP_DIR:-~/cr8/Catalog}}"
RDATA="${CR8_REMOTE_DATA:?set CR8_REMOTE_DATA}"
MODE="${1:-run}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 "$REMOTE")
# A non-interactive ssh session does not source a login shell, so Homebrew
# is not on PATH and ffmpeg, ffprobe and fpcalc disappear. Five tool-dependent
# tests then fail and the gate blames the far side for a fault in the check.
REMOTE_ENV="export PATH=/opt/homebrew/bin:\$PATH;"

say() { print -r -- "  $*"; }

remote_ready() {
  say "checking the far side"
  local ok=1
  local tests=$($SSH "$REMOTE_ENV cd $RAPP && ./.venv/bin/python -m pytest -q 2>&1 | tail -1")
  say "tests:    $tests"
  [[ "$tests" == *"passed"* && "$tests" != *"failed"* ]] || ok=0
  for probe in "127.0.0.1:8080/healthz" "127.0.0.1:3100/login"; do
    local code=$($SSH "curl -s -o /dev/null -w '%{http_code}' -m 10 http://$probe")
    say "$probe -> $code"
    [[ "$code" == "200" ]] || ok=0
  done
  local corpus=$($SSH "du -sm $RDATA/corpus 2>/dev/null | cut -f1")
  local here=$(du -sm "${CR8_CORPUS:?set CR8_CORPUS}" 2>/dev/null | cut -f1)
  say "corpus:   ${corpus}MB there of ${here}MB here"
  if [[ -n "$corpus" && -n "$here" && "$corpus" -lt $((here * 95 / 100)) ]]; then
    say "corpus is still copying - the app will serve fine, but new files"
    say "will not be ingested there until it finishes"
  fi
  return $(( ok == 1 ? 0 : 1 ))
}

if [[ "$MODE" == "--check" ]]; then
  remote_ready && say "READY" || say "NOT READY"
  exit 0
fi

if [[ "$MODE" == "--rollback" ]]; then
  say "taking cr8.li back"
  $SSH "launchctl unload ~/Library/LaunchAgents/com.cr8.cr8-tunnel.plist 2>/dev/null" || true
  launchctl load "$HOME/Library/LaunchAgents/com.cr8.cr8-tunnel.plist" 2>/dev/null
  sleep 12
  say "cr8.li -> $(curl -s -o /dev/null -w '%{http_code}' -A Mozilla/5.0 -m 20 https://cr8.li)"
  exit 0
fi

remote_ready || { say "far side is not ready - refusing to cut over"; exit 1; }

say ""
say "--- stopping writes here ---"
# The web app is the only thing that writes to the database on behalf of a
# person. Stop it first so the snapshot cannot miss a tag saved mid-copy.
launchctl unload "$HOME/Library/LaunchAgents/com.cr8.cr8-web.plist" 2>/dev/null
launchctl unload "$HOME/Library/LaunchAgents/com.cr8.cr8-api.plist" 2>/dev/null
launchctl unload "$HOME/Library/LaunchAgents/com.cr8.crate-ingest.plist" 2>/dev/null
say "web, api and ingest stopped"

say "--- final snapshot ---"
"$APP/.venv/bin/python" - <<'PY'
import sqlite3, pathlib
src = sqlite3.connect(pathlib.Path.home() / "Music/Catalog/catalog.db")
dst = sqlite3.connect("/tmp/catalog.cutover.db")
src.backup(dst)
check = dst.execute("PRAGMA integrity_check").fetchone()[0]
counts = dst.execute(
    "SELECT (SELECT COUNT(*) FROM songs), (SELECT COUNT(*) FROM users),"
    " (SELECT COUNT(*) FROM song_tags), (SELECT COUNT(*) FROM reactions),"
    " (SELECT COUNT(*) FROM listen_assignments)"
).fetchone()
dst.close(); src.close()
print(f"  integrity {check} · songs {counts[0]} users {counts[1]} "
      f"tags {counts[2]} reactions {counts[3]} assignments {counts[4]}")
PY

say "--- sending the database and any new mirror files ---"
scp -q -o BatchMode=yes /tmp/catalog.cutover.db "$REMOTE:$RDATA/catalog.db"
rsync -a --delete -e 'ssh -o BatchMode=yes' "$APP/mirror/" "$REMOTE:$RDATA/mirror/"
rsync -a -e 'ssh -o BatchMode=yes' "$APP/stems/" "$REMOTE:$RDATA/stems/" 2>/dev/null || true
say "sent"

say "--- restarting the far side against the fresh database ---"
$SSH "launchctl kickstart -k gui/\$(id -u)/com.cr8.cr8-api 2>/dev/null; \
      launchctl kickstart -k gui/\$(id -u)/com.cr8.cr8-web 2>/dev/null"
sleep 8

say "--- moving the connector ---"
# Whichever machine runs cloudflared serves cr8.li. This is the switch.
launchctl unload "$HOME/Library/LaunchAgents/com.cr8.cr8-tunnel.plist" 2>/dev/null
$SSH "launchctl load ~/Library/LaunchAgents/com.cr8.cr8-tunnel.plist 2>/dev/null"
sleep 15

say ""
say "--- after ---"
for url in https://cr8.li https://cr8.li/login; do
  say "$url -> $(curl -s -o /dev/null -w '%{http_code}' -A Mozilla/5.0 -m 25 $url)"
done
say ""
say "this laptop still holds its own catalog.db and corpus, untouched."
say "roll back with: scripts/cutover.sh --rollback"
