# Operator tools

Everything here is for **running your own crate**, not for day-to-day app development.

Contributor scripts (gate, probes) live in `scripts/`. This folder holds deploy,
sync, tunnel, and launchd templates.

## Topology (default)

| Surface | Port | Notes |
|---------|------|--------|
| Next.js public face | `127.0.0.1:3100` | what users hit |
| FastAPI owner app | `127.0.0.1:8080` | loopback only; Next proxies |

Set `CR8_PUBLIC_ORIGIN` to your public HTTPS origin when you put a tunnel in front.

## Configure identity (required)

Copy the example and fill in **your** hosts — never commit the filled file:

```sh
cp ops/env.example ops/env
# edit ops/env
```

`ops/env` is gitignored. Scripts source it automatically when present.

| Variable | Purpose |
|----------|---------|
| `CR8_DEPLOY_REMOTE` | `user@host` for ssh deploy |
| `CR8_DEPLOY_APP_DIR` | app path on the remote |
| `CR8_LAUNCHD_WEB` / `CR8_LAUNCHD_API` | launchd labels to kickstart |
| `CR8_SMOKE_ORIGIN` | live URL for smoke tests |
| `CR8_SMOKE_USER` | login used by smoke/gate probes |
| `CR8_CORPUS` / `CR8_REMOTE_CORPUS` | corpus-sync paths |

## Deploy

```sh
ops/deploy.sh              # or: scripts/deploy.sh (wrapper)
ops/deploy.sh --web-only
```

## LaunchAgents

Templates: `ops/launchd/com.cr8.*.plist` (labels `com.cr8.api`, `com.cr8.web`, …).

```sh
ops/install-services.sh
ops/install-services.sh --tunnel
```

## keyfinder-cli

Not vendored (architecture-specific). Install or build `keyfinder-cli` and put it
on `PATH`, or leave key enrichment optional — `cr8` skips missing tools cleanly.
