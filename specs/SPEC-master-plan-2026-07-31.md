# cr8 — Master Plan (v4 FINAL, post-spar + eng review + mobile mandate) — 2026-07-31

Five features, one day, one repo (`~/Music/Catalog`): FastAPI + SQLite (APSW, raw SQL) backend on :8080, Next.js 16 app on :3100 proxying to it, Tailscale Funnel as today's public path. 473 songs / 653 bounces. Two frontends coexist (legacy Jinja + Next port).

## Global decisions

- **D1 — Next-only UI.** All five features land in the Next app only. The Jinja app stays as-is (legacy owner console). No double implementation.
- **D2 — No new services.** Search stays SQLite. No Meilisearch/Typesense; at 653 tracks they are pure overhead.
- **D3 — New `invites` table, not `shares` revival.** `shares` has 9 stale rows and dead semantics (guest share links). Copy its *pattern* (ULID + token_sha256 + expires/max_uses/revoked_at) into a clean `invites` table; leave `shares` untouched.
- **D4 — Ordering.** Phase 0 foundations → 1 admin/invites → 2 to-listen → 3 search → 4 ingestion → 5 mobile parity → 6 Cloudflare walkthrough.
- **D5 — Never expose a day-old write path to the open internet (spar).** The Cloudflare domain goes up tonight gated behind **Cloudflare Access** (allowed emails: Hareesh + Henry). Access comes off later, deliberately. Tailscale Funnel stays live as fallback. During the soak, **invite links and the watcher use the Tailscale hostname** (eng review 5A) — invite generation takes a configurable base URL.
- **D6 — Snapshot before every migration (spar).** Each phase that bumps the schema starts with a `sqlite3 .backup` snapshot. Rollback = restore snapshot + git revert.
- **D7 — New endpoints live in per-feature router modules (eng review 6A):** `routes_admin.py`, `routes_assignments.py`, `routes_upload.py`, each with its own `APIRouter` included in `create_app()`; shared guards in one place. `routes.py` (2,844 lines) does not grow.
- **D8 — One token module (eng review 7A):** `cr8/web/common/tokens.py` — mint / digest (HMAC, file-key like sessions) / constant-time verify / expiry+max-use+revocation checks — with its own unit tests. Invites and upload tokens both consume it. No inline token code.
- **D9 — Mobile parity is a ship requirement (user mandate).** Every surface — existing and new — must be fully usable on a phone. New UI in Phases 1–4 is built responsive from the first commit (not retrofitted); Phase 5 retrofits the existing surfaces. The `ui_audit` gate runs at mobile viewports for every phase, not just Phase 5.

---

## Phase 0 — Foundations (roles + rate-limit fix)

Today `users.role` is CHECK-constrained to `owner|band` but nothing reads it, and `create_member()` hard-codes `'owner'` (`cr8/web/common/auth.py:126`). Everyone is a full admin. Separately, `RateLimitMiddleware._ip()` trusts client-supplied `X-Forwarded-For` unconditionally (`security.py:141-145`) — spoofable on the public Funnel today (eng review 1A: pulled forward from Phase 5).

1. `create_member()` takes a role param, default `'band'`.
2. `require_admin` guard (wraps `_require_owner`, checks `role == 'owner'`). Gate: `/members*`, `/tags` + `/tags/rewrite`, all new invite/admin/token endpoints.
3. Data fix: hareesh + henry → `role='owner'`; other users → `'band'`.
4. **Rate-limit fix:** `_ip()` honors `CF-Connecting-IP` (preferred) then `X-Forwarded-For` **only when the direct peer is a trusted proxy** (127.0.0.1 — the Next proxy / cloudflared); otherwise the socket peer IP. 
5. Tests: band 403 on admin routes; owner passes; unauthenticated 401. **[R2]** existing rate-limit behavior unchanged for direct connections; new: spoofed XFF from untrusted peer keys on socket IP. **[R4]** `/members` flow still works (band default, explicit owner).

