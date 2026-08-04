#!/bin/zsh
# Walk every page of the app and report craft violations that are PROVABLE:
# covered controls, clipped text, small targets, inflated chips, radius/font
# sprawl, contrast failures, sideways scroll, heavy DOM.
#
#   scripts/ui-audit.sh            # audit every page
#   scripts/ui-audit.sh /tags      # audit one page
#   CR8_AUDIT_VIEWPORT=390x844 scripts/ui-audit.sh    # audit as a phone
set -uo pipefail

BASE="$HOME/Music/Catalog"
B="$HOME/.claude/skills/gstack/browse/dist/browse"
APP="${CR8_AUDIT_BASE:-http://127.0.0.1:8080}"
PAGES=("$@")
[[ ${#PAGES[@]} -eq 0 ]] && PAGES=(/ /tags /collections /triage /members)

[[ -x "$B" ]] || { echo "browse not built: $B"; exit 1; }

PW=$(cat "$BASE/secrets/owner-password.txt" 2>/dev/null) || {
  echo "no owner password file"; exit 1
}

# The phone is not a smaller desktop: a control that is reachable at
# 1440x900 can be under the player dock or off the edge at 390x844.
VIEWPORT="${CR8_AUDIT_VIEWPORT:-1440x900}"
"$B" viewport "$VIEWPORT" >/dev/null 2>&1
"$B" goto "$APP/login" >/dev/null 2>&1
sleep 2
"$B" fill "#username" "${CR8_SMOKE_USER:-owner}" >/dev/null 2>&1
"$B" fill "#password" "$PW" >/dev/null 2>&1
"$B" click "button[type=submit]" >/dev/null 2>&1
sleep 3

AUDIT=$(cat "$BASE/scripts/ui_audit.js")
REPORT="$BASE/reports/ui-audit.md"
mkdir -p "$BASE/reports"
: > "$REPORT"
print -r -- "# UI audit" >> "$REPORT"
print -r -- "" >> "$REPORT"

total_high=0
for page in $PAGES; do
  "$B" goto "$APP$page" >/dev/null 2>&1
  sleep 2
  raw=$("$B" js "$AUDIT" 2>/dev/null | tail -1)
  print -r -- "$raw" | python3 "$BASE/scripts/ui_report.py" | tee -a "$REPORT"
done

print -r -- "" >> "$REPORT"
echo
echo "report: $REPORT"
