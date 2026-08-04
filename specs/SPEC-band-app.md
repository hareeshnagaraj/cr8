# SPEC: cr8 band app (Phase 4–5) — the product

Two FastAPI apps over the catalog + mirror. This spec carries binding security architecture and
product rulings from an adversarial review — deviations from the MUST items are failures.

## Two-process split (MUST — architecture-level security ruling)

Tailscale Funnel path-scoping is NOT a boundary (tailscale/tailscale#10234: funneling one path
publishes the whole domain, and the ts.net hostname is public via CT logs). Therefore:

- **`crate-owner` app** — 127.0.0.1:8080, exposed ONLY via `tailscale serve`. Full library UI.
- **`crate-guest` app** — 127.0.0.1:8081, the ONLY thing `tailscale funnel` exposes. A separate
  ASGI process whose module imports ZERO owner routes — at the code level the owner app cannot
  be reached through the public origin. Guest app has NO search, NO browse, NO library endpoints:
  only share-scoped listen-through + tag/heart/verdict writes.
- Shared code in `cr8/web/common/` (db access, auth helpers, player partials) — but route
  modules are disjoint and the guest entrypoint must be import-audited (test greps that
  `cr8.web.owner` is never imported from `cr8.web.guest`).

## Stack (BOM — pinned, vendored, no node/build step)

- FastAPI (starlette **>=0.49.1** — CVE-2025-62727; assert at startup) + uvicorn + Jinja2.
- Vendored static: htmx 2.0.9, htmx-ext-sse 2.2.4, Alpine.js 3.x (owner app), wavesurfer.js
  7.11.0, SortableJS 1.15.x. Guest pages: Alpine **CSP build** or vanilla JS only (strict CSP).
  **All vendor JS and woff2 fonts are ALREADY DOWNLOADED in `vendor-cache/{js,fonts}` — copy
  from there into the app's static dir; do not fetch from the network.**
- SQLite: the existing catalog.db. Enforce SQLite >= 3.53.2 at startup (CVE-2026-11822/FTS5)
  or refuse to boot with a clear message. FTS5 search on owner app only; queries wrapped as
  escaped quoted-phrase literals, length-capped, OperationalError → generic 400.
- SSE via native EventSourceResponse: "poke then re-GET fragment" pattern, in-process asyncio
  queues; guest pages get a 30 s polling fallback attribute.
- Hand-written CSS (see Design). No Tailwind/Bootstrap/component library.

## Write discipline (MUST — data-integrity ruling)

- ALL mutations flow through the app's HTTP handlers (single-writer discipline). The CLI keeps
  its own write paths (scan/build) but never runs concurrently with long app transactions —
  app write transactions are milliseconds (`BEGIN IMMEDIATE`, busy_timeout=10000, bounded retry).
- UI acks: a chip/heart lights ONLY after commit confirmation (htmx response = the lit fragment).
  A swallowed SQLITE_BUSY is a bug: assert, log, alert.
- All reactions are per-user append-only rows; a session can only insert/soft-delete its OWN rows.

## Schema additions (migration)

```sql
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, display TEXT,
  role TEXT CHECK(role IN ('owner','band')), password_hash TEXT, created_at TEXT);
-- band members authenticate exactly like guests (long-lived named tokens) — password only owner
CREATE TABLE shares (
  id INTEGER PRIMARY KEY, ulid TEXT UNIQUE, label TEXT,          -- "sent to EJ 8/1"
  token_sha256 TEXT UNIQUE NOT NULL,                             -- raw token NEVER stored
  kind TEXT CHECK(kind IN ('listen_through','band')),
  scope_json TEXT NOT NULL,               -- SNAPSHOTTED ordered bounce-ULID list at mint time
  expires_at TEXT, max_uses INTEGER, use_count INTEGER DEFAULT 0,
  revoked_at TEXT, created_at TEXT);
CREATE TABLE sessions (id INTEGER PRIMARY KEY, sid_sha256 TEXT UNIQUE, share_id INTEGER,
  user_id INTEGER, guest_name TEXT, created_at TEXT, last_seen TEXT);
CREATE TABLE reactions (                  -- hearts + chips + verdicts, append-only
  id INTEGER PRIMARY KEY, bounce_ulid TEXT NOT NULL, song_id INTEGER,
  actor TEXT NOT NULL,                    -- 'owner' | share label+guest_name
  kind TEXT CHECK(kind IN ('heart','chip','verdict','note')),
  dim TEXT, value TEXT,                   -- chip: dim+value; verdict: value in gem/keep/archive
  created_at TEXT, deleted_at TEXT);
CREATE TABLE listen_progress (share_id INTEGER, bounce_ulid TEXT, actor TEXT,
  state TEXT CHECK(state IN ('unheard','heard','skipped')), heard_s REAL, updated_at TEXT,
  PRIMARY KEY(share_id, bounce_ulid, actor));
```