## Phase 1 — Admin UI: invites + revocation

Backend (`routes_admin.py`):
- New table `invites(id, ulid, label, role, token_sha256, created_by, created_at, expires_at, max_uses, use_count, revoked_at)` — schema version bump in `cr8/web/common/schema.py`.
- `POST /api/admin/invites` → returns the full join URL **once** (raw token never stored). Base URL configurable (D5: funnel hostname during soak).
- `GET /api/admin/invites` → list with state (active/expired/exhausted/revoked).
- `POST /api/admin/invites/{ulid}/revoke`.
- `GET /join/{token}` (page) + `POST /api/join` — validates via `tokens.py`, invitee picks username/display/password, creates user with the invite's role, increments `use_count` inside the `BEGIN IMMEDIATE` mutation, starts a session. Next page sends the CSRF header — no middleware exemption.

Frontend: `/admin` (members table + role, remove member, invite list, create dialog with one-time copy-link, revoke), `/join/[token]` claim form, admin nav item gated by new `GET /api/me` (username + role).

Tests: tokens.py round-trip; expiry/max-use/revoke; join creates band user; admin gating; **concurrent redemption of last use → exactly one succeeds**; **taken username → clear error, token not consumed**; **POST /api/join without CSRF header → 403**; UI: double-click create makes one invite.

## Phase 2 — "To Listen" (assignments)

Backend (`routes_assignments.py`):
- New table `listen_assignments(id, ulid, bounce_ulid, song_id, assigned_to, assigned_by, note, state pending|heard|done|dismissed, created_at, heard_at, done_at)`. `assigned_to`/`assigned_by` = username, matching the actor convention (eng review 4A). **Member removal also deletes that user's pending assignments** (sent-by history survives).
- `POST /api/assignments` (bounce_ulids[], to, note). **Unknown username → 400; duplicate pending (same track+user) → no-op.**
- `GET /api/assignments` (mine, with track projection + sender + note), `POST .../{ulid}/done`, `.../dismiss`, `GET /api/assignments/count`.
- State machine (spar: no silent completion — trust in the homework list is the product):

```
             assign                    listen ≥ threshold                tap
  (none) ────────────▶ pending ──────────────────────────▶ heard ────────────▶ done
                          │        threshold: heard_s ≥ 60s   │
                          │        OR ≥50% duration for       │  card stays visible,
                          │        tracks under 120s          │  marked "listened ✓"
                          └──────────────── dismiss ──────────┴───▶ dismissed
```

- Auto-advance hook in the existing listen-progress path; **short-track (<120s) 50% rule tested explicitly**. Activity rows on assign + done.

Frontend: "Send to →" in Inspector + row overflow (member picker + note); nav "For You" with count badge (fetch on mount + 60s poll); `/for-you` cards (cover, title, from, note, age, state), Play All → PlayerProvider queue, per-item done/dismiss.

Tests: assign→list→count; cross-user isolation; auto-heard at threshold; heard≠done; dismiss; duplicate no-op; unknown-user 400; removal cleanup; UI: Play All fills the queue.

## Phase 3 — Search & filtering re-architecture

Goal: crate-digging feel — instant, fuzzy, forgiving. Current: FTS5 over title+slug only; era/key/hearted filters and ALL sorting in Python post-fetch (`queries.py:437-521`); `/api/facets` runs `library_songs()` 5×.

Server:
- Rebuild `songs_fts` as FTS5 **trigram**, indexing title, slug, aliases, notes, collab, concatenated tags; rewrite the 3 sync triggers + tag-change trigger; backfill migration.
- **Short-query fallback (eng review 2A): `len(query) < 3` → SQL `LIKE '%q%'` on title/slug** instead of MATCH (trigram matches nothing under 3 chars).
- Push era, key, bpm-range (`bpm_min`/`bpm_max`), hearted into SQL; all sorts into `ORDER BY`; covering indexes.
- `/api/facets` → single-pass aggregates.
- Coarse perf test at current scale (not a vanity p95 gate).

