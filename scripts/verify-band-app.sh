#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${CR8_PYTHON:-"$project_dir/.venv/bin/python"}

cd "$project_dir"

"$python_bin" -m pytest
"$python_bin" -m compileall -q cr8 tests

if rg -n '\|\s*safe' cr8/web; then
  echo "Jinja safe-filter audit failed" >&2
  exit 1
fi

plutil -lint ops/launchd/com.cr8.owner.plist

"$python_bin" -m pip_audit --progress-spinner off

if [ -n "${CR8_PUBLIC_ORIGIN:-}" ]; then
  "$project_dir/scripts/probe-public.sh" "$CR8_PUBLIC_ORIGIN"
else
  echo "public probe: SKIP (CR8_PUBLIC_ORIGIN is not configured)"
fi
