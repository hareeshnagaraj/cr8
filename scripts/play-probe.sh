#!/bin/zsh
# Press-play latency, the number the product is judged by: a friend on a
# 5 Mbit beach connection presses play and music starts in about a second.
# Measures click -> the transport flipping to Pause with the clock moving,
# twice: unthrottled, then CDP-throttled to 5 Mbit / 150 ms RTT.
#
#   scripts/play-probe.sh             # against http://127.0.0.1:3100
#
# Budgets come from perf-budgets.json. Local only — the throttle emulation
# belongs on a production BUILD, never on production itself.
set -uo pipefail

BASE="$(cd "$(dirname "$0")/.." && pwd)"
APP="${CR8_PLAY_BASE:-http://127.0.0.1:3100}"
[[ "$APP" == *cr8.li* ]] && { echo "refusing to throttle-probe production"; exit 1; }

B="${CR8_BROWSE_BIN:-$HOME/.claude/skills/gstack/browse/dist/browse}"
[[ -x "$B" ]] || { echo "browse binary not found"; exit 1; }
PW=$(cat "$BASE/secrets/owner-password.txt" 2>/dev/null) || { echo "no owner password"; exit 1; }

UNTHROTTLED_BUDGET=$(python3 -c "import json;print(json.load(open('$BASE/perf-budgets.json'))['play_latency']['unthrottled_ms'])")
THROTTLED_BUDGET=$(python3 -c "import json;print(json.load(open('$BASE/perf-budgets.json'))['play_latency']['throttled_5mbit_ms'])")

cd "$BASE"
"$B" viewport 1280x900 >/dev/null 2>&1
"$B" goto "$APP/login" >/dev/null 2>&1
sleep 2
if "$B" js "!!document.querySelector('input[name=username]')" 2>/dev/null | grep -q true; then
  "$B" fill 'input[name="username"]' "${CR8_SMOKE_USER:-owner}" >/dev/null 2>&1
  "$B" fill 'input[name="password"]' "$PW" >/dev/null 2>&1
  "$B" click 'button[type=submit]' >/dev/null 2>&1
  sleep 3
fi

measure() {  # $1 = row index to play, $2 = settle seconds before the click
  local T=""  # zsh function vars are global by default; a wedged eval in run 2
              # once inherited run 1's time and reported 1361ms "at 5Mbit"
  # Play latency is the REPEATED action on a warm app — the beach friend's 1s
  # is after the page has loaded. Let the page and its art settle so the click
  # isn't sharing the throttled pipe with row covers still streaming in.
  "$B" goto "$APP/" >/dev/null 2>&1
  "$B" wait --networkidle >/dev/null 2>&1
  sleep "${2:-2}"
  "$B" js "
window.__playT = null;
window.__playErr = null;
(function () {
  // The library is VIRTUALIZED — only ~20 rows exist in the DOM at any
  // moment, so a fixed index past the window clicks nothing and read as
  // 'playback never started' for a whole day. Clamp into what is rendered;
  // a nearby cold row measures exactly the same thing.
  const rows = document.querySelectorAll('.row');
  if (!rows.length) {
    window.__playErr = 'no rows rendered';
    window.__playT = -2;
    return;
  }
  const row = rows[Math.min($1, rows.length - 1)];
  const clock = () => {
    const t = document.querySelector('.dock-time');
    return t ? t.textContent : null;
  };
  const t0 = performance.now();
  row.querySelector('button').click();
  const poll = setInterval(() => {
    // The 'play' event (and the Pause label) fires on INTENT, before any
    // audio has arrived. The dock clock only moves once decoded audio is
    // actually rendering — that is the moment the friend on the beach hears
    // sound, so that is what we time.
    const c = clock();
    if (c && c !== '0:00') {
      window.__playT = Math.round(performance.now() - t0);
      clearInterval(poll);
    }
    if (performance.now() - t0 > 20000) { window.__playT = -1; clearInterval(poll); }
  }, 16);
})(); 'armed'" >/dev/null 2>&1
  for _ in $(seq 1 40); do
    T=$("$B" js "window.__playT" 2>/dev/null | tail -1)
    [[ "$T" != "null" && -n "$T" ]] && break
    sleep 0.5
  done
  if [[ "$T" == "-1" || "$T" == "-2" || "$T" == "null" || -z "$T" ]]; then
    ERR=$("$B" js "window.__playErr" 2>/dev/null | tail -1)
    DOCK=$("$B" js "(document.querySelector('.dock')?.textContent||'NO DOCK').slice(0,80)" 2>/dev/null | tail -1)
    echo "  probe diagnostics: err=${ERR} dock=${DOCK}" >&2
  fi
  echo "$T"
}