Client:
- **Client-side index**: one fetch of the full-catalog lightweight index (~150KB: ulid, title, tags, bpm, key, era, duration, status). Keystroke narrowing, BPM dual-range slider + histogram, facet toggles — all in-memory; URL params reflect state; server canonical for deep links.
- **Freshness (spar + eng review 9A):** index endpoint carries a catalog version + ETag. Mutations **patch the in-memory index optimistically**; one debounced refetch (~2s after the last mutation, 304 when unchanged); refetch on window focus.

Tests: trigger sync on song/tag/notes edits; short-query fallback; SQL/client parity on bpm+era+key+short queries; facet single-pass == old counts; ETag/304 + version bump; deep-link URL params reproduce state; 0-result empty state offers reset. **[R1]** all 10 sorts ±desc: SQL results == old Python results. **[R3]** every query matching today still matches post-trigram.

## Phase 4 — Shared drive ingestion (upload UI + Henry's auto-sync)

Uploads land in a new `drops/<username>/` root outside the read-only synced corpus (one-way sync must never see foreign files).

Backend (`routes_upload.py`):
- `POST /api/upload` (multipart). Auth: session OR per-user **upload token** (`api_tokens` table via `tokens.py`, revocable from /admin) — **checked from headers before any body read**.
- **Enforcement is mechanism, not intention (eng review 3A):** reject early on `Content-Length` when present; stream to `drops/` with a running byte counter aborting past 512MB; **sha256 computed incrementally in the same pass**; partial files cleaned up on abort. Extension whitelist from `[audio].extensions`; **filename sanitization tested against `../` traversal, unicode, 200+ chars**; dedupe by sha256.
- **Ingest poke with retry:** the ingest `FileLock` is non-blocking (`automation.py:115-118`) — on `LockBusy`, retry with backoff; upload status always derived from the DB, never from the poke. Belt-and-braces: launchd WatchPaths on `drops/`.
- Pipeline: drops root scanned as its own layer with uploader attribution; **unparseable filenames never bounce** — title-from-filename, status `idea`, collab=uploader, routed to review/triage.
- `GET /api/uploads` — recent uploads with status (pending/ingested/needs-review).

Frontend: `/upload` drag-drop multi-file with per-file progress + visible per-file errors; recent list.

Henry's watcher (4B): single-file stdlib-Python `crate-drop` script downloadable from `/admin` (launchd WatchPaths template, token baked in, **sends the `X-CR8-Request: 1` CSRF header**), local ledger of uploaded hashes (**tested: no re-upload**). Targets the **Tailscale hostname** (no CF 100MB cap, no Access wall).
- Stopgap, labeled: browser uploads >100MB through the CF domain fail until chunked upload exists (real fix: chunking, deferred).

Upload → cr8 flow:

```
 Henry's Mac                    catalog box
 ┌───────────────┐   HTTPS     ┌──────────────────────────────────────────────┐
 │ Ableton export│  (tailnet)  │ POST /api/upload                             │
 │  └─ watcher ──┼────────────▶│  auth(headers) → cap-stream → drops/henry/   │
 │     (launchd, │             │        │                        │            │
 │      ledger)  │             │        └─ poke ingest (retry on LockBusy)    │
 └───────────────┘             │                                 ▼            │
        browser drag-drop ────▶│  scan → resolve → mirror build (mp3/peaks/   │
                               │  art/rsgain) → library, collab=henry         │
                               │  unparseable → review_queue → /triage        │
                               └──────────────────────────────────────────────┘
```

Tests: 401-before-body; over-cap abort + cleanup; missing Content-Length; ext whitelist; sanitization; dedupe; token auth + revocation; LockBusy retry; drops ingest end-to-end; unparseable fallback; uploads status list.

