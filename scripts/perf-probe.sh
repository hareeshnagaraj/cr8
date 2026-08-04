#!/bin/zsh
# Measure whether the app still feels instant, and fail if it does not.
#
#   scripts/perf-probe.sh                 # measure, print, exit non-zero if over
#   scripts/perf-probe.sh --baseline      # record today's numbers as the baseline
#
# The budget it holds (specs/SPEC-motion-and-art.md):
#
#   keystroke -> repaint   p75 32ms, max 50ms
#   blocking per keystroke p75 0ms,  max 0ms
#   dropped frames on a 2s fling      2% / 5%
#   cumulative layout shift           0.02 / 0.05
#
# Only ever run against a production build. Development mode double-renders
# every component under StrictMode and the numbers are fiction.
set -uo pipefail

BASE="${0:A:h:h}"
B="${CR8_BROWSE_BIN:-$HOME/.claude/skills/gstack/browse/dist/browse}"
APP="${CR8_PERF_BASE:-http://127.0.0.1:3100}"
VIEWPORT="${CR8_PERF_VIEWPORT:-1440x900}"
ITERATIONS="${CR8_PERF_ITERATIONS:-12}"
MODE="${1:-check}"

[[ -x "$B" ]] || { echo "browse not built: $B"; exit 1; }
PW=$(cat "$BASE/secrets/owner-password.txt" 2>/dev/null) || {
  echo "no owner password file"; exit 1
}

# The browse daemon sandboxes file evals to the cwd it was STARTED from. If a
# previous session launched it from web/ (or anywhere else), the harness eval
# below is silently denied and the probe reports "no samples". Detect and
# relaunch it from the repo root — this trap has bitten twice.
cd "$BASE"
if "$B" eval "$BASE/scripts/perf_probe.js" 2>&1 | grep -q "Path must be within"; then
  "$B" stop >/dev/null 2>&1
  sleep 1
fi

"$B" viewport "$VIEWPORT" >/dev/null 2>&1
"$B" goto "$APP/login" >/dev/null 2>&1
sleep 2
"$B" fill "#username" "${CR8_SMOKE_USER:-owner}" >/dev/null 2>&1
"$B" fill "#password" "$PW" >/dev/null 2>&1
"$B" click "button[type=submit]" >/dev/null 2>&1
sleep 3

RESULTS="$BASE/reports/perf-samples.jsonl"
mkdir -p "$BASE/reports"
: > "$RESULTS"

echo "measuring $APP at $VIEWPORT, $ITERATIONS iterations"

for i in $(seq 1 "$ITERATIONS"); do
  "$B" goto "$APP/" >/dev/null 2>&1
  "$B" wait ".rows-viewport" >/dev/null 2>&1
  sleep 1
  "$B" eval "$BASE/scripts/perf_probe.js" >/dev/null 2>&1

  # Real keystrokes. Anything dispatched from JavaScript is isTrusted:false,
  # gets no interactionId, and never appears in Event Timing at all.
  # Two search fields exist in the DOM (desktop bar + phone sheet); an
  # ambiguous click errors and the keystrokes land nowhere, so focus the
  # visible one explicitly.
  "$B" js "([...document.querySelectorAll('.search, .mobile-search')].find((e) => e.offsetParent) || document.querySelector('.search')).focus()" >/dev/null 2>&1
  for key in s t a y h; do
    "$B" press "$key" >/dev/null 2>&1
    sleep 0.25
  done

  "$B" js "window.__cr8probe.fling(2000)" >/dev/null 2>&1
  sleep 2.5
  "$B" js "JSON.stringify(window.__cr8probe.report())" 2>/dev/null \
    | tail -1 >> "$RESULTS"
  printf "."
done
echo

python3 - "$RESULTS" "$BASE" "$MODE" <<'PY'
import json, statistics, sys, pathlib

samples_path, base, mode = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3]

rows = []
for line in pathlib.Path(samples_path).read_text().splitlines():
    line = line.strip()
    if not line.startswith("{"):
        # browse wraps some replies in quotes
        line = line.strip('"').replace('\\"', '"')
    try:
        rows.append(json.loads(line))
    except json.JSONDecodeError:
        continue

if not rows:
    print("no samples captured - is the app on a production build and signed in?")
    sys.exit(1)

# Discard the slowest quarter: the first paint after a cold navigation is real
# but is not what the budget is about.
def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]

keystrokes = [v for row in rows for v in row.get("inputToRender", [])]
blocking = [v for row in rows for v in row.get("blocking", [])]
dropped = [row["frames"]["droppedPercent"] for row in rows if row.get("frames")]
worst_gap = [row["frames"]["worstGapMs"] for row in rows if row.get("frames")]
cls = [row.get("cls", 0) for row in rows]

measured = {
    "samples": len(rows),
    "keystrokeSamples": len(keystrokes),
    "inputToRender_p75": round(percentile(keystrokes, 0.75), 1),
    "inputToRender_max": round(max(keystrokes), 1) if keystrokes else 0.0,
    "blocking_p75": round(percentile(blocking, 0.75), 1),
    "blocking_max": round(max(blocking), 1) if blocking else 0.0,
    "dropped_p75": round(percentile(dropped, 0.75), 1),
    "dropped_max": round(max(dropped), 1) if dropped else 0.0,
    "worstFrameGap_ms": round(max(worst_gap), 1) if worst_gap else 0.0,
    "cls_max": round(max(cls), 4) if cls else 0.0,
}

BUDGET = [
    ("inputToRender_p75", 32.0, "keystroke -> repaint p75"),
    ("inputToRender_max", 50.0, "keystroke -> repaint max"),
    ("blocking_max", 0.0, "blocking time per keystroke"),
    ("dropped_p75", 2.0, "dropped frames p75"),
    ("dropped_max", 5.0, "dropped frames max"),
    ("cls_max", 0.05, "layout shift"),
]

print()
for key, limit, label in BUDGET:
    value = measured[key]
    verdict = "ok  " if value <= limit else "OVER"
    print(f"  {verdict} {label:<32} {value:>8}  (budget {limit})")
print(f"       {'samples':<32} {measured['samples']:>8}"
      f"  ({measured['keystrokeSamples']} keystrokes measured)")

baseline_path = base / "reports" / "perf-baseline.json"
if mode == "--baseline":
    baseline_path.write_text(json.dumps(measured, indent=2) + "\n")
    print(f"\nbaseline written to {baseline_path}")
    sys.exit(0)

if baseline_path.is_file():
    baseline = json.loads(baseline_path.read_text())
    print("\n  versus baseline:")
    for key, _, label in BUDGET:
        was, now = baseline.get(key), measured[key]
        if was is None:
            continue
        delta = now - was
        arrow = "same" if abs(delta) < 0.05 else ("worse" if delta > 0 else "better")
        print(f"    {label:<32} {was:>7} -> {now:<7} {arrow}")

failures = [label for key, limit, label in BUDGET if measured[key] > limit]
if failures:
    print("\nOVER BUDGET: " + ", ".join(failures))
    sys.exit(1)
print("\nwithin budget")
PY
