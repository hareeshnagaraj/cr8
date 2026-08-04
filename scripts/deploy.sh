#!/bin/zsh
# Contributor-facing entry: run the operator deploy script under ops/.
# Host/user/launchd names come from env or ops/env (gitignored) — never hardcoded.
exec "$(cd "$(dirname "$0")/.." && pwd)/ops/deploy.sh" "$@"