## Phase 5 — Mobile parity (full responsiveness, all surfaces)

Goal: the whole app works one-handed on a phone. The Next app is desktop-first today (fixed left nav rail, right Inspector panel, hover-dependent row actions, `ui_audit` only runs 1440×900). New pages from Phases 1–4 arrive responsive (D9); this phase retrofits the rest.

Layout:
- Viewport meta + safe-area insets (`env(safe-area-inset-*)`) in the root layout.
- `Shell.tsx` (<768px): left nav rail → **bottom tab bar** (Library, For You, Upload, Activity, +Admin for admins); player dock docks above the tab bar with thumb-reachable transport.
- `Inspector.tsx` → slide-up **bottom sheet** on mobile (tag chips, share, stems all reachable); row overflow menu becomes a long-press/action sheet.
- `FilterRail.tsx` → filter **drawer** with a touch-sized BPM dual-range slider and facet chips; active-filter count badge on the trigger.
- Library rows: collapse to two-line layout (title + key/bpm/era chips), keep `@tanstack/react-virtual`, all targets ≥44px, no hover-only affordances.
- `Waveform.tsx`: touch scrubbing with an enlarged hit area.

Playback on iOS Safari:
- Play always originates from a user gesture (already true); the single `<audio>` element in `PlayerProvider` survives navigation (already true — keep it that way).
- **Media Session API**: lock-screen/notification transport (play/pause/next/prev) + track metadata + generated art.

Gate: `scripts/ui_audit.js` and the interaction sweep run at **390×844 and 768×1024** in addition to 1440×900 — covered-control, target-size, clipped-text checks must pass at all three; real-device check over the Funnel URL.

## Phase 6 — Cloudflare domain (guided walkthrough, together)

1. User-side (interactive): domain into Cloudflare, named tunnel + token in Zero Trust.
2. Box-side (scripted): install `cloudflared`, launchd service (KeepAlive), tunnel → `http://127.0.0.1:3100`, DNS CNAME.
3. **Cloudflare Access on (D5):** allow-policy = Hareesh + Henry emails. Invites + watcher use the funnel hostname during the soak (5A). Removing Access is a deliberate later step.
4. App verification behind CF: rate-limit keys on `CF-Connecting-IP` via the Phase 0 trusted-proxy logic; cookie `Secure`; Range/streaming pass-through; uploads <100MB OK on the domain, >100MB documented to the Tailscale hostname.
5. Keep Funnel live during cutover; extend `scripts/probe-public.sh`; document in `docs/DEPLOY.md`.

---

## Execution notes

- **⚠ Concurrent rename in flight:** while this plan was being reviewed, another session committed a package rename `cr8` → `cr8` (b39ba34, 1d9b2f0: package, imports, paths, CLI entry point; `CR8_` env vars with `CRATE_` fallback). Every `cr8/...` path in this plan should be read as `cr8/...` post-rename. **First act of execution: `git pull`/sync with that session, confirm the rename is complete (both `cr8/` and `cr8/` existed on disk at review time), and re-verify the quoted line numbers before editing.** Do not start Phase 0 with the rename half-landed.
- Each phase gates on its tests green (`pytest`) + `scripts/web-restart.sh` + a `ui_audit`/interaction-sweep pass before the next.
- Schema changes via the idempotent `migrate()` path + version bump; D6 snapshot first.
- Every new write endpoint (browser AND watcher) sends `X-CR8-Request: 1`.
- **Cut lines, in order:** (1) defer Henry's watcher (upload UI alone delivers the core value); (2) defer the facets single-pass rewrite; (3) defer server-side SQL sort/filter pushdown (client index delivers the felt speed) — **the regression suite [R1] lands with the pushdown, whichever day that is**; (4) Media Session API polish (lock-screen transport) can slip a day — the responsive layouts cannot (D9); (5) Phase 6 slides whole — Tailscale keeps working. **Never cut:** role enforcement before invites; the Phase 0 rate-limit fix; Access before the domain; heard≠done; the security-critical tests (sanitization, XFF, join race, CSRF, token revocation); mobile-viewport `ui_audit` gates.
- Adjacent debt flagged, not touched (eng review): `LIBRARY_SQL` per-row `version_count` correlated subquery (`queries.py:333-335`); zero JS tests on the Next app.

