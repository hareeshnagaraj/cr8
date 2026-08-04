#!/bin/zsh
# Operator deploy: gate → push → remote build/restart → live smoke.
# Identity (host, launchd labels, smoke user) comes from environment or ops/env.
#
#   ops/deploy.sh                 # full ship
#   ops/deploy.sh --web-only      # skip API kickstart
#   ops/deploy.sh --smoke-only    # smoke only
#
# Required for ship (not smoke-only): CR8_DEPLOY_REMOTE
# Optional: CR8_DEPLOY_APP_DIR (default ~/cr8/Catalog on remote)
#           CR8_LAUNCHD_WEB (default com.cr8.web)
#           CR8_LAUNCHD_API (default com.cr8.api)
#           CR8_SMOKE_ORIGIN (default https://cr8.li)
#           CR8_SMOKE_USER (required for authenticated smoke)
#           CR8_SMOKE_PASSWORD_FILE (default secrets/owner-password.txt)
set -uo pipefail

BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
[[ -f "$BASE/ops/env" ]] && source "$BASE/ops/env"

REMOTE="${CR8_DEPLOY_REMOTE:-}"
APP_DIR="${CR8_DEPLOY_APP_DIR:-~/cr8/Catalog}"
LAUNCHD_WEB="${CR8_LAUNCHD_WEB:-com.cr8.web}"
LAUNCHD_API="${CR8_LAUNCHD_API:-com.cr8.api}"
SMOKE_ORIGIN="${CR8_SMOKE_ORIGIN:-https://cr8.li}"
SMOKE_USER="${CR8_SMOKE_USER:-}"
SMOKE_PW_FILE="${CR8_SMOKE_PASSWORD_FILE:-$BASE/secrets/owner-password.txt}"

WEB_ONLY=false
SMOKE_ONLY=false
[[ "${1:-}" == "--web-only" ]] && WEB_ONLY=true
[[ "${1:-}" == "--smoke-only" ]] && SMOKE_ONLY=true

if ! $SMOKE_ONLY; then
  [[ -n "$REMOTE" ]] || { echo "set CR8_DEPLOY_REMOTE (or put it in ops/env)"; exit 1; }
  scripts/gate.sh || exit 1

  echo "\n=== deploy: push"
  git push origin main || { echo "push failed"; exit 1; }

  echo "=== deploy: server build + restart"
  ssh -o BatchMode=yes "$REMOTE" "export PATH=/opt/homebrew/bin:\$PATH && set -e && cd $APP_DIR && git pull --ff-only -q && cd web && CI=true pnpm install > /tmp/cr8-deploy-install.out 2>&1 && npm run build > /tmp/cr8-deploy-build.out 2>&1 && launchctl kickstart -k gui/\$(id -u)/$LAUNCHD_WEB" || {
    echo "server web deploy failed:";
    ssh -o BatchMode=yes "$REMOTE" "tail -5 /tmp/cr8-deploy-install.out /tmp/cr8-deploy-build.out 2>/dev/null";
    exit 1; }
  if ! $WEB_ONLY; then
    ssh -o BatchMode=yes "$REMOTE" "launchctl kickstart -k gui/\$(id -u)/$LAUNCHD_API" || { echo "api kickstart failed"; exit 1; }
  fi
  sleep 15
fi

echo "=== deploy: live smoke against $SMOKE_ORIGIN"
[[ -n "$SMOKE_USER" ]] || { echo "set CR8_SMOKE_USER for authenticated smoke"; exit 1; }
[[ -f "$SMOKE_PW_FILE" ]] || { echo "missing password file: $SMOKE_PW_FILE"; exit 1; }
PW=$(cat "$SMOKE_PW_FILE")
LOGIN_B=$(python3 -c "import json;print(json.load(open('perf-budgets.json'))['live_smoke']['login_ttfb_ms'])")
LIB_B=$(python3 -c "import json;print(json.load(open('perf-budgets.json'))['live_smoke']['library_ttfb_ms'])")
MEDIA_B=$(python3 -c "import json;print(json.load(open('perf-budgets.json'))['live_smoke']['media_first_64kb_ms'])")

TTFB=$(curl -s -o /dev/null -w "%{time_starttransfer}" "$SMOKE_ORIGIN/login")
TTFB_MS=$(python3 -c "print(round($TTFB*1000))")
echo "  /login TTFB: ${TTFB_MS}ms (budget ${LOGIN_B})"
[[ "$TTFB_MS" -le "$LOGIN_B" ]] || { echo "SMOKE RED: /login slow"; exit 1; }

JAR=$(mktemp)
LIB_BODY=$(mktemp)
curl -s -c "$JAR" -o /dev/null "$SMOKE_ORIGIN/login"
curl -s -b "$JAR" -c "$JAR" -X POST "$SMOKE_ORIGIN/api/login" -d "username=$SMOKE_USER" --data-urlencode "password=$PW" -o /dev/null
LIB=$(curl -s -b "$JAR" -o "$LIB_BODY" -w "%{time_starttransfer}" "$SMOKE_ORIGIN/api/library?limit=1")
LIB_MS=$(python3 -c "print(round($LIB*1000))")
echo "  /api/library TTFB: ${LIB_MS}ms (budget ${LIB_B})"
[[ "$LIB_MS" -le "$LIB_B" ]] || { echo "SMOKE RED: library slow"; exit 1; }

ULID=$(python3 -c "import json;print(json.load(open('$LIB_BODY'))['tracks'][0]['bounce_ulid'])") || { echo "SMOKE RED: no track ulid in library response"; exit 1; }
[[ -n "$ULID" ]] || { echo "SMOKE RED: empty track ulid"; exit 1; }
MED_OUT=$(curl -s -b "$JAR" -H "Range: bytes=0-65535" -o /dev/null -w "%{http_code} %{time_total}" "$SMOKE_ORIGIN/m/$ULID")
MED_CODE=${MED_OUT%% *}
MED_MS=$(python3 -c "print(round(${MED_OUT#* }*1000))")
echo "  /m first 64KB: ${MED_MS}ms, HTTP ${MED_CODE} (budget ${MEDIA_B})"
[[ "$MED_CODE" == "206" ]] || { echo "SMOKE RED: media returned ${MED_CODE}, not 206"; exit 1; }
[[ "$MED_MS" -le "$MEDIA_B" ]] || { echo "SMOKE RED: media slow"; exit 1; }

echo "\ndeploy: LIVE AND GREEN"
