# SPEC: safety spine (Phase 0/5) — backup, nightly job, monitoring

The irreplaceable data gets protected BEFORE features ship to the band. Some steps need the
owner (B2 account, jukebox provisioning, FileVault) — scripts land now with clear placeholders
in config; nothing destructive, ever.

## Backup (data-integrity rulings — all MUST)

- **Never point restic at the live DB.** Nightly, before restic:
  `sqlite3 catalog.db "VACUUM INTO '<backups>/catalog-YYYY-MM-DD.db'"`, then
  `PRAGMA integrity_check` on the copy — abort + alert if not "ok". restic excludes
  `catalog.db*` live files. Keep last 14 daily VACUUM copies locally.
- **Two independent restic repos on independent hardware:** Repo A = jukebox (sftp over
  Tailscale), Repo B = Backblaze B2. NEVER a repo on the corpus disk.
- **B2 key hygiene:** application key WITHOUT deleteFiles capability + bucket Object Lock;
  delete-capable master key stays offline (password manager). Prune runs from a different
  machine or not at all initially.
- **Retention with a long tail:** `--keep-daily 7 --keep-weekly 5 --keep-monthly 12
  --keep-yearly 10` (immutable audio dedupes; deep retention ≈ free).
- Backup targets: the corpus root (read-only source), catalog VACUUM copies, config/keymap,
  NOT the mirror (derived, counts as zero copies).
- Monthly: `restic check --read-data-subset=5%` on BOTH repos + restore drill (restore 20
  random files to scratch, sha256 vs catalog) — wired to the dead-man switch.
- Secrets: restic passwords + B2 keys via `security` CLI Keychain lookups inside the scripts —
  never in plists or env files. Document the one-time `security add-generic-password` commands.

## The ONE nightly LaunchDaemon (ops-collapse ruling)

`com.crate.nightly` (03:30, RunAtLoad missed-window catch-up) runs `cr8 nightly`, a single
orchestrator subcommand executing stages with per-stage try/except, flock lock, `set -euo
pipefail` semantics in Python, and a stage report:

scan → verify V1–V4/V7 → import-mik (idempotent) → fingerprint (incremental) → build (guarded)
→ push (guarded, when jukebox exists) → scrub (weekly rotation) → db VACUUM INTO + integrity →
restic A → restic B → verify V5/V8/V9 → write reports/digest-YYYY-MM-DD.md → macOS notification
(one line: "14 rated · 2 new hearts · coverage 100% · backups ✓") → healthchecks.io ping
**only if every stage succeeded** (a failed backup stage MUST fail the heartbeat).

Every failure mode degrades to STALE, never to wrong. Any stage that mutates (build/push) hard-
refuses while `pgrep -x Live` succeeds… EXCEPT read-only stages which are always safe.

Second plist: `com.crate.ingest` — WatchPaths on the corpus root + curated dirs (non-recursive
top-level firing is the feature: bounce drops fire it, project-guts churn doesn't),
ThrottleInterval 300 → `cr8 ingest-tick` (scan curated scope with the 120 s debounce → parse
→ resolve → incremental build → notify only on unparseable names). Its silent death degrades
exactly to the nightly.

Third plist: `com.crate.monthly` — restic checks + restore drill.

## Server/node hardening checklist (owner actions — emit as `cr8 doctor --setup` output)

- FileVault ON (`fdesetup status` check). No auto-login. UPS optional. Manual unlock after
  unplanned reboots is ACCEPTED (dead-man switch surfaces the outage within a day);
  `fdesetup authrestart` only for attended maintenance.
- Tailscale: server node gets tag:cr8 (tagged nodes: key expiry disabled by default), device
  approval + tailnet lock enabled. Bandmates are NEVER tailnet members.
- `pmset -c sleep 0; disablesleep 1` when serving moves to jukebox.
- healthchecks.io: two checks (nightly, monthly), ping URLs in Keychain-backed config.

## Acceptance

1. `cr8 nightly --dry-run` prints the full stage plan; real run executes read-only stages
   green today (backup stages skip with clear "not configured" until credentials exist).
2. VACUUM INTO + integrity_check proven by test (corrupt a scratch copy → abort path fires).
3. flock proven: second concurrent nightly exits 0 immediately with "already running".
4. Plists install via `cr8 install-launchd` (writes to ~/Library/LaunchAgents for now;
   LaunchDaemon migration documented for jukebox) and `launchctl print` shows them loaded.
5. Heartbeat logic: simulated failed stage → no ping sent (assert via mock).