## Resolved decision log

- OD1 invites table: new table (D3). OD2 search: hybrid client-index + canonical server (freshness designed: ETag + optimistic patch + debounced refetch). OD3 drops: separate root outside corpus, own layer + attribution. OD4 upload auth: per-user token via shared tokens.py. OD5 large files: CF 100MB documented stopgap, watcher on Tailscale, chunking deferred.
- Spar verdicts rejected: "abort the day" (phases independently shippable with gates + cut lines); "roles are ceremony" (invites create third parties; enforcement is the gate); "domain is vanity" (explicit requirement; risk handled by D5).
- Eng review adoptions: 1A rate-limit fix now; 2A short-query fallback; 3A upload mechanisms; 4A username+cleanup; 5A funnel URLs during soak; 6A router split; 7A tokens.py; 8A all 14 gap tests + 4 regressions; 9A optimistic index patch.
- User mandate (post-review): D9 mobile parity — new Phase 5, Cloudflare renumbered to Phase 6.

## What already exists (reused, not rebuilt)

- Token pattern: sessions' mint→HMAC-digest→verify (`auth.py:48`) and the dead `shares` schema → extracted into `tokens.py`, consumed by invites + upload tokens.
- Ordered-list pattern: `collections`/`collection_items` → copied for `listen_assignments`.
- Actor-keyed per-user state: `reactions`/`listen_progress` convention → assignments use it (with removal cleanup).
- FTS5 + triggers (`schema.py:182-206`) → rebuilt with trigram, same external-content approach.
- Review/triage queue → absorbs unparseable uploaded filenames; no new "quarantine" system.
- Launchd automation + FileLock + WatchPaths (`automation.py`) → drops/ ingestion rides the same machinery.
- Activity feed + undo infra → assignment events emit into it.
- `probe-public.sh` / `go-public.sh` / `web-restart.sh` → extended for the CF domain, not replaced.

## NOT in scope (considered, deferred)

- Chunked/resumable uploads — CF 100MB cap documented; Tailscale path uncapped; revisit when a >512MB or browser-over-CF need is real.
- Removing Cloudflare Access (opening the domain to anonymous traffic) — deliberate later step after the soak, never today (D5).
- JS/TS test harness for the Next app — `ui_audit` + interaction sweep carry UI QA today; a Playwright suite is a separate day.
- Jinja app retirement — untouched legacy owner console; deciding its fate is not today's work.
- `LIBRARY_SQL` `version_count` correlated-subquery cleanup — adjacent debt, flagged not touched.
- Push notifications (APNs/email) for assignments — badge + activity feed suffice for a two-person crew; revisit if invitees grow.
- Spellfix/typo-correction beyond trigram — trigram covers substring+fuzzy; evaluate only if real queries miss.

## Failure modes (new codepaths)

| Codepath | Realistic failure | Test? | Handled? | User sees |
|---|---|---|---|---|
| Invite redeem | race on last use | yes (8A) | BEGIN IMMEDIATE count check | clear "invite exhausted" |
| Join page | expired/revoked token | yes | 4xx + message | clear error |
| Assignment auto-heard | scrub/preview false-positive | yes | threshold + heard≠done | card stays until tap |
| Badge poll | stale count after action | yes | refetch on action + 60s poll | momentarily stale, self-heals |
| Upload | disk-fill / oversize | yes (3A) | streamed cap + cleanup | per-file error |
| Upload | ingest lock busy | yes (3A) | retry/backoff + DB-derived status | pending → resolves |
| Upload | traversal filename | yes (8A) | sanitization | file lands only in drops/ |
| Search rebuild | short query empty | yes (2A) | LIKE fallback | results as expected |
| Sort pushdown | order drift vs old | yes [R1] | regression fixture | identical ordering |
| Rate limit | spoofed XFF | yes | trusted-peer logic | attacker throttled |
| CF cutover | all-traffic-one-IP throttle | yes | CF-Connecting-IP via trusted peer | no false 429s |
| Mobile audio | no lock-screen controls | manual | Media Session API | transport on lock screen |