Verdicts/hearts inform; they NEVER auto-change songs.status (owner promotes deliberately).
Owner "gem" verdicts set songs.keeper (owner is special-cased).

## Auth (MUST)

- Owner: username+password (argon2 via `argon2-cffi` or scrypt stdlib), session cookie.
- Bandmates + guests: ONE mechanism — opaque `secrets.token_urlsafe(32)` links, sha256-stored,
  constant-time compare. Band tokens: kind='band', long-lived, named, revocable; bandmates
  NEVER join the tailnet.
- Token exchange: first GET renders a minimal shell; on first interactive action (name entry /
  play tap) POST exchanges token → HttpOnly+Secure+SameSite=Lax session cookie, then
  `history.replaceState` strips the token from the URL. Media/API endpoints accept the session
  cookie ONLY — a forwarded mp3 URL is worthless.
- Pre-auth HTML contains NO song titles/metadata (unfurl hygiene); `X-Robots-Tag:
  noindex,nofollow,noarchive`; `Referrer-Policy: no-referrer`; generic 401 shell at `/`.
- NOT single-use: use-count thresholds (bot prefetch tolerance), expiry 60–90 d, revocable,
  one-click "revert everything from this token" (soft-delete its reactions).
- Per-token quotas: req/min, distinct-tracks/hour, bytes/day → cutoff + owner alert row.

## Media serving (MUST)

Route `/m/<bounce-ulid>` → catalog lookup → absolute mirror path → Starlette FileResponse
(native Range). Client never supplies a path; `resolve(strict=True)` + `is_relative_to(mirror)`
containment; no StaticFiles mount over media. Guest app checks share-scope membership before
EVERY byte (metadata, peaks, art, audio). Cap Range header count/length in middleware;
per-IP/per-token rate limit middleware. Serve process runs as a dedicated low-privilege user
with read-only mirror access and NO access to the originals tree.

## CSRF/XSS (MUST)

Jinja2 autoescape everywhere; `|safe` on user text banned (grep test). Strict CSP on the guest
origin: `default-src 'self'`, no unsafe-inline/eval. All mutations POST + require header
`X-CR8-Request: 1`; zero state-changing GETs; `frame-ancestors 'none'`; no CORS credentials;
`X-Content-Type-Options: nosniff`. Notes length-capped, control chars stripped.

## Product surfaces

### Owner app (tailnet)

1. **Library** — the home. Fully valuable at ZERO tags (ruling): browse/search over parsed
   metadata (title, date, key, BPM, version chains, era) from day one. Song rows expand to
   version chains. Filter chip rows for every dimension (steal: LMS declare-then-filter).
   FTS5 search. Sort: recency default.
2. **Player** — persistent bottom bar (one `<audio>` element survives htmx navigation — wrap
   body swaps so the player node is never replaced; hx-boost with `hx-preserve` on the player).
   Media Session API (lock-screen controls, artwork). Wavesurfer 7.11 MediaElement mode over
   pre-computed peaks JSON + known duration — never client-decode. CBR 320 = clean seeks.
3. **Now-playing tag surface** — heart + ONE row of ≤6 frequency-sorted chips in the thumb zone;
   "more" opens a sheet with full vocab + status/collab editors. Tags never gate anything.
4. **Triage queue** — resumable gem/keep/archive, one tap per verdict, auto-advance, undo toast.
   Shows today's count only — NO global percent-complete anywhere (guilt-dashboard ruling).
