#!/bin/zsh
# Put stem separation on the machine that serves the app.
#
#   scripts/install-stems.sh            # models + venv + verify
#   scripts/install-stems.sh --check    # report only, change nothing
#
# audio-separator is Ultimate Vocal Remover without the GUI: the same MDX and
# Demucs models, the same weights, driven from a command line. cr8 has always
# called it - the app was simply never given the two things it needs.
#
# Those two things are kept apart deliberately. The models are 137MB of binary
# weights that are gitignored and must be copied; the virtualenv is 1.2GB of
# torch that must be BUILT rather than copied, because a venv records absolute
# paths in its own scripts and its interpreter is a symlink into a specific
# Homebrew keg. Copying one between machines produces a directory that looks
# complete and cannot run - which is exactly the state the laptop is in now,
# after a Homebrew upgrade moved python out from under it.
set -uo pipefail

APP="${0:A:h:h}"
[[ -f "$APP/ops/env" ]] && source "$APP/ops/env"
REMOTE="${CR8_REMOTE:-${CR8_DEPLOY_REMOTE:?set CR8_DEPLOY_REMOTE}}"
RAPP="${CR8_REMOTE_APP:-${CR8_DEPLOY_APP_DIR:-~/cr8/Catalog}}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 "$REMOTE")
# Pinned to what the laptop resolved to, so the far side does not silently get
# a different separator than the one the recipes were tuned against.
PIN="audio-separator==0.44.5"

say() { print -r -- "  $*"; }

if [[ "${1:-}" == "--check" ]]; then
  say "models here:  $(du -sh "$APP/models/uvr" 2>/dev/null | cut -f1)"
  $SSH "export PATH=/opt/homebrew/bin:\$PATH
    print -r -- '  models there: '\$(du -sh $RAPP/models/uvr 2>/dev/null | cut -f1 || echo none)
    print -r -- '  separator:    '\$($RAPP/.venv-stems/bin/audio-separator --version 2>&1 | head -1)"
  exit 0
fi

say "--- copying the models (137MB, gitignored so git never carried them) ---"
# Plain -a and nothing else. macOS ships openrsync, which rejects --info and
# several other GNU rsync flags outright, and the failure looks like a usage
# message rather than an error - so the copy silently does not happen and the
# build carries on as though it had.
rsync -a -e 'ssh -o BatchMode=yes' \
  "$APP/models/uvr/" "$REMOTE:$RAPP/models/uvr/"
say "copied $(du -sh "$APP/models/uvr" | cut -f1)"

say "--- building the virtualenv on the far side ---"
$SSH "export PATH=/opt/homebrew/bin:\$PATH
set -e
cd $RAPP
# Prefer the version the laptop used. A venv is tied to the interpreter that
# made it, so picking whatever python3 happens to be first on PATH is how this
# breaks again the next time Homebrew upgrades.
PY=''
for v in 3.13 3.12 3.11; do
  if [ -x /opt/homebrew/opt/python@\$v/bin/python\$v ]; then
    PY=/opt/homebrew/opt/python@\$v/bin/python\$v; break
  fi
done
[ -z \"\$PY\" ] && PY=\$(command -v python3)
print -r -- \"  building with \$PY (\$(\$PY -V 2>&1))\"
rm -rf .venv-stems
\$PY -m venv .venv-stems
./.venv-stems/bin/pip install --quiet --upgrade pip
print -r -- '  installing $PIN and torch - this pulls about 1.2GB'
./.venv-stems/bin/pip install --quiet '$PIN'
print -r -- \"  separator: \$(./.venv-stems/bin/audio-separator --version 2>&1 | head -1)\"
"

say "--- verifying against what the app actually requires ---"
$SSH "export PATH=/opt/homebrew/bin:\$PATH
cd $RAPP && ./.venv/bin/python -c \"
from cr8.config import load_config
from cr8.stems import _required_paths
separator, models = _required_paths(load_config('config.toml'))
print('  OK - separator', separator)
print('  OK - models   ', models)
\""
