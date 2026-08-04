#!/bin/zsh
# Interaction-integrity storm. Spams plays, transport and queue mutations at a
# LOCAL production build, then asserts the player's three faces agree and the
# app still answers. Born from a real bug: same-tick queue mutations from
# stale closures lost operations and wedged the player ("it kinda got stuck").
#
#   scripts/click-storm.sh            # against http://127.0.0.1:3100
#
# Never point this at production. It mutates the queue of whoever is signed in.
# The browse daemon's js eval drops return values from long async payloads, so
# every mutation fires as a short eval and every assertion is its own read.
set -uo pipefail

BASE="$(cd "$(dirname "$0")/.." && pwd)"
APP="${CR8_STORM_BASE:-http://127.0.0.1:3100}"
if [[ "$APP" == *cr8.li* ]]; then
  echo "refusing to storm production"; exit 1
fi

B="$BASE/.claude/skills/gstack/browse/dist/browse"
[[ -x "$B" ]] || B="${CR8_BROWSE_BIN:-$HOME/.claude/skills/gstack/browse/dist/browse}"
[[ -x "$B" ]] || { echo "browse binary not found"; exit 1; }
PW=$(cat "$BASE/secrets/owner-password.txt" 2>/dev/null) || {
  echo "no owner password file"; exit 1
}

"$B" viewport 1280x900 >/dev/null 2>&1
"$B" goto "$APP/login" >/dev/null 2>&1
sleep 2
if "$B" js "!!document.querySelector('input[name=username]')" 2>/dev/null | grep -q true; then
  "$B" fill 'input[name="username"]' "${CR8_SMOKE_USER:-owner}" >/dev/null 2>&1
  "$B" fill 'input[name="password"]' "$PW" >/dev/null 2>&1
  "$B" click 'button[type=submit]' >/dev/null 2>&1
  sleep 3
fi
"$B" goto "$APP/" >/dev/null 2>&1
sleep 2

fail() { echo "STORM FAIL: $1"; exit 1; }
evaljs() { "$B" js "$1" 2>/dev/null | tail -1; }

ROWS=$(evaljs "document.querySelectorAll('.row').length")
[[ "${ROWS:-0}" -ge 8 ]] || fail "library rows missing"

# Rapid plays across rows, then transport spam.
evaljs "var r=document.querySelectorAll('.row'); [0,3,1,5,2].forEach(i=>r[i].querySelector('button').click()); 'ok'" >/dev/null
sleep 1
evaljs "for (let i=0;i<8;i++) document.querySelectorAll('.tbtn')[2]?.click(); for (let i=0;i<6;i++) document.querySelector('.tbtn-main')?.click(); 'ok'" >/dev/null
sleep 1

# Open the queue; same-tick triple remove must land all three.
evaljs "document.querySelector('.dock').click(); 'ok'" >/dev/null
sleep 1
BEFORE=$(evaljs "parseInt(document.querySelector('.queue-count')?.textContent ?? '0', 10)")
evaljs "var rm=document.querySelectorAll('.queue-remove'); rm[0]?.click(); rm[1]?.click(); rm[2]?.click(); 'ok'" >/dev/null
sleep 1
AFTER=$(evaljs "parseInt(document.querySelector('.queue-count')?.textContent ?? '0', 10)")
[[ $((BEFORE - AFTER)) -eq 3 ]] || fail "same-tick removes lost ($BEFORE -> $AFTER)"

# Jump within the queue, close, and confirm the three faces agree.
evaljs "document.querySelectorAll('.queue-track')[3]?.click(); 'ok'" >/dev/null
sleep 1
evaljs "document.querySelector('.queue-close')?.click(); 'ok'" >/dev/null
FACES=$(evaljs "JSON.stringify({dock: document.querySelector('.dock-title')?.textContent ?? '', row: document.querySelector('.row.is-playing .name')?.textContent ?? ''})")
echo "$FACES" | grep -q '"dock":"[^"]' || fail "nothing playing after storm"
DOCK=$(echo "$FACES" | sed -E 's/.*"dock":"([^"]*)".*/\1/')
ROW=$(echo "$FACES" | sed -E 's/.*"row":"([^"]*)".*/\1/')
[[ "$DOCK" == "$ROW" ]] || fail "dock and playing row disagree ($DOCK vs $ROW)"

# The app must answer the next click instantly.
evaljs "document.querySelectorAll('.row')[7].querySelector('button').click(); 'ok'" >/dev/null
sleep 1
FINAL=$(evaljs "document.querySelector('.dock-title')?.textContent ?? ''")
[[ -n "$FINAL" && "$FINAL" != "$DOCK" ]] || fail "app did not answer the post-storm click"

echo "storm: ok ($BEFORE -> $AFTER on triple remove; faces agree; answered with '$FINAL')"