# Random rows so repeated runs measure cold tracks, not yesterday's cache.
ROW_A=$((RANDOM % 12))
ROW_B=$(((RANDOM % 12) + 12))
PLAIN=$(measure "$ROW_A" 2)
[[ -z "$PLAIN" || "$PLAIN" == "null" || "$PLAIN" -lt 1 ]] 2>/dev/null && { echo "PLAY-PROBE FAIL: playback never started (unthrottled)"; exit 1; }

# 5 Mbit / 150 ms RTT — the beach, via an in-repo pacing proxy on :3199
# (the browse daemon's CDP surface deny-lists network emulation). The proxy
# throttles the app->browser direction, which is the one that decides how
# fast play starts. Cookies carry across ports (host-scoped), so the session
# from the login above still works on the proxied origin.
lsof -tnP -iTCP:3199 -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
python3 "$BASE/scripts/throttle_proxy.py" 3199 3100 --kbps 625 --latency-ms 150 &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null' EXIT
# The proxy must provably answer before anything navigates to it — a dead
# proxy leaves the browser on the fast origin and fakes a green number.
PROXY_UP=false
for _ in $(seq 1 10); do
  sleep 0.5
  [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 http://127.0.0.1:3199/login)" == "200" ]] && { PROXY_UP=true; break; }
done
$PROXY_UP || { echo "PLAY-PROBE FAIL: throttle proxy did not come up"; exit 1; }
APP="http://127.0.0.1:3199"
# Land on the proxied origin first — page-relative fetches only pass through
# the throttle once the page itself lives behind it.
"$B" goto "$APP/" >/dev/null 2>&1
sleep 3
# (Fire-and-poll: the browse daemon drops return values from long async
# evals, so the fetch stores its result on window and we poll for it.)
"$B" js "
window.__sanity = null;
(async () => {
  const lib = await fetch('/api/library?limit=1', {credentials:'same-origin'}).then(r=>r.json());
  const u = lib.tracks[0].bounce_ulid;
  const t0 = performance.now();
  // no-store: a cached range from a previous run reads in 3ms and fakes a
  // dead throttle.
  await fetch('/m/'+u+'?probe='+Date.now(), {headers:{Range:'bytes=0-1048575'}, credentials:'same-origin', cache:'no-store'}).then(r=>r.arrayBuffer());
  window.__sanity = Math.round(performance.now() - t0);
})(); 'armed'" >/dev/null 2>&1
SANITY=""
for _ in $(seq 1 20); do
  SANITY=$("$B" js "window.__sanity" 2>/dev/null | tail -1)
  [[ -n "$SANITY" && "$SANITY" != "null" ]] && break
  sleep 0.5
done
if [[ -z "$SANITY" || "$SANITY" == "null" || "$SANITY" -lt 1200 ]]; then
  echo "PLAY-PROBE FAIL: throttle did not engage (1MB in ${SANITY:-?}ms)"; exit 1
fi
echo "  throttle sanity: 1MB in ${SANITY}ms at 5Mbit — emulation live"
# One retry on a fresh random row before believing "never started": a single
# sample through the pacing proxy flakes on contention (an unlucky large file
# plus a competing fetch), and it has twice refused a deploy that measured
# comfortably under budget on rerun. A REAL regression fails both rows; the
# interaction probe runs x3 for exactly this reason.
THROTTLED=$(measure "$ROW_B" 5)
if [[ -z "$THROTTLED" || "$THROTTLED" == "null" || "$THROTTLED" -lt 1 ]] 2>/dev/null; then
  ROW_B2=$(((RANDOM % 12) + 24))
  echo "  throttled attempt 1 never started — retrying on row $ROW_B2"
  THROTTLED=$(measure "$ROW_B2" 5)
fi
"$B" cdp Network.emulateNetworkConditions '{"offline":false,"latency":0,"downloadThroughput":-1,"uploadThroughput":-1}' >/dev/null 2>&1
[[ -z "$THROTTLED" || "$THROTTLED" == "null" || "$THROTTLED" -lt 1 ]] 2>/dev/null && { echo "PLAY-PROBE FAIL: playback never started (throttled)"; exit 1; }

echo "play latency: ${PLAIN}ms unthrottled (budget ${UNTHROTTLED_BUDGET}) · ${THROTTLED}ms at 5Mbit/150ms (budget ${THROTTLED_BUDGET})"
[[ "$PLAIN" -le "$UNTHROTTLED_BUDGET" ]] || { echo "PLAY-PROBE FAIL: unthrottled over budget"; exit 1; }
[[ "$THROTTLED" -le "$THROTTLED_BUDGET" ]] || { echo "PLAY-PROBE FAIL: throttled over budget"; exit 1; }
echo "play-probe: ok"
