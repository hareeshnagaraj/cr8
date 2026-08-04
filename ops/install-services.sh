#!/bin/zsh
# Install LaunchAgents for the machine you are on.
#
#   ops/install-services.sh            # api + web
#   ops/install-services.sh --tunnel   # also tunnel
set -uo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$APP/ops/env" ]] && source "$APP/ops/env"
PREFIX="${CR8_LAUNCHD_PREFIX:-com.cr8}"
WITH_TUNNEL="${1:-}"
JOBS=(api web)
[[ "$WITH_TUNNEL" == "--tunnel" ]] && JOBS+=(tunnel)

for job in $JOBS; do
  src="$APP/ops/launchd/${PREFIX}.$job.plist"
  # templates are always com.cr8.* in tree; install under PREFIX
  tmpl="$APP/ops/launchd/com.cr8.$job.plist"
  dst="$HOME/Library/LaunchAgents/${PREFIX}.$job.plist"
  [[ -f "$tmpl" ]] || { echo "missing $tmpl"; continue; }
  sed -e "s|__HOME__|$HOME|g" -e "s|__APP__|$APP|g" -e "s|com\.cr8\.|${PREFIX}.|g" "$tmpl" > "$dst"
  launchctl unload "$dst" 2>/dev/null
  launchctl load "$dst" 2>/dev/null
  echo "  loaded ${PREFIX}.$job"
done

sleep 8
for job in $JOBS; do
  line=$(launchctl list | grep "${PREFIX}.$job")
  printf "  %-28s %s\n" "$job" "${line:-NOT RUNNING}"
done
