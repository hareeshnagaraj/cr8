#!/bin/zsh
# The gate. One command, machine refusal — every deploy goes through it, and a
# red gate means the change does not ship, full stop. Exists so iteration can
# be fast AND the app never gets slower: budgets live in perf-budgets.json and
# breaching one fails loudly instead of shipping quietly.
#
#   scripts/gate.sh                  # full battery against a fresh local build
#   scripts/gate.sh --skip-build     # when a fresh production build is already on :3100
#
# Order is cheapest-first so failures land early.
set -uo pipefail

BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
[[ -f "$BASE/ops/env" ]] && source "$BASE/ops/env"
SKIP_BUILD=false
[[ "${1:-}" == "--skip-build" ]] && SKIP_BUILD=true
FAILED=""

step() { echo "\n=== gate: $1"; }
die() { echo "\nGATE RED: $1 — this does not ship."; exit 1; }

step "pytest (honest exit code)"
./.venv/bin/python -m pytest -q > /tmp/cr8-gate-pytest.out 2>&1 || {
  tail -5 /tmp/cr8-gate-pytest.out; die "test suite red"; }
tail -1 /tmp/cr8-gate-pytest.out

step "typescript"
(cd web && npx tsc --noEmit) || die "tsc red"

if ! $SKIP_BUILD; then
  step "production build"
  (cd web && npm run build > /tmp/cr8-gate-build.out 2>&1) || {
    tail -10 /tmp/cr8-gate-build.out; die "next build red"; }
fi

# Payload is measured as ground truth — the JS the library page actually
# loads, summed from the browser's resource entries (decoded bytes, cache-
# independent) — because Next's manifest layout changes between versions and
# the invariant is about what a visitor pays, not what exists on disk.
# This step runs AFTER the local server is up (see below).

step "local production server"
lsof -tnP -iTCP:3100 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
# A listener on :8080 only counts if it actually answers healthy — the gate
# once reused a wedged leftover backend that 500'd every page, and the old
# plain curl (no -f) called that "up". Kill anything unhealthy, start fresh,
# and keep the log: a silent backend death should be readable, not gone.
if ! curl -sf -o /dev/null http://127.0.0.1:8080/login; then
  lsof -tnP -iTCP:8080 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
  (./.venv/bin/uvicorn cr8.web.owner.app:create_app --factory --host 127.0.0.1 --port 8080 >/tmp/cr8-gate-uvicorn.out 2>&1 &)
fi
(cd web && npx next start -p 3100 >/dev/null 2>&1 &)
sleep 4
curl -sf -o /dev/null http://127.0.0.1:8080/login || {
  tail -5 /tmp/cr8-gate-uvicorn.out 2>/dev/null; die "local backend is not healthy"; }
curl -sf -o /dev/null http://127.0.0.1:3100/login || die "local server did not come up healthy"

step "client payload budget (JS the library page actually loads)"
B="${CR8_BROWSE_BIN:-$HOME/.claude/skills/gstack/browse/dist/browse}"
"$B" goto "http://127.0.0.1:3100/" >/dev/null 2>&1
sleep 4
PAGE_JS_KB=$("$B" js "Math.round(performance.getEntriesByType('resource').filter(e=>e.name.endsWith('.js')).reduce((s,e)=>s+e.decodedBodySize,0)/1024)" 2>/dev/null | tail -1)
BUDGET_KB=$(python3 -c "import json;print(json.load(open('perf-budgets.json'))['payload']['library_page_js_kb'])")
echo "  library page JS: ${PAGE_JS_KB:-?} KB decoded (budget ${BUDGET_KB} KB)"
[[ -n "$PAGE_JS_KB" && "$PAGE_JS_KB" != "null" ]] || die "payload measurement returned nothing"
# The library page genuinely loads ~640 KB; a tiny figure is a broken
# measurement (dead daemon, unloaded page), not a small app. 0 KB once
# passed this gate while the whole backend was down.
[[ "$PAGE_JS_KB" -ge 100 ]] || die "payload measured ${PAGE_JS_KB} KB — measurement is broken, not the app"
[[ "$PAGE_JS_KB" -le "$BUDGET_KB" ]] || die "library page JS grew past budget"

step "interaction probe x3 (perf-probe.sh)"
for i in 1 2 3; do
  OUT=$(scripts/perf-probe.sh 2>&1)
  echo "$OUT" | grep -E "repaint p75|frames p75" | head -2 | sed 's/^/  /'
  if echo "$OUT" | grep -q "OVER BUDGET"; then FAILED="probe-$i"; fi
  if echo "$OUT" | grep -q "no samples"; then FAILED="probe-nosamples-$i"; fi
done
[[ -z "$FAILED" ]] || die "interaction probe over budget ($FAILED) after 3 runs"

step "interaction integrity (click-storm.sh)"
scripts/click-storm.sh || die "click storm red"

step "play latency (play-probe.sh)"
scripts/play-probe.sh || die "play latency over budget"

echo "\ngate: GREEN — ship it with scripts/deploy.sh"
