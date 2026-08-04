#!/bin/zsh
# Push the corpus from the machine you produce on to the machine that serves it.
#
#   scripts/corpus-sync.sh            # sync
#   scripts/corpus-sync.sh --dry-run  # show what would move, change nothing
#   scripts/corpus-sync.sh --install  # run it every 10 minutes via launchd
#
# One direction only, always. This laptop is where Ableton writes and is the
# only authority on what the corpus contains; the far side is a copy that cr8
# reads and never modifies. Nothing ever comes back.
#
# Why rsync rather than Resilio or Syncthing: those are bidirectional by
# default, and a two-way sync pointed at a folder Ableton is writing into is a
# good way to lose a bounce. rsync is one-way by construction, needs nothing
# installed on either end, and writes to a temporary name before renaming - so
# the far side never sees a half-copied file, which matters because a truncated
# wav that got catalogued would look exactly like a real track.
set -uo pipefail

[[ -f "$(cd "$(dirname "$0")/.." && pwd)/ops/env" ]] && source "$(cd "$(dirname "$0")/.." && pwd)/ops/env"

SOURCE="${CR8_CORPUS:?set CR8_CORPUS to your local corpus root}"
REMOTE="${CR8_DEPLOY_REMOTE:?set CR8_DEPLOY_REMOTE}"
# Far-side corpus root must match that machine's config.toml — set explicitly.
DEST="${CR8_REMOTE_CORPUS:?set CR8_REMOTE_CORPUS}"
ARCHIVE_SOURCE_ROOT="${CR8_ARCHIVE_SOURCE_ROOT:-}"
ARCHIVE_DEST="${CR8_REMOTE_CORPUS_ARCHIVE:-}"
ARCHIVE_DIRS=(${=CR8_ARCHIVE_DIRS:-})
LOCK="${CR8_CORPUS_SYNC_LOCK:-$HOME/.cr8-corpus-sync}"
LOG="${CR8_CORPUS_SYNC_LOG:-$HOME/Library/Logs/cr8-corpus-sync.log}"
LABEL="${CR8_CORPUS_SYNC_LABEL:-com.cr8.corpus-sync}"
MODE="${1:-run}"

if [[ "$MODE" == "--install" ]]; then
  plist="$HOME/Library/LaunchAgents/$LABEL.plist"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$HOME/Music/Catalog/scripts/corpus-sync.sh</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><false/>
  <key>Nice</key><integer>10</integer>
  <key>LowPriorityIO</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
  echo "wrote $plist"
  echo "start with:  launchctl load $plist"
  echo "log:         $LOG"
  exit 0
fi

# Never let two syncs overlap. A second copy of a 122GB rsync while the first is
# still walking the tree is how you turn a background job into a foreground one.
# macOS has no flock(1), so the lock is an atomic mkdir.
if ! mkdir "$LOCK.d" 2>/dev/null; then
  echo "$(date '+%F %T') another sync is running, skipping"
  exit 0
fi
trap 'rmdir "$LOCK.d" 2>/dev/null' EXIT HUP INT TERM

# The lock above only knows about copies this script started. The first copy
# onto a new machine is run by hand and holds no lock, and it runs for hours -
# so look for any rsync already writing to the same destination and stand
# down. That lets this be installed while that first copy is still going: it
# skips every tick until the copy finishes, then takes over by itself.
if pgrep -fl rsync 2>/dev/null | grep -qF "$REMOTE:$DEST"; then
  echo "$(date '+%F %T') a copy to $DEST is already running, skipping"
  exit 0
fi

# Refuse to run against an empty or unmounted source. --delete plus a source
# that vanished is how a mirror becomes an empty directory on the far side.
DRY=""
[[ "$MODE" == "--dry-run" ]] && DRY="--dry-run"

echo "$(date '+%F %T') sync start${DRY:+ (dry run)}"

# -a preserves mtimes, which the catalogue's debounce depends on: it waits for a
# file to stop changing before ingesting, and that only works if the far side
# sees the real modification time rather than the time it was copied.
#
# nice and --bwlimit keep this out of the way of a session in progress; the
# whole point is that you never notice it running.
sync_leg() {
  nice -n 10 rsync -a --delete --partial --stats $DRY \
    --timeout=120 \
    --bwlimit=40000 \
    --exclude '.DS_Store' \
    --exclude 'Icon*' \
    --exclude '.*' \
    --exclude '*.asd' \
    --exclude 'Backup/' \
    -e 'ssh -o BatchMode=yes -o ConnectTimeout=15' \
    "$@"
}

count=$(find "$SOURCE" -maxdepth 2 -type f 2>/dev/null | head -50 | wc -l | tr -d ' ')
if [[ ! -d "$SOURCE" || "$count" -lt 5 ]]; then
  echo "$(date '+%F %T') main source looks empty ($count files) - refusing that leg"
  main_result=1
else
  sync_leg "$SOURCE/" "$REMOTE:$DEST/"
  main_result=$?
fi
if [[ $main_result -eq 0 ]]; then
  echo "$(date '+%F %T') main sync ok"
else
  echo "$(date '+%F %T') main sync failed ($main_result)"
fi

# These archives are static. The server was pre-seeded from these same source
# directories, so the first scripted pass is a no-op apart from metadata.
archive_sources=()
archive_ready=1
for directory in "${ARCHIVE_DIRS[@]}"; do
  source_path="$ARCHIVE_SOURCE_ROOT/$directory"
  archive_count=$(find "$source_path" -maxdepth 2 -type f 2>/dev/null | head -50 | wc -l | tr -d ' ')
  if [[ ! -d "$source_path" || "$archive_count" -lt 5 ]]; then
    echo "$(date '+%F %T') archive source looks empty: $source_path ($archive_count files) - refusing that leg"
    archive_ready=0
  fi
  archive_sources+=("$source_path")
done

if [[ $archive_ready -eq 1 ]]; then
  sync_leg "${archive_sources[@]}" "$REMOTE:$ARCHIVE_DEST/"
  archive_result=$?
else
  archive_result=1
fi
if [[ $archive_result -eq 0 ]]; then
  echo "$(date '+%F %T') archive sync ok"
else
  echo "$(date '+%F %T') archive sync failed ($archive_result)"
fi

# Both legs always get their chance under the same lock. Preserve a non-zero
# status from either one so launchd/log monitoring still reports the failure.
[[ $main_result -eq 0 ]] || exit $main_result
exit $archive_result