5. **Shares admin** — mint listen-throughs: pick ≤7 tracks (soft warning above 7), label it,
   get the link; per-share dashboard: per-track heard/skipped/hearts/chips per actor (partial
   progress is success, not failure). Revoke button. Band token management.
6. **Batch ops** — desktop multi-select → set status/instr/collab in bulk (this is where those
   dims live; NOT on the per-listen surface). CSV round-trip stays in the CLI.
7. **Activity** — reverse-chron feed of reactions (who hearted/tagged what), SSE-fresh.

### Guest app (funneled)

Exactly one flow: open link → minimal shell → type first name (stored per-session, shown to
owner) → listen-through: batch title, "6 songs · ~18 min", then track-by-track: big play,
waveform, heart, ≤6 chips, optional short note, next/skip. Server-side per-actor resume (ANY
device, same link resumes mid-batch — progress keyed to share+actor, not localStorage).
Skip is first-class. End screen: "thanks — EJ heard 5, hearted 2." Nothing else exists on
this origin.

### Playback rulings (MUST)

Plain `<audio>` + Media Session; play starts only from a tap (iOS autoplay); ship a manifest
but do NOT prompt add-to-home-screen (iOS 26 installed-PWA audio broken through 26.2); no Web
Audio in the playback path on iOS (AudioContext dies on lock) — ReplayGain via GainNode only on
desktop/Android UAs, via `TXXX:REPLAYGAIN_*` values exposed in track JSON.

## Design intent (RATIFIED — binding)

The design system was forged and ratified: read **`canon/ratifications/2026-07-29-crate-design.md`**
(tokens, era palette, signature moves, motion, voice, banlist) and use the mockups as the
visual reference implementation:
- Owner app = **B Studio Vault**: `canon/references/mockups/B-vault-balanced.html` (chrome,
  list, player, tag bar) and `B-vault-pole.html` (song-detail version rail, guest-page calm).
- Song detail also grafts the **spec-grid metadata block** and the **UNHEARD red signal
  system** from `A-console-balanced.html`.
- Guest listen-through = **C Flyer poster treatment**: `C-flyer-pole.html` screen 3 (FOR EJ
  poster, setlist rows, ticket-punch verdict stamps) with B's interaction calm underneath.
Fonts: Instrument Sans + IBM Plex Mono (+ Archivo Black ONLY on the guest poster headline),
self-hosted woff2 vendored into static/ (download at build setup; no runtime Google Fonts).
Reuse the mockups' CSS decisions (ladders, gradients, chip construction, waveform bars,
era-cover fields) rather than re-deriving them. The ratification's banlist is binding.

## Ops (MUST)

- Two LaunchDaemons (owner + guest uvicorn) + `tailscale serve 8080` / `tailscale funnel 8081`.
  (Runs on the studio Mac for now; migration to jukebox later is a config change — document.)
- Secrets (session signing key) in a 0600 file under ~/Music/Catalog/secrets/ (Keychain
  integration optional later); never in plists.
- Nightly external probe script (curl from the funneled origin) asserts owner routes 404/401
  publicly; wire into verify.
- `pip-audit` wired into verify. Version headers suppressed.

## Acceptance

1. pytest green: token lifecycle (mint/exchange/revoke/expiry/use-count), scope enforcement
   (guest cannot fetch out-of-scope ULID: metadata, peaks, art, audio → 403/404), import audit
   (guest never imports owner routes), CSRF header required, FTS escaping, reactions
   append-only + own-rows-only, listen progress resume, media path containment (symlink +
   poisoned-row attempts), chip commit-ack flow.
2. Playwright (or httpx+manual) smoke: owner login → browse at zero tags → play → heart+chip →
   triage 3 tracks → mint a 3-track share → open in fresh session → guest flow end-to-end →
   owner sees per-track progress + reactions attributed.
3. Both processes boot under launchd; `tailscale serve`/`funnel` configured (funnel activation
   may be deferred until security checklist done — leave the command documented).
4. Zero writes to corpus; guest process user cannot read the originals tree (test with `sudo -u`).
5. Lighthouse mobile pass on the guest page ≥ 90 performance; total JS < 200 KB vendored.
