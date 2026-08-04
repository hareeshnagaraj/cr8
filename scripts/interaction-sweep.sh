#!/bin/zsh
# Clicks real controls and asserts the app CHANGED.
#
# The UI audit proves a control is reachable. It cannot prove the control does
# anything. "Clicking demo does not filter" was exactly that gap: the chip was
# reachable, pressed, correctly wired, and the result still looked inert. Each
# check below names what it expects to change and fails loudly when it does not.
#
#   scripts/interaction-sweep.sh            # sweep the Next app on :3100
#   CR8_SWEEP_BASE=... scripts/interaction-sweep.sh
set -uo pipefail

BASE="$HOME/Music/Catalog"
B="$HOME/.claude/skills/gstack/browse/dist/browse"
APP="${CR8_SWEEP_BASE:-http://127.0.0.1:3100}"

[[ -x "$B" ]] || { echo "browse not built: $B"; exit 1; }
PW=$(cat "$BASE/secrets/owner-password.txt") || exit 1

j() { "$B" js "$1" 2>/dev/null | tail -1 | sed 's/^"//;s/"$//'; }

"$B" viewport 1440x900 >/dev/null 2>&1
"$B" goto "$APP/login" >/dev/null 2>&1; sleep 2
"$B" fill "#username" "${CR8_SMOKE_USER:-owner}" >/dev/null 2>&1
"$B" fill "#password" "$PW" >/dev/null 2>&1
"$B" click "button[type=submit]" >/dev/null 2>&1; sleep 3
"$B" goto "$APP/" >/dev/null 2>&1; sleep 5

pass=0; fail=0
check() {  # name, before, after
  if [[ "$2" != "$3" && -n "$2" && -n "$3" ]]; then
    print -r -- "  [ok  ] $1: ${2:0:30} -> ${3:0:30}"; ((pass++))
  else
    print -r -- "  [FAIL] $1: stayed at ${2:0:40}"; ((fail++))
  fi
}

echo "== library =="

before=$(j 'document.querySelector(".lib-count").textContent')
j 'var i=document.querySelector(".search");
   Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(i,"jam");
   i.dispatchEvent(new Event("input",{bubbles:true}));""' >/dev/null; sleep 2
check "search 'jam'" "$before" "$(j 'document.querySelector(".lib-count").textContent')"
j 'var i=document.querySelector(".search");
   Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(i,"");
   i.dispatchEvent(new Event("input",{bubbles:true}));""' >/dev/null; sleep 2

for idx in 0 1 2 3 4 5; do
  label=$(j "(function(){var c=[].slice.call(document.querySelectorAll('.frail .chip')).filter(function(x){return !x.disabled});return c[$idx]?c[$idx].textContent.trim():''})()")
  [[ -z "$label" ]] && continue
  before=$(j 'document.querySelector(".lib-count").textContent')
  j "(function(){var c=[].slice.call(document.querySelectorAll('.frail .chip')).filter(function(x){return !x.disabled});c[$idx].click();return ''})()" >/dev/null
  sleep 2
  check "filter $label" "$before" "$(j 'document.querySelector(".lib-count").textContent')"
  j "(function(){var c=[].slice.call(document.querySelectorAll('.frail .chip')).filter(function(x){return !x.disabled});c[$idx].click();return ''})()" >/dev/null
  sleep 2
done

for idx in 1 2 3 4 5; do
  label=$(j "(function(){var h=document.querySelectorAll('.th');return h[$idx]?h[$idx].textContent.trim():''})()")
  [[ -z "$label" ]] && continue
  before=$(j 'document.querySelector(".name-link").textContent')
  j "document.querySelectorAll('.th')[$idx].click();''" >/dev/null; sleep 2
  check "sort $label" "$before" "$(j 'document.querySelector(".name-link").textContent')"
done

echo "== playback =="
before=$(j '(document.querySelector(".dock-title")||{textContent:"(none)"}).textContent')
j 'document.querySelector(".row-play").click();""' >/dev/null; sleep 3
after=$(j '(document.querySelector(".dock-title")||{textContent:"(none)"}).textContent')
check "play a row" "$before" "$after"
check "inspector follows" "(none)" "$(j '(document.querySelector(".ins-title")||{textContent:"(none)"}).textContent')"

before=$(j 'document.querySelector(".name-link").textContent')
j 'document.querySelector(".primary").click();""' >/dev/null; sleep 3
check "shuffle reorders list" "$before" "$(j 'document.querySelector(".name-link").textContent')"

echo "== writes =="
# Target an ADDITIVE dimension. Status, keeper and key are single-select, so
# turning one on turns another off and the active count never moves - which
# looks like a failed write when nothing is wrong.
vibe_sel='(function(){var g=[].slice.call(document.querySelectorAll(".ins-group")).filter(function(s){var l=s.querySelector(".ins-label");return l&&l.textContent==="Vibe"})[0];return g})()'
before=$(j "(function(){var g=$vibe_sel;return g?String(g.querySelectorAll('.chip.is-on').length):'0'})()")
j "(function(){var g=$vibe_sel;if(!g)return '';var c=g.querySelector('.chip:not(.is-on)');if(c)c.click();return ''})()" >/dev/null
sleep 3
check "tag toggle persists" "$before" "$(j "(function(){var g=$vibe_sel;return g?String(g.querySelectorAll('.chip.is-on').length):'0'})()")"

before=$(j 'String(document.querySelectorAll(".bulkbar").length)')
j 'document.querySelector(".pick").click();""' >/dev/null; sleep 1
check "select raises bulk bar" "$before" "$(j 'String(document.querySelectorAll(".bulkbar").length)')"
j 'document.querySelector(".pick").click();""' >/dev/null; sleep 1

echo "== routes =="
for route in /collections /triage /activity; do
  "$B" goto "$APP$route" >/dev/null 2>&1; sleep 3
  title=$(j '(document.querySelector(".lib-title")||{textContent:""}).textContent')
  body=$(j 'String(document.body.innerText.length)')
  if [[ -n "$title" && "$body" -gt 120 ]]; then
    print -r -- "  [ok  ] $route renders: $title"; ((pass++))
  else
    print -r -- "  [FAIL] $route is empty"; ((fail++))
  fi
done

echo
print -r -- "passed $pass · failed $fail"
[[ $fail -eq 0 ]]