No critical gaps: every identified failure mode has both a test and handling.

## Worktree parallelization

| Step | Modules touched | Depends on |
|---|---|---|
| P0 foundations | cr8/web/common/ (auth, security) | — |
| P1 invites | routes_admin.py, tokens.py, schema.py, web/app/admin+join | P0 |
| P2 assignments | routes_assignments.py, schema.py, web/app/for-you, components | P0 |
| P3 search | queries.py, schema.py (FTS), web/app/page, FilterRail | P0 |
| P4 uploads | routes_upload.py, automation.py, mirror/scan, web/app/upload | P0, tokens.py (P1) |
| P5 mobile retrofit | web/components (Shell, Inspector, FilterRail, Waveform) | P1–P4 UIs landed |
| P6 Cloudflare | deploy/, scripts/, docs/ | P0 (rate-limit), all soak-relevant |

Lanes after P0: **A:** P1 → P4 (tokens.py dependency) · **B:** P2 · **C:** P3. A/B/C can run in parallel worktrees; **conflict flag:** P1/P2/P3 all bump `schema.py` — serialize the migration-version bumps (trivial merge if coordinated). P5 after UI lanes merge; P6 last.

## Implementation Tasks

Synthesized from findings; checkbox as you ship.

- [ ] **T1 (P1)** — security — trusted-proxy `_ip()` fix + spoof/regression tests (Issue 1)
- [ ] **T2 (P1)** — auth — role param + `require_admin` + data fix + member tests (Phase 0)
- [ ] **T3 (P1)** — common — `tokens.py` mint/digest/verify + unit tests (Issue 7)
- [ ] **T4 (P1)** — admin — invites table/endpoints/`/admin`/`/join` + race/CSRF/username tests (Phase 1)
- [ ] **T5 (P1)** — assignments — table/endpoints/For You/badge + state machine + cleanup (Phase 2, Issues 4)
- [ ] **T6 (P1)** — search-server — trigram rebuild + short-query fallback + SQL pushdown + single-pass facets + [R1][R3] (Phase 3, Issue 2)
- [ ] **T7 (P1)** — search-client — client index + BPM slider + optimistic patch/ETag freshness (Phase 3, Issue 9)
- [ ] **T8 (P1)** — upload — endpoint with streamed cap/hash/sanitization + LockBusy retry + tests (Phase 4, Issue 3)
- [ ] **T9 (P1)** — pipeline — drops root scan/attribution + unparseable fallback + end-to-end test (Phase 4)
- [ ] **T10 (P2)** — watcher — `crate-drop` script + ledger + launchd template + /admin download (Phase 4B, first cut line)
- [ ] **T11 (P1)** — mobile — responsive retrofit + Media Session + mobile ui_audit gates (Phase 5, D9)
- [ ] **T12 (P1)** — deploy — cloudflared + Access + DNS + probe extension + DEPLOY.md (Phase 6, Issue 5)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | timed out (5m); Claude-subagent fallback interrupted by user |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 9 issues, 0 critical gaps, all resolved |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

Adversarial spar (Grok) ran pre-review: verdict "abort" rejected with reasons; 6 attack points absorbed (D5, D6, heard≠done, index freshness, cut lines, auth-before-body).

**VERDICT:** ENG CLEARED — ready to implement. Outside voice did not complete (Codex timeout, fallback interrupted); informational only, never gating.

NO UNRESOLVED DECISIONS
