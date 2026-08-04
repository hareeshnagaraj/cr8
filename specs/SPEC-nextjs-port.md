# SPEC — Next.js port (definitive)

Status: **authoritative build plan.** Supersedes the earlier sketch of the same name in full.
Date: 2026-07-30. Owner: Hareesh.
Assembled from three drafted parts on 2026-07-30; where they disagreed, the conflict is resolved in
place rather than left for the build to discover.

This document specifies one thing: replacing the Jinja/htmx view layer of the owner app with a single
Next.js App Router client at `apps/web`, in the visual idiom of the Samply audit. The Python
pipeline, the SQLite catalog, and the FastAPI auth, media and download surfaces are untouched — they
become a JSON API, and Caddy on loopback puts both apps behind one origin so the session cookie, the
undo stack and the Range-backed byte paths never change. §1–§4 fix the goal, the runtime topology,
the dependency set and the design tokens; §5–§6 specify every screen and enumerate feature parity
line by line; §7–§9 give the thirteen-step build order, the acceptance gates and the residual risks;
§10 lists the decisions still open to the owner.

Authority where documents conflict: `canon/references/samply-audit.md` (the design brief), then
`specs/SPEC-craft.md` (binding craft rules), then `specs/SPEC-r6-ui-fixes.md` (defects that must not
return), then `canon/ratifications/2026-07-29-crate-design.md` (superseded where §4 says so).

Two conflicts are settled here so they are not relitigated mid-build:

- **Repo shape.** r6 §7 deletes the share/guest surface and `docs/DEPLOY.md` describes a single
  authenticated ASGI app. There is no second consumer, so there is no monorepo. **One Next app at
  `apps/web`.**
- **List rendering.** Ship `content-visibility: auto` over all 472 rows first. TanStack Virtual is a
  measured escalation with a named trigger (§5.2), not a default.

---

## 1. Goal and non-goals

### Goal

Replace the Jinja/htmx view layer with a Next.js App Router client in the visual idiom of the Samply
audit, without changing a byte of the Python pipeline or the catalog schema.

The port exists for one technical reason. This is a stateful client: a persistent `<audio>` element
that must survive navigation, a queue with a cursor, optimistic tag writes, and an inspector that
follows playback. htmx is server-rendered fragment swapping, which is its weakest case. Four real
bugs shipped from that mismatch: a hidden queue that disagreed with the visible list, row controls
painted underneath row titles, a player that needed `hx-preserve` gymnastics to survive navigation,
and a 438,767-byte library page. An App Router root layout holds the audio element and the Zustand
queue store above the routed content, so navigation swaps the page inside a player that never
remounts. That is the whole thesis.

The second goal is visual. The current build is inflated and colliding. The audit is the correction:
one sans family, sentence case, flat fills, white as the only accent, a genuine table with a gutter
play affordance, an inline version subtree, a fixed right rail per row, `Listen | Manage`, settings
as grouped rows, and a grid library built from era covers we already generate.

### Non-goals

- **The Python pipeline is untouched.** `cr8 scan/parse/resolve/detect/build/verify`, the filename
  parser, the entity resolver, Mixed In Key import, keyfinder/aubio, chromaprint, the mp3-320 mirror
  with peaks and covers and ReplayGain, UVR separation, restic, and the launchd automation all stay
  exactly as built.
- **Python remains the only writer to `catalog.db`.** A data-integrity ruling, not a preference. Next
  never opens the database, never holds a SQLite handle, never runs a migration.
- **The FastAPI auth model is unchanged.** No second auth path, no token exchange, no NextAuth, no
  session library in Node. The `crate_owner_sid` HttpOnly cookie stays the only credential.
- **Media serving is unchanged.** `/m`, `/peaks`, `/art`, `/download/*` keep their Starlette
  implementations and their path-containment checks. Node never touches a media byte.
- **No guest app and no share links in v1.** `shares`, `share_id = 0` and `landing_collection_id`
  stay as vestigial columns. Share geometry is settled in `specs/DEFERRED.md`; it is built after
  cutover.
- **No new columns, tables, or schema version bump.** Every new endpoint is a projection over
  existing rows.
- **No CI.** One Mac. The deploy loop is a shell script and `launchctl kickstart`.

---

## 2. Runtime topology

### 2.1 Shape

Three loopback processes behind one front door. **Every process in this stack binds `127.0.0.1` and
nothing else, Caddy included.**

```
tailnet TLS, terminated by tailscaled
  https://<machine>.ts.net:8443   build phase, tailnet only
  https://<machine>.ts.net:443    after cutover, Tailscale Funnel, public internet
        │
  Caddy 2 · 127.0.0.1:8443 · plain HTTP, does no certificate work
        ├─ /api/* /m/* /peaks/* /art/* /download/* /static/* /login /logout /healthz
        │     → FastAPI · 127.0.0.1:8080   (unchanged process, unchanged plist)
        └─ everything else
              → Next · 127.0.0.1:3100      (output: 'standalone')

  FastAPI alone reaches catalog.db + mirror/ + stems/. Python is the only writer.
```

| Process | Bind | Reachable from | Supervisor |
|---|---|---|---|
| FastAPI (uvicorn) | `127.0.0.1:8080` | Caddy | `com.cr8.cr8-owner.plist`, unmodified |
| Next standalone | `127.0.0.1:3100` | Caddy | `com.cr8.cr8-web.plist`, new |
| Caddy 2 | `127.0.0.1:8443` | tailscaled | `com.cr8.cr8-caddy.plist`, new |
| tailscaled | tailnet, then `:443` Funnel | the internet, after cutover | Tailscale |

`Caddyfile`, in full, in its post-12a state. Step 0 ships this same file with the catch-all
pointed at 8080 and moves prefixes to 3100 as pages are built (§2.2, step 2):

```
{
	servers {
		trusted_proxies static 127.0.0.1/32 ::1/128
	}
}

http://127.0.0.1:8443 {
	@fastapi path /api/* /m/* /peaks/* /art/* /download/* /static/* /login /logout /healthz
	handle @fastapi {
		reverse_proxy 127.0.0.1:8080
	}
	handle {
		reverse_proxy 127.0.0.1:3100
	}
}
```

Four things in that file are load-bearing.

**`http://127.0.0.1:8443`, never `:8443`.** A bare site address means `0.0.0.0:8443` *and*
`[::]:8443`, which publishes a cleartext front door onto whatever Wi-Fi the machine is on. The
`Secure` cookie is not sent over that origin, so the symptom reads as "everything 401s" rather than
"I am exposed". It also collides with the `tailscale serve --https=8443` of §2.5; both can hold the
port only because they hold different addresses. Step 0's acceptance is `lsof -nP -iTCP:8443
-sTCP:LISTEN` showing the loopback address alone, with `tailscale serve status` showing the proxy up
at the same moment.

**`trusted_proxies static 127.0.0.1/32 ::1/128`.** `RateLimitMiddleware._ip()` reads the *first*
`X-Forwarded-For` entry and both tailscaled and Caddy append rather than replace, so without this a
client picks its own rate-limit bucket by sending its own XFF. That bucket is the only thing between
a publicly funneled `/login` and an offline-speed password guess.

**`/static/*` routes to 8080.** The Jinja templates load `/static/owner.css` and `/static/owner.js`.
While any Jinja route is mounted, which is through step 12 and a month beyond it, they must reach
8080 or the pages that still render will render unstyled. Deleted with the templates, not before.

**No `/activity/events` handle**, because v1 opens no `EventSource` (§5.11).

**The routing invariant.** *Every request the Next client issues carries an `/api/` prefix, or is one
of the media/download paths above.* That rule is why this file is fourteen lines and stays correct,
and it is why writes get `/api` siblings in step 1b instead of Caddy method matchers over `/songs/*`:
`POST /songs/{ulid}/tags/toggle` and the Next route `/songs/[song_ulid]` are the same path, and
`handle` matches on path only.

### 2.2 Why the extra process earns its keep

**Node never touches a media byte.** The archive is 121 GB and every seek is a Range request.
Starlette's `FileResponse` already answers 206 with `Content-Range`, handles multipart ranges and
416s an unsatisfiable one, verified end to end. A Next route handler or `rewrite` in that path
inserts a Node hop that buffers, can drop `Accept-Ranges` on the way back, and turns every seek into
a re-proxied stream. Caddy's `reverse_proxy` passes Range through untouched.

**One origin means the auth problem disappears.** Page and API share scheme, host and port, so the
browser attaches the HttpOnly cookie to every `fetch` under the default `credentials: "same-origin"`.
Next needs no auth code, no cookie forwarding, no token, no session store. There is no
`CORSMiddleware` and none will be added: credentialed CORS would force the cookie to
`SameSite=None`, and `SameSite=Lax` is the actual CSRF defence on the one CSRF-exempt path.

**Cutover and reversal are each one line.** Caddy arrives in step 0 with everything routed to 8080,
byte-identical to today. Prefixes move to 3100 as pages are built, and Jinja keeps serving the rest
on the same origin, sharing the same session and undo stack.

### 2.3 Auth and CSRF

Credential mechanics are unchanged. Login argon2-verifies against `users.password_hash`, inserts a
`sessions` row keyed by `HMAC-SHA256(raw_sid, secrets/owner-session.key)`, and sets `crate_owner_sid`
(HttpOnly, SameSite=Lax, Secure, Path=/, Max-Age=30d). Sessions do not slide. `session.username` is
the actor string written into `reactions`, `listen_progress` and `song_tags.author`;
`session.session_id` scopes the undo stack, so the Jinja and Next apps share one undo stack during
the side-by-side phase, which is correct rather than coincidental.

Who serves the login *page* changes, and only at the end. Through step 11 Caddy routes `/login` to
8080 and Jinja serves it, because the old app is still the front door on 443. Step 10 adds the `/api`
siblings (`POST /api/session`, `DELETE /api/session`, `POST /api/setup`) with the same handler bodies
and the same argon2 work. Step 12a drops `/login` and `/logout` from the `@fastapi` matcher and Next
owns `GET /login` and `GET /setup`. Splitting `/login` by method inside Caddy is rejected: a method
matcher on an auth path is the kind of config that is subtly wrong for a month.

`POST /api/setup` needs **no** CSRF exemption. The Next setup page submits JSON through the same
fetch wrapper as every other write, so it carries `X-CR8-Request: 1` (§5.13), and
`CSRFMiddleware.EXEMPT_PATHS` stays `/login` alone. The bug this avoids is live today: `/setup` is not
exempt and the Jinja submit handler bails out on auth pages (no `#cr8-player` dock to hang the header
on), so that form posts natively and gets `403 request rejected`. It has no test, and the port must not
inherit it — it is fixed by Next owning the page and posting through the wrapper, not by widening the
exemption list.

Every mutating request carries `X-CR8-Request: 1` or `CSRFMiddleware` returns a bare `403
text/plain`, which reads exactly like an auth bug. Prevented structurally: one fetch wrapper at
`apps/web/src/lib/api.ts` adds the header to every non-GET, it is the only transport TanStack Query
mutations may use, and an ESLint rule bans bare `fetch(` outside it.

Reads are client-side, never server-side, which keeps the cookie flowing natively and keeps
`RateLimitMiddleware` seeing real client IPs instead of collapsing the app into one `127.0.0.1`
bucket. `proxy.ts` (Next 16's renamed `middleware.ts`) tests only for cookie *presence* and redirects
to `/login`: a UX convenience, never a boundary. FastAPI re-queries `sessions JOIN users` on every
request and remains the only thing that authorises a byte.

### 2.4 Exposure and limits

After cutover this machine publishes port 443 to the public internet through Tailscale Funnel. Every
number below is stated so it is not adjusted casually.

| Control | Value | Where | Change |
|---|---|---|---|
| Global rate limit | **240 req/min per IP**, one window | `CRATE_IP_REQUESTS_PER_MINUTE` | **unchanged** |
| Rate-limit exempt prefixes | `/static/`, `/m/`, `/peaks/`, `/art/` | `security.py` | add three, step 0 |
| Login budget | 10/min and 60/hour per **submitted username** | new, `POST /login` + `/api/session` | new, step 0 |
| XFF trust | `127.0.0.1/32`, `::1/128` only | Caddyfile `trusted_proxies` | new, step 0 |
| CSRF header | `X-CR8-Request: 1` on every non-GET | `CSRFMiddleware` | unchanged |
| CSRF exempt paths | `/login` only | `CSRFMiddleware.EXEMPT_PATHS` | **unchanged**, see §2.3 |
| Range header | ≤200 chars, ≤4 ranges, else 416 | `RangeLimitMiddleware` | unchanged |
| Selection ZIP | 50 files / 2 GB | download route | unchanged |
| Session lifetime | 30 days, no sliding refresh | `sessions` | unchanged |

**The cap is not raised.** The problem the raise was for is a 472-row list whose covers each hit
`/art`, and the exemption solves that completely. Going from 240 to 1200 would be a 5× increase in
the password-guessing budget against a CSRF-exempt `/login` on a publicly funneled machine, and there
is exactly one window with one global limit: no per-path budget, no per-account budget, no lockout,
no backoff after a failed argon2 verify. The exempt prefixes are byte routes behind an authenticated
session, and the rate limiter was never what protected them.

**The login budget is keyed on the username, not the IP**, because the IP key is attacker-chosen
until `trusted_proxies` lands and attacker-rotatable afterwards, while the submitted username cannot
be rotated. Exhaustion returns the same generic `401 Login failed.` copy, so the endpoint still
reveals nothing.

All four new lines land in **step 0**: none depends on anything else in the plan, and the cover storm
arrives in step 4 with the table, not in step 9 with the grid.

### 2.5 Development

`next dev` on 3100, reached at `https://<machine>.ts.net:8443` through the same Caddy front door as
production, so dev is single-origin and the cookie behaves identically:

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:8443
```

`CRATE_COOKIE_SECURE=true` means a plain `http://localhost:3100` dev server never receives the
session cookie, and the symptom is a silent wall of 401s. Do not set `CRATE_COOKIE_SECURE=false` on
the running production process to work around it.

Next 16 blocks cross-origin requests to dev-only assets by default, and `next dev` initialises on
`localhost:3100` while you reach it at the `.ts.net` name, so without configuration every
`/_next/static/chunks/*` request is refused and the dev app loads nothing at all. One key in
`next.config.ts`:

```ts
allowedDevOrigins: ['<machine>.ts.net'],
```

Dev-only, no production effect, so "dev and prod differ in zero files" still holds: one key, both
environments, same file. `next.config.ts` carries no rewrites for `/api`, `/m`, `/peaks` or `/art`;
Caddy owns routing in both. It also sets `outputFileTracingRoot: __dirname`, because the directory is
literally named `apps/web` and a stray lockfile at the Catalog root silently relocates the output to
`.next/standalone/apps/web/server.js`, breaking the hardcoded plist path with no build error.

### 2.6 Processes and deploy

**Node.** The Mac runs Node 23.11.0, an odd-numbered line EOL since mid-2025, on a machine that
publishes 443. Replace it with Node 24 LTS (24.18.1) first, by absolute path in the plist: launchd
jobs get no shell profile, so nvm and mise shims do not resolve.

**`ops/launchd/com.cr8.cr8-web.plist`** (new): `/opt/homebrew/opt/node@24/bin/node` +
`<catalog>/apps/web/current/server.js`; `PORT=3100`, `HOSTNAME=127.0.0.1`, `NODE_ENV=production`;
`UserName: crateowner`; `RunAtLoad`, `KeepAlive`, `ThrottleInterval: 5`; `logs/web{,-error}.log`.

**`ops/launchd/com.cr8.cr8-caddy.plist`** (new). Caddy is the single front door for every byte in
the system and is not installed on this machine today. Unsupervised, the first reboot or crash is a
simultaneous outage of audio, API and UI with the funnel pointed at a dead port, so it gets the
supervision uvicorn already has: absolute path to the binary, `run --config <abs>/Caddyfile`,
`RunAtLoad`, `KeepAlive`, `ThrottleInterval: 5`, `logs/caddy{,-error}.log`. `brew services start
caddy` is acceptable only if genuinely enabled at boot; a hand-started `caddy run` in a terminal is
not. Step 0 accepts on `sudo launchctl kickstart -k system/com.cr8.cr8-caddy` followed by a
Range `curl` through 8443, and health checks point at 8443 so the monitor covers the new hop.

The owner and stems-worker plists are not modified.

**`scripts/deploy-web.sh`**: `pnpm install --frozen-lockfile` → `pnpm build` → copy `.next/static`
and `public` into `.next/standalone` (which `next build` does not do) → move to
`apps/web/releases/<timestamp>` → assert `test -f releases/<ts>/server.js` → boot that release on a
scratch port and `curl --fail` it → **only then** repoint the `apps/web/current` symlink → `launchctl
kickstart -k system/com.cr8.cr8-web` → `curl --fail` through 8443. The health check precedes
the symlink flip: a bad build must be an aborted deploy, not a live outage. Keep the previous two
releases; rollback is a symlink swap plus a kickstart. Never run `next build` from inside the launchd
job.

### 2.7 Cutover and reversal

Cutover is **two commits**, because three independent things change and a single-command revert only
undoes one of them.

**12a, the app moves and the funnel does not.** Drop `basePath: '/app'` and move the Caddy catch-all
from 8080 to 3100. The old app stays on 443 through the funnel the whole time, so this is verified at
leisure at `https://<machine>.ts.net:8443/` with nothing at risk. This commit touches every internal
link, every `router.push`, `proxy.ts`'s redirect target, and every URL assumption in the
sessionStorage-persisted queue. It is where the bugs are, and it reverts by reverting one file.

**12b, the funnel moves.** One line, one variable:

```sh
tailscale funnel --bg --https=443 http://127.0.0.1:8443    # was 8080
```

**Reversal.** Put the Caddy catch-all back on 8080. That restores the Jinja app on 443 and 8443 at
once and leaves the side-by-side rig alive for diagnosis. Repointing the funnel back at 8080 is
faster but leaves `:8443` serving a Next app with `basePath` already dropped, which kills the rig at
exactly the moment it is needed. Either way FastAPI, the database, the mirror and the session cookie
are untouched, so a revert loses nothing but the new UI.

---

## 3. The stack

| Concern | Choice | Version | Why |
|---|---|---|---|
| Runtime | Node 24 LTS, absolute path in the plist | 24.18.1 | Active LTS; installed 23.x is EOL on a publicly funneled machine |
| Package manager | pnpm, one app, no workspace | 10.4.1 | Nothing to separate once guest is gone |
| Framework | Next.js App Router, TypeScript, Turbopack, `output: 'standalone'` | 16.2.12 | A persistent root layout is the entire justification for the port |
| UI runtime | React / react-dom | 19.2.8 | Next 16 target. `<Activity/>` preserves state and DOM but **not scroll offsets** (it hides with `display:none`), so §5.3 restores `scrollTop` explicitly. `useEffectEvent` separates audio side effects from reactive state |
| Styling | Tailwind v4, CSS-first, `@tailwindcss/postcss` | 4.3.3 | Tokens live once as real custom properties; no JS config, no drift, native `oklch` |
| Class merge | `clsx` + `tailwind-merge` | 2.1.1 / 3.6.0 | The whole utility layer is one `cn()` |
| Primitives | Radix Primitives via `radix-ui`, hand-styled with our tokens | 1.6.7 | Portal, Popper collision detection, FocusScope and DismissableLayer make r6 §1 and §2 structurally impossible rather than merely fixed |
| Table | None. CSS Grid rows, fixed template columns | 0 deps | Sorting and filtering are server-side because the rail needs live counts; a headless table would be adapters disabling the features it was installed for |
| Long lists | `content-visibility: auto`; TanStack Virtual only on the §5.2 trigger | 3.14.9 if needed | Measured escalation, not a default |
| Client state | Zustand, one store, `"use client"`, audio element a module singleton outside React | 5.0.14 | The queue is one state machine; Media Session, `ended` and key handlers call `getState().next()` without a hook |
| Server state | TanStack Query against same-origin `/api/*`, client-side only | 5.101.4 | `onMutate` → snapshot → `setQueryData` → rollback → invalidate is exactly optimistic tagging plus honest undo. No Server Actions: a second server tier in Node whose only job is to re-authenticate and forward is pure surface |
| Waveform | One hand-rolled canvas renderer over pre-generated peaks, at dock and hero size | 0 deps | Never client-decode. Click-to-seek is `audio.currentTime = (e.offsetX / rect.width) * audio.duration` |
| Motion | None. CSS transitions on `var(--ease)`, plus Radix's `data-state` contract for overlay exits | 0 deps | Radix stamps `data-state="open\|closed"` and waits for the CSS animation before unmounting: exit animation with no dependency and no `forceMount` coordination |
| Drag | None in v1. `Move up` / `Move down` in the `…` menu plus the keyboard path | 0 deps | Three dependencies for the plan's own most fragile interaction, against one collection in the database. The menu path is ~20 lines onto the same permutation endpoint |
| Types | `openapi-typescript` → `src/lib/api-types.ts` | build script | End-to-end safety across the Python/TypeScript boundary, but only under the `response_model` rule below |

Explicitly not installed: shadcn/ui (copies opinionated styles a ratified system would spend the
project fighting; read `ui.shadcn.com` for how to wire a Radix part and install nothing), sonner,
cmdk, vaul, react-hot-toast, any form library, `next-themes` (dark-only), TanStack Table (v8 frozen
since 2025-04-14, v9 shipping betas daily), React Compiler (forces Babel into the build; revisit
after the app is measured), `motion`, `wavesurfer.js`, `@dnd-kit/*`, `@atlaskit/pragmatic-*`.

**`next/image` is banned**, which needs saying because it is the default reflex for 472 covers. The
optimizer makes Node a byte path, contradicting the topology this port rests on, and `/_next/image`
issues its own server-side fetch with no cookie jar against `/art` routes behind `_require_owner`, so
every cover 401s and the symptom reads as an auth bug. `sharp` is not a Next 16 dependency either.
Plain `<img>` with explicit `width`/`height`, `loading="lazy"`, `decoding="async"`, and an ESLint rule
banning the import alongside the bare-`fetch(` rule. `next/font` remote loaders are banned for the
same reason; fonts are self-hosted woff2.

**`wavesurfer.js` is not installed.** In 7.12.11 the constructor reads `this.options.url ||
this.getSrc()`, `getSrc()` returns the shared `<audio>` element's `currentSrc`, and it loads
immediately if that is non-empty. Constructing a strip before an async `/peaks` fetch resolves
therefore pulls the whole mp3-320 over the tailnet and runs it through `decodeAudioData`, which is
what "never client-decode" forbids and what the iOS AudioContext regression makes non-negotiable.
Guarding that correctly costs more than the one-line click handler it buys.

**`response_model` is binding, not aspirational.** There is not one `response_model` or `BaseModel`
in `cr8/web/` today; routes return bare dicts or `JSONResponse`, FastAPI emits an empty response
schema for those, and `openapi-typescript` renders it as `unknown`, against which "no `any`" passes
vacuously. So every `/api` route declares a Pydantic response model, and models are written **only
for endpoints the current step ships**. `openapi_url` is `None` in `cr8/web/owner/app.py`, so the
generator calls `create_app().openapi()` directly with `CRATE_CORPUS_ROOT` and friends set and dumps
to a file at build time. The schema is never exposed at runtime.

`cacheComponents` / PPR stays off: every byte on every screen is per-user private data behind a
session cookie, fetched client-side, so there is no static surface to prerender and the caching model
would only add staleness bugs.

Radix does not violate the "no component library look" rule, because it ships zero styles. The
templated look the banlist forbids comes from shadcn's class strings, not Radix behaviour.
"Hand-built primitives, no shadcn/MUI" prohibits borrowed *styling*; it is not a mandate to
reimplement focus trapping and collision-aware positioning, which is precisely how r6 §1 and §2
happened.

---

## 4. Design tokens

Paste verbatim into `apps/web/src/app/tokens.css`, imported once from the root layout before
`@import "tailwindcss"`. There is no second copy of these values anywhere, and no component may
introduce a colour, radius, duration or size literal.

**The `@theme` block aliases; it never re-exports.** This file is authoritative CSS and works through
`var()` on its own. Tailwind v4 reserves `--text-*` for font sizes and `--color-*` for colours, and
this set deliberately uses `--text-1..4` for the colour ladder and `--text-display`…`--text-micro`
for the size ladder. Two meanings on one namespace is fine in plain CSS and catastrophic inside
`@theme`: dropping `--text-2: oklch(1 0 0 / .62)` in there emits `.text-2 { font-size: oklch(…) }`,
and because no colour lives under `--color-*`, re-exporting the palette produces zero colour
utilities. `--space-*` is not a v4 namespace either; v4 drives spacing from a single `--spacing`
multiplier. So `globals.css` contains aliases only:

```css
@theme {
  --color-text-1:    var(--text-1);
  --color-text-2:    var(--text-2);
  --color-surface-1: var(--surface-1);
  --color-era:       var(--era);
  /* …and so on. The only --text-* entries are the size ladder. */
}
```

Spacing stays `var()`-only. A stylelint rule asserts that no numeric `--text-N` appears inside
`@theme`.

```css
/* ===========================================================================
   CRATE — design tokens (port baseline, 2026-07-30)

   Authority order where sources conflict:
     1. canon/references/samply-audit.md      (2026-07-30, authoritative)
     2. specs/SPEC-craft.md                   (binding, checkable)
     3. canon/ratifications/2026-07-29-crate-design.md
   Core correction from the audit: ONE sans family, sentence case, WHITE-only
   accent, FLAT fills. No monospace webfont. No gradient elevation.
   Colour is OKLCH throughout. Nothing in the 280-330 hue band (no purple).
   =========================================================================== */

:root {
  color-scheme: dark;

  /* -- 1. SURFACES ---------------------------------------------------------
     Flat fills only. Elevation comes from a 1px ring or a real divider,
     never a gradient, never a border-as-structure, never backdrop-filter.
     Cool cast is 0.004 chroma at hue 255 (blue) — perceptually neutral. */
  --ground:              oklch(0.163 0.004 255);  /* page, ~#0e0e10 */
  --surface-1:           oklch(0.200 0.004 255);  /* cards, panels, ~#161618 */
  --surface-2:           oklch(0.225 0.004 255);  /* inputs, chip rest, ~#1c1c1e */
  --surface-3:           oklch(0.262 0.004 255);  /* raised / chip hover, ~#242426 */
  --surface-sunken:      oklch(0.140 0.004 255);  /* slider + waveform troughs */
  --sticky-bg:           var(--ground);           /* sticky surfaces are OPAQUE */
  --scrim:               oklch(0.10 0 0 / 0.72);  /* modal backdrop */

  /* -- 2. TEXT LADDER ------------------------------------------------------
     Four rungs. 1 / .48 / .28 are the ratified values and are preserved;
     .62 is the audit's observed secondary and is added, not substituted.
     --text-4 is DECORATION ONLY (2.4:1) — never body, never under 12px. */
  --text-1:              oklch(1 0 0 / 1);        /* titles, values        */
  --text-2:              oklch(1 0 0 / 0.62);     /* secondary body 7.7:1  */
  --text-3:              oklch(1 0 0 / 0.48);     /* labels, captions 4.9:1*/
  --text-4:              oklch(1 0 0 / 0.28);     /* decoration 2.4:1      */
  --text-placeholder:    var(--text-3);
  --text-on-accent:      oklch(0.145 0 0);

  /* -- 3. ACCENT -----------------------------------------------------------
     White is the only accent. Link-blue is banned everywhere, including the
     one inline `Add version` link the audit observed at #4a7dff. */
  --accent:              oklch(1 0 0);
  --accent-fill:         oklch(1 0 0 / 0.92);     /* primary pill fill     */
  --accent-fill-hover:   oklch(1 0 0 / 1);
  --accent-ink:          oklch(0.145 0 0);        /* text on a white fill  */

  /* -- 4. HAIRLINES + RINGS ------------------------------------------------ */
  --hairline:            oklch(1 0 0 / 0.08);
  --hairline-strong:     oklch(1 0 0 / 0.13);
  --cover-outline:       oklch(1 0 0 / 0.1);      /* SPEC-craft §3, exact  */

  /* -- 5. INTERACTION FILLS ------------------------------------------------
     Row background is reserved for transient state (hover/selection).
     Playing is NOT a background — see --era thread + weight, below. */
  --fill-quiet:          oklch(1 0 0 / 0.06);
  --fill-quiet-hover:    oklch(1 0 0 / 0.10);
  --fill-quiet-active:   oklch(1 0 0 / 0.14);
  --row-hover:           oklch(1 0 0 / 0.05);
  --row-selected:        oklch(1 0 0 / 0.09);
  --row-selected-hover:  oklch(1 0 0 / 0.13);

  /* -- 6. ERA HUES (quarantined) -------------------------------------------
     Permitted ONLY on: generated cover fields, the 3px inset row thread,
     and the active-chip underline. Never text, never a selection colour. */
  --era-pelicana:        oklch(0.72 0.15 25);     /* 2023-24 rose   */
  --era-nova1:           oklch(0.78 0.13 195);    /* 2024-25 teal   */
  --era-working:         oklch(0.86 0.16 115);    /* 2026 chartreuse*/
  --era-unknown:         oklch(1 0 0 / 0.14);     /* undated: NEUTRAL, not a hue */
  --era:                 var(--era-unknown);      /* per-row override */

  /* -- 7. SIGNAL (UNHEARD only) --------------------------------------------
     Fill or dot only. Never used as text on the ground (fails the L>0.9 rule)
     and never rendered within 24px of an era-rose cover field. */
  --signal:              oklch(0.60 0.22 27);
  --signal-ink:          oklch(0.145 0 0);

  /* -- 8. WAVEFORM (pre-generated peaks, never client-decoded) ------------- */
  --wave-unplayed:       oklch(1 0 0 / 0.16);
  --wave-buffered:       oklch(1 0 0 / 0.26);
  --wave-played:         oklch(1 0 0 / 0.92);
  --wave-cursor:         oklch(1 0 0 / 0.92);

  /* -- 9. STATUS ----------------------------------------------------------- */
  --status-error:        oklch(0.72 0.17 27);
  --status-error-bg:     oklch(0.60 0.22 27 / 0.14);
  --status-ok:           var(--text-2);           /* success is quiet, not green */

  /* -- 10. TYPOGRAPHY ------------------------------------------------------
     ONE family. Instrument Sans, variable, self-hosted woff2, 400/500/650.
     --font-mono is a SYSTEM stack, never loaded, and is permitted only for
     column-aligned digits if Instrument Sans ships no `tnum`, and for literal
     file paths in Settings. Never for labels, chips, dates, keys, durations. */
  --font-sans: "Instrument Sans", ui-sans-serif, system-ui, -apple-system,
               "Helvetica Neue", Arial, sans-serif;
  --font-numeric: var(--font-sans);   /* + font-variant-numeric: tabular-nums */
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;

  --weight-regular:      400;
  --weight-medium:       500;
  --weight-semi:         650;

  --text-display:        34px;  /* page title, sentence case          */
  --text-title:          22px;  /* section / panel title              */
  --text-heading:        17px;  /* card + group heading               */
  --text-body:           15px;  /* prose, descriptions                */
  --text-ui:             14px;  /* table rows, controls, nav — default*/
  --text-caption:        13px;  /* chips, meta lines                  */
  --text-micro:          12px;  /* HARD FLOOR. Never with --text-4.   */
  --text-input:          16px;  /* 16px always — iOS zooms below this */

  --leading-display:     1.1;
  --leading-title:       1.15;
  --leading-heading:     1.3;
  --leading-body:        1.55;
  --leading-ui:          1.35;

  --tracking-display:    -0.02em;
  --tracking-title:      -0.015em;
  --tracking-body:       0em;
  --tracking-label:      0.055em;  /* RESERVED: guest poster only.
                                      The owner app is sentence case. */

  /* -- 11. SPACING (4px base) ---------------------------------------------- */
  --space-0:   0px;
  --space-05:  2px;
  --space-1:   4px;
  --space-2:   8px;
  --space-3:   12px;
  --space-4:   16px;
  --space-5:   20px;
  --space-6:   24px;
  --space-8:   32px;
  --space-10:  40px;
  --space-12:  48px;
  --space-16:  64px;

  /* -- 12. RADIUS (concentric: inner = outer − padding) --------------------
     Sanctioned nestings, exact:
       panel  16 / pad 8 → card    8
       card   12 / pad 4 → control 8
       control 8 / pad 4 → tick    4
     A parent and its child must never share a radius. */
  --radius-xs:    2px;
  --radius-sm:    4px;
  --radius-md:    8px;   /* controls, chips, inputs, small covers */
  --radius-lg:    12px;  /* cards, large covers                  */
  --radius-xl:    16px;  /* panels, modal, sheet                 */
  --radius-full:  999px; /* pills, avatars, round icon buttons   */

  /* -- 13. SIZE + LAYOUT ---------------------------------------------------
     Breakpoints (documented; @media cannot read custom properties):
       >=1400  rail 220 | list fluid (min 640) | inspector 380
       1120-1399  rail + list, inspector becomes a right overlay
       900-1119   rail collapses to a 56px icon strip
       <900       phone stack, rail becomes a filter sheet */
  --topbar-h:            56px;
  --player-h:            80px;   /* 72-88 band; never >10% of viewport */
  --rail-w:              220px;
  --rail-w-collapsed:    56px;
  --inspector-w:         380px;
  --list-min-w:          640px;
  --modal-w:             470px;

  --row-h:               44px;   /* default, fixed, single line */
  --row-h-comfortable:   56px;   /* density toggle              */
  --row-gutter-w:        36px;   /* the #/play column           */
  --row-tail-w:          132px;  /* fixed right rail, never overlaps title */
  --header-row-h:        34px;
  --tag-row-h:           40px;

  --control-h-sm:        28px;   /* fine pointer, 40px hit via ::before */
  --control-h:           32px;   /* chips, segmented, selects           */
  --control-h-md:        40px;   /* desktop hit floor, SPEC-craft §5    */
  --tap-min:             44px;   /* enforced under @media (pointer:coarse) */

  --icon-sm:             14px;
  --icon-md:             18px;
  --icon-lg:             22px;

  --cover-sm:            36px;
  --cover-md:            48px;
  --cover-lg:            96px;
  --cover-hero:          220px;

  --safe-top:            env(safe-area-inset-top, 0px);
  --safe-bottom:         env(safe-area-inset-bottom, 0px);

  /* -- 14. MOTION ----------------------------------------------------------
     The frequency gate (SPEC-craft §6a): anything triggered 100+ times a day
     does NOT animate. Chip toggle, j/k cursor, play/pause = --dur-0. */
  --ease:                cubic-bezier(0.32, 0, 0.16, 1);   /* ratified default */
  --ease-glyph:          cubic-bezier(0.2, 0, 0, 1);       /* SPEC-craft §8    */
  --ease-exit:           cubic-bezier(0.32, 0, 0.67, 1);   /* exits are softer */

  --dur-0:               0ms;    /* instant state change      */
  --dur-1:               90ms;   /* opacity settle            */
  --dur-2:               150ms;  /* hover, colour             */
  --dur-3:               180ms;  /* default transition        */
  --dur-4:               240ms;  /* panel / drawer enter      */
  --dur-exit:            120ms;
  --stagger:             100ms;  /* semantic chunks only, never list rows */

  /* Motion primitives — these are what prefers-reduced-motion flips. */
  --press-scale:         0.97;   /* 0.95-0.98 band, every pressable thing */
  --travel:              8px;    /* any translate distance                */
  --glyph-scale-from:    0.25;
  --glyph-blur-from:     4px;

  /* -- 15. ELEVATION -------------------------------------------------------
     Dark-mode recipe: a single ring. The 3-layer stacked shadow is the
     light-mode recipe and is not used here. */
  --ring:                0 0 0 1px oklch(1 0 0 / 0.08);
  --ring-hover:          0 0 0 1px oklch(1 0 0 / 0.13);
  --shadow-overlay:      0 12px 32px oklch(0 0 0 / 0.55),
                         0 0 0 1px oklch(1 0 0 / 0.08);
  --shadow-dock:         0 -8px 24px oklch(0 0 0 / 0.45);

  /* Focus. Two-tone so it survives a white chip fill AND an
     overflow:hidden ancestor. Always visible, never removed. */
  --focus-halo:          0 0 0 2px oklch(1 0 0 / 0.92);
  --focus-inner:         2px solid var(--ground);
  --focus-simple:        2px solid oklch(1 0 0 / 0.92);  /* unclipped controls */
  --focus-offset:        2px;

  /* -- 16. Z-INDEX --------------------------------------------------------- */
  --z-list:    0;
  --z-sticky:  10;
  --z-dock:    20;
  --z-drawer:  30;
  --z-popover: 40;
  --z-modal:   50;
  --z-toast:   60;
}

/* Reduced motion = GENTLER, NOT ZERO (SPEC-craft §14).
   Colour and opacity transitions survive; travel and scale go to nothing.
   A user who asks for reduced motion still needs to see their chip toggle. */
@media (prefers-reduced-motion: reduce) {
  :root {
    --press-scale:      1;
    --travel:           0px;
    --glyph-scale-from: 1;
    --glyph-blur-from:  0px;
    --stagger:          0ms;
    --dur-4:            150ms;
  }
}

/* Coarse pointer: every interactive target reaches 44px, by hit area if not
   by visible size. Adjacent hit areas must never overlap. */
@media (pointer: coarse) {
  :root {
    --control-h-sm: var(--tap-min);
    --control-h:    var(--tap-min);
    --row-h:        var(--row-h-comfortable);
  }
}

html {
  font-family: var(--font-sans);
  font-size: var(--text-ui);
  font-variant-numeric: tabular-nums;   /* set once, inherited */
  font-synthesis: none;                 /* a missing weight must fail visibly */
  background: var(--ground);
  color: var(--text-1);
  -webkit-font-smoothing: antialiased;
}
```

### 4.1 What this changes about the shipped app

Token-level re-assignments and retirements, each with its reason. Composition corrections (row tail,
gutter play affordance, inspector subject) live in §5 and §6.

| Change | Reason |
|---|---|
| IBM Plex Mono removed as a loaded webfont; dates, keys, camelot, durations, version labels and counts move to Instrument Sans with inherited `tabular-nums` | The audit's single biggest tell: mono as a personality shortcut reads as a terminal, not a music app. `--font-mono` survives unloaded, for column-aligned digits only if Instrument Sans ships no `tnum` (verify before build) and for literal file paths in Settings |
| Uppercase microlabels at +0.055em retired; everything is sentence case | One typographic grammar. `--tracking-label` is kept but reserved for the guest poster surface |
| Body drops from 17px to a 14px UI default with 15px prose; inputs stay 16px | 17px is a phone reading size and is much of why the desktop build reads inflated. 16px inputs stop iOS zooming |
| Gradient elevation (#242424 → #101010) retired entirely, including the "surfaces ≤160px" carve-out | Flat fills plus one 1px ring and real hairline dividers. Gradients-as-fills are on the anti-slop list |
| Ground darkens to `oklch(0.163 0.004 255)`; the old #191919 becomes `--surface-1` | A re-assignment, not a retirement. It restores the ground/card separation a single-value ground never had |
| Text ladder extended from three rungs to four (1 / .62 / .48 / .28) | Both ratified values survive; .62 is the audit's observed secondary. .28 measures 2.4:1 and is now formally decoration only. This supersedes the proposed `--t-label` at .40, which failed body contrast at the 12px floor |
| White is the only accent; the audit's one blue inline link is deliberately not adopted | Link-blue stays banned. Links are `--text-1` with a hairline underline |
| Chips 32px, keeper a 32px segmented row, library and collection rows 44px, tags rows 40px | r6 §3 measured ~60px chips and ~130px tag rows. Acceptance: 15+ tags and 15+ collection tracks visible at 1440×900 |
| The blanket `*{transition:none!important}` reduced-motion kill is replaced by flipping `--press-scale`, `--travel`, `--glyph-scale-from`, `--glyph-blur-from` | Reduced motion means gentler, not zero, and no component needs its own branch |
| `era_for_date(None)` maps to `--era-unknown`, a neutral white-alpha field | 75 undated songs (16%) currently render chartreuse, making "we have no date" the loudest signal on screen. No era colour may be assigned by a fallback path |
| A signal dot never renders within 24px of a rose cover field at comparable size | `--signal` sits 2° of hue from `--era-pelicana`; adjacent, the quarantine reads as decoration |
| Sticky surfaces are fully opaque (`--sticky-bg` = `--ground`); no `backdrop-filter` | Glassmorphism is banned, and opacity solves the ghosting it was meant to solve |
| Rail facet groups get real scrollbars (`scrollbar-gutter: stable`, `overscroll-behavior: contain`) | The mockups' `scrollbar-width: none` makes most of the 24 camelot values unreachable with a mouse |
| `--press-scale` is scoped away from full-width table rows and scoped into anchors | An 820px row scaling reads as a glitch; the filter rail is anchors and currently has no press feedback at all |

---

## 5. Screen-by-screen composition

### 5.0 The shell

`app/layout.tsx` is a server component holding the token stylesheet, the self-hosted `@font-face`
declarations, and one client `<AppShell>`: a 100dvh grid,
`grid-template-rows: var(--topbar-h) 1fr var(--player-h)`. The dock is the third row, never
`position: fixed` with a transform, because a non-`none` transform makes an element a containing
block for every fixed descendant and would anchor overlays to the dock instead of the viewport.
Nothing is fixed except Radix portals.

| Viewport | Middle row |
|---|---|
| ≥1400 | rail 220 \| list (fluid, 640 floor) \| inspector 380 |
| 1120–1399 | rail \| list, inspector as a right overlay |
| 900–1119 | rail collapses to a 56px icon strip |
| <900 | phone stack; rail and inspector both become bottom `Sheet`s |

The list is the hero and gets the width. Never cap the content column: the shipped app renders
everything in ~620px and leaves two thirds black.

Held above the router outlet and never remounted: the `<audio>` element, the Web Audio graph, the
Zustand store (`"use client"`, never imported by a server component), the Query client and its 401
handler, Media Session, and the `document`-level keyboard map.

**The audio module is a singleton and the graph is built once.** `lib/audio.ts` owns the element,
the `AudioContext`, the `GainNode` and the single `createMediaElementSource` call behind
`let graph: AudioGraph | null = null`. A second call on the same element throws `InvalidStateError`,
and an element routed into a suspended context plays no sound with nothing in the UI to say why, so
StrictMode and Fast Refresh must not be able to reach it twice. It is built only from inside a real
user gesture, in the same handler that arms the silent-WAV unlock, with `ctx.resume()` on every
play. Level match defaults off, its toggle is disabled until the graph exists, and the skip is any
Safari, feature-detected: Web Audio on macOS Safari also takes the element off the native output
path and breaks AirPlay.

**TopBar**, 56px, one typographic grammar, sentence-case sans for nav and actions: brand · Library ·
Tags · Collections · Settings, then `Shuffle everything` as the one primary white pill, `Dig` and
`Dig the untagged` quiet, then the inspector toggle. `aria-current` on the active route. No Share
item (r6 §7).

**PlayerDock**, 80px, full bleed, radius 0, `--shadow-dock` plus a top hairline. Ten controls in an
80px bar is how r6 §1 happened, so the width is budgeted:

```css
grid-template-columns:
  120px                /* transport: prev · play/pause · next            */
  minmax(180px, 240px) /* NowPlayingPill                                 */
  minmax(0, 1fr)       /* waveform + seek                                */
  72px                 /* m:ss / m:ss                                    */
  minmax(0, 200px)     /* mode readout, ellipsised                       */
  128px                /* mode cluster: shuffle · reshuffle · repeat · LM */
  40px;                /* queue toggle                                   */
```

That leaves ~600px of waveform at 1440. The mode cluster is where shuffle, reshuffle, repeat and
level match live; they were homeless once the queue moved to the inspector, and two layers deep on a
phone is not a home. Below 1120 the mode readout collapses into the pill's tooltip and the cluster
becomes a single `…`; below 900 the dock is transport · pill · queue toggle and the waveform becomes
a 2px progress hairline on the dock's top edge. The toast slot is absolutely positioned at
`bottom: 100%`, so toasts never float over content and never steal width.

The transport keeps both glyphs in the DOM, one absolutely positioned, cross-fading `scale
var(--glyph-scale-from) → 1`, `opacity 0 → 1`, `blur var(--glyph-blur-from) → 0` on `--ease-glyph`.
Never toggle `display`. The state change is instant per the frequency gate; only the glyph
cross-fades, driven by `data-state` on the player root.

**Waveforms are canvas, everywhere.** Both strips, the dock's and Listen's, are the same ~60 line
renderer over `/peaks/{ulid}`: bars from the min/max array, masked to a percentage, seeking with
`currentTime = (offsetX / rect.width) * duration`. No wavesurfer instance is constructed anywhere.
It reads the shared element's `currentSrc` as its initial URL and calls `decodeAudioData` on it
whenever peaks are not yet in hand, which pulls a whole mp3-320 over the tailnet and is what the
never-client-decode rule forbids; and two strips bound to one media element is two instances
fighting over it. Overlay exits use Radix's `data-state="open|closed"` contract with a CSS
animation, so no animation library is involved either.

### 5.1 The subject model

The store holds `cursorUlid`, `playingUlid`, and `follow: 'cursor' | 'playback'`. One derived
`subjectUlid` is what the inspector renders and what `t`, the `1–9` deck, the heart and every tag
write target. Nothing queries the DOM for its target.

`follow` snaps to `'playback'` when a dig starts and whenever playback advances with no intervening
cursor move; it returns to `'cursor'` on the next `j`/`k` or row click. Pin freezes the subject
either way, and unpinning snaps back immediately. While following playback, the list scrolls the
sounding row into view on every advance.

The reason is the dig loop, which is the app's primary purpose. During a dig you are listening, not
pressing `j`/`k`, so a cursor-only inspector leaves the cursor parked and sends `1–9` to whatever row
you last touched. A partially-correct panel is worse than no panel because it makes you tag the wrong
song. The inspector header prints the subject title, so the target of a keystroke is never ambiguous.

### 5.2 `/` library, table view

The primary work surface, replacing the 438 KB HTML page, and the archetype every other list reuses.

**Data.** One `GET /api/library?<filters>&include=facets,counts,tags`, with no client `limit`. The
response is the full materialised result (SQL already caps at 10 000) plus `total`, `truncated`, and
the ordered `queue` array of bounce ULIDs. A magic `limit=500` is wrong twice: era, key and
`skip_sketches` filtering runs in Python *after* the SQL `LIMIT`, so it caps the candidate set before
filtering; and the archive grows nightly, so the failure lands in weeks as rows silently missing and
`Shuffle these {N}` disagreeing with what is rendered. When `truncated` is true the list renders a
banner rather than lying. Paint cost is governed by `content-visibility`, not by a data ceiling.

Three fields join the row projection, all projections over existing rows and none a schema change:
`hearted` (one `LEFT JOIN reactions … kind='heart' AND actor=? AND deleted_at IS NULL`),
`has_original` and `original_size` (one batched pass over `files`, replacing the per-row
`bounce_download_options()` that costs ~950 queries and ~950 `stat()` calls today). **The shared tail
reads its pressed state and its download affordance from the row's data, never from the DOM it was
cloned out of.** Without them the port reproduces the live bug where every heart renders un-hearted
and `download original` has nothing to hide itself with.

**Queue seeding.** Activating a row by any route (gutter ▶, `Enter`, double-click, `…` → play)
replaces the queue with the payload's `queue` array and sets the cursor to that row's index. `add to
queue` and `play next` are the only paths that mutate an existing queue. Today two routes on the same
row build two different queues, one of them a dead end of length 1.

**Sticky top of the list column**, fully opaque, no backdrop-filter: `ListHeaderBar` (`{total} songs`
· `Shuffle these {N}` · sort · density · grid/list), `ActiveFilterStrip` (one removable token per
filter plus `Clear all`, the only combined readout of what is filtered), then the table header at 34px,
`--text-3`, sentence case, thin vertical hairlines between column groups, search at its far right
inside the header. `SortHeaderCell` surfaces the 14 server-side sort modes and the `header_sorts`
computation that exists in Python and that no template renders: click cycles asc → desc → off,
URL-encoded. `random` is not a sort, it is a scramble button beside shuffle.

**Row anatomy**, `--row-h` 44px fixed, single line, never wrapping mid-token:

```css
grid-template-columns:
  28px  var(--row-gutter-w)  var(--cover-sm)  minmax(0, 1fr)
  auto auto auto auto  var(--row-tail-w);
/* checkbox · # → ▶ · era field · title+meta · era · key · bpm · length · tail */
```

That declaration makes r6 §1 structurally impossible: the tail is a grid track, not an absolute
overlay, so it cannot escape the row or cover the title, and the title track ellipsises rather than
pushing, always carrying a `title` attribute. `scroll-margin-block: 88px 96px` keeps `j`/`k` from
parking a row under the header or the dock.

**The 36px row cover is a CSS era field, not an image.** At 36px the generated typographic cover is
illegible anyway, and 472 `<img>` requests on first paint is 472 SQLite opens and an immediate 429.
The generated JPEGs appear only at ≥96px (grid card, inspector hero, detail hero, Media Session), as
plain `<img>` with `width`/`height`, `loading="lazy"`, `decoding="async"`, from
`/art/song/{song_ulid}`. `next/image` is banned by lint: `/_next/image` fetches upstream server-side
with no cookie jar, so every cover would 401, and it puts Node in the byte path.

**RowGutter** is the whole play affordance: the tabular row number becomes a ▶ on hover or focus
(nudged 2px right) and equalizer bars while that row sounds. No separate play button competing with
the title, which is the collision r6 §1 documents.

**RowTail**, fixed 132px, identical position on every row: version badge · heart · `…`. The menu, in
order: `tag this song` · play next · add to queue · add to collection · rip stems · download original
· download mp3 · reveal in list · open detail. `tag this song` is first because it replaces today's
one-click `#` glyph, the shortest path from "I see a row" to "I'm typing a vibe on it"; `t` needs the
cursor moved first and is a second action. The tail is rendered on demand as one shared element at
table level, moved to the hovered or focused row, not 472 copies. This is the fix for r6 §10 and the
one place a naive React port re-introduces the 438 KB page, because "just render the buttons in the
row component" is the obvious thing to write.

**Row states.** Four co-occur constantly and each owns a different property:

| State | Property |
|---|---|
| hover / selected | background (`--row-hover` / `--row-selected` / `--row-selected-hover`) |
| focus | two-tone ring (`--focus-inner` + `--focus-halo`), survives a white fill and `overflow: hidden` |
| playing | `box-shadow: inset 3px 0 0 var(--era)` plus `--weight-semi` plus the gutter glyph |
| panel subject | 4px dot in the tail |

Deliberate divergence from the audit: Samply's active row is a near-white background, but a Samply
row only ever carries one state and ours carries four. The era thread is always `box-shadow: inset`,
never `border-left`, which shifts layout and jitters the list on every advance.

**Reveal, and its miss path.** `L` and the `NowPlayingPill` centre the sounding row, move the cursor,
focus and flash for 900 ms. Today both do nothing at all, silently, when the sounding track is
filtered out of the view (`Shuffle everything`, then filter to `unheard`). The port specifies the
miss: drop the filter tokens that exclude it, mutating the `ActiveFilterStrip` so the change is
visible, then centre and flash; if it still cannot be found, raise the dock toast `Now playing isn't
in this view.`

**FilterRail**, 220px, a facet index with counts rather than the reference's 360px nav list.
Collapsible groups, each with a live count the query already computes and the current UI throws away:
View (all · unheard · hearted by me · no vibe yet · skip sketches under 90 s) · Era · Key · Status ·
Vibe · Instr · Collab · Use, each tag dimension carrying an explicit `— untagged —` pseudo-value with
its own count · Sort · Collections. A dimension with ≤1 distinct value is suppressed (status is 471
demo + 1 released, a dead control taking rail space). Type-to-filter appears past 12 values. Cmd-click
multi-selects within a dimension (OR); dimensions combine with AND. Every click is a client-side
filter that updates the URL and preserves scroll and selection, never a navigation. Real scrollbars,
`scrollbar-gutter: stable`, `overscroll-behavior: contain`.

**BulkTagBar.** Idle it reads only `Select songs to tag or download` (r6 §8), as a distinct render
branch rather than a bar with hidden children, so the noisy shipped copy cannot leak back. With a
selection: `12 selected`, add/remove chips, bulk status, download selected, and a `more` disclosure
for make collection / mark released / queue stems. The cap note appears only when the selection
exceeds it, sourced from `GET /api/downloads?ulids=`.

**Rendering budget.** `content-visibility: auto` with `contain-intrinsic-size: 0 44px` on every row,
and no virtualisation. The measured problem in r6 §10 was 3.6 KB of markup per row, not row count,
and `content-visibility` removes paint and layout cost while preserving `Ctrl+F`, shift-click ranges,
`scrollIntoView` on the sounding row and focus retention, all of which the keyboard model depends on.
A virtualised list unmounts rows that scroll out of view, including the focused one, dropping focus
to `<body>` and silently killing `j`/`k`. Introduce TanStack Virtual only on a named trigger:
`ui-audit` reports `heavy-dom` on `/`, or cursor-move latency exceeds 100 ms at p95. If it fires,
drive `j`/`k` from the store cursor, `scrollToIndex` on every move, and force-mount the cursor and
sounding rows.

**Scroll restoration** covers five containers: list, rail, each facet group, inspector, version rail.
`<Activity/>` preserves state and DOM but hides with `display: none`, which destroys the scroll
container's layout box and resets `scrollTop` to 0 on re-show. Each container therefore captures
`scrollTop` in a `useLayoutEffect` cleanup and restores it on show, to within 1px.

### 5.3 `/` library, grid view

Same shell, rail and inspector; only the list column swaps, and cursor, selection, queue and playback
survive it. The era covers are already generated and used today only for Media Session artwork, which
makes this the cheapest large win available.

`repeat(auto-fill, minmax(196px, 1fr))`, `gap: var(--space-4)`: 5 per row at 1440, 2 on a phone.
Square aspect on the cover, never the card. Card = cover (`--radius-md`, 1px `--cover-outline`) +
title (`--text-ui`, `--weight-medium`) + era (`--text-caption`, `--text-2`), exactly two type sizes;
card radius `--radius-lg` with `--space-1` padding keeps the concentric rule. Covers are the colour
and the UI around them stays grayscale. Cover titles never wrap mid-word. Hover dims the cover and
centres the play triangle; the card body is not a second play button. The grid is a browsing surface:
multi-select and tagging stay in the table, and a card's `…` is a subset of the row's.

### 5.4 The right inspector

380px, subject per §5.1, a single scrolling column, `--space-4` padding, `--radius-xl` panel with
`--radius-md` controls inside. It paints optimistically from the row's own data on subject change,
then settles `GET /api/songs/{ulid}` over `--dur-1`, so it never flashes empty and never shows two
songs at once. Sections separated by `--space-6` and hairlines: cover hero · title over era and date
range · `SpecGrid` (label above value, `--text-3` over `--text-1`, tabular, not monospace, nulls `—`,
derived values dotted-underlined; it recedes behind the title because it is reference, not hierarchy)
· `VersionRail` · `TagPanel` · `StemsList` · downloads · `Open full catalog detail ›`. The queue is a
second tab (§5.14).

`VersionRail` is the song's history as a tappable commit graph: dots on a 1px rule, filled dot for
current, every node playable, sans labels with tabular figures. 96 songs have multiple bounces; the
other 376 render the single-version state, which must not look broken.

**Nothing inside the inspector clips or scrolls horizontally.** Chip rows wrap inside the padding.
That is r6 §2, enforced by an audit rule rather than by vigilance. Below 1400 it is a right overlay,
below 900 a bottom `Sheet`. There is no separate `row-detail` route: the sheet is the narrow-viewport
inspector, which deletes a duplicate template.

### 5.5 `/songs/[song_ulid]`, `Listen | Manage`

`BackHeader`: back chevron · small square artwork · title over era · round play / `+` / `…` pushed
right, with a `Listen | Manage` segmented control centred at the top. The mode lives in the URL and
survives reload. This is the audit's most valuable steal: one surface for consuming and one for
editing, instead of a single mode that tries to be both and collides.

**Listen** is the cover as a real object (`--cover-hero` 220px), the full-width canvas waveform with
click-to-seek, the version subtree, the ordered version tracklist. **Manage** is `SpecGrid` ·
`TagPanel` · `catalog knowledge` (factual provenance with source and author) · `needs your ear`
(judgment tags with human-vs-judgment styling) · stems · downloads · notes · `songs similar to this`.

The title is the hero at `--text-display` and the `SpecGrid` recedes at `--text-caption` labels.
Metadata is the design here: key, camelot, bpm, stems and tag provenance are our value and the reason
we do not adopt the reference's emptiness. Era colour appears exactly twice, the cover field and one
3px thread. Real motion is allowed here and almost nowhere else, staggering the panel's chunks at
`--stagger`; the chips inside it may not.

Below 1120 the library row's disclosure expands versions inline as an indented subtree with a 1px
connector and an `⊕ Add version` affordance (`--text-2` with a hairline underline, not the reference's
blue). At ≥1120 it is suppressed in favour of the inspector's `VersionRail`, which keeps the fixed
row-height contract intact. `/more/{bounce_ulid}` is retired; everything in it is in Manage and in the
inspector's `TagPanel`.

### 5.6 TagPanel and the tag deck

One chip group per dimension: vibe · instr · collab · use · status · keeper · key, with `your ear`
marking the subjective ones. Every write targets `subjectUlid`.

- Every chip is a real `<button aria-pressed>` at 32px, 13px, `padding: 0 10px`, `gap: 6px`, not the
  ~60px monsters shipped. Fine pointer gets a 40px hit area via `::before`; coarse raises the visible
  height to 44.
- A chip **posts unconditionally** so an applied chip can be un-pressed. Today the client skips the
  write when the chip is already applied, which means no tag can be removed anywhere in the app.
- Applied is `--accent-fill` + `--accent-ink`, or `--fill-quiet-active` with a 3px era underline via
  inset box-shadow, never a border. Derived chips render at `--text-2` with a dotted underline; one
  click promotes them to `source='human'`.
- **Toggles do not animate.** Instant fill and colour, at most a `--dur-1` opacity settle. This is the
  frequency gate, and the `1–9` model depends on it.
- Provenance is one quiet line under the group (`demo · set by you`), never layered on the chip and
  never a floating `↗`. r6 §4 forbids re-layering it, and `TagChip`'s props do not accept provenance
  children, so it cannot regress. Chips wrap inside the panel's padding.
- Every group ends with a `+ value` `TypeaheadInput` suggesting over existing vocabulary with each
  candidate's global count inline (`dreamy · 7 songs`), so the owner reuses rather than invents, and
  offering creation on no-match. Anchored by a relatively positioned wrapper with Radix Popper
  collision detection; CSS anchor positioning is rejected as Chromium-only.
- `KeeperSegment` is a 0–5 segmented row, 32px, ~34px per digit, one tab stop, arrow keys within it.
  It is a rating, not six buttons.
- The archive holds three human tags across two distinct vibe values, so **the empty state is the
  default state.** Below eight of the owner's own values, show a dismissible `suggested` group. No
  hardcoded vocabulary ever writes to the database.

**`1–9` binds to a nine-slot deck, not to "the first nine chips".** Frequency-sorted chips change
meaning as you tag, so muscle memory cannot form. The deck derives from the owner's own most-used
values with the digit printed on each slot as a `Keycap`, which is the part that matters. There is no
manager UI: on day one most slots are empty, and pressing an unbound digit opens the vibe typeahead
bound to that slot, so the deck is configured by use. A bound slot never changes meaning on its own.

### 5.7 Stems

In the inspector and in Manage, nowhere else, from `GET /api/songs/{ulid}/stems`. Two rails, `source
exports` and `separated`, each node playable with a sized download. Job progress is always a phase
label and an indeterminate spinner, never a percentage; the banlist forbids percent-complete and that
includes separation. Polls `GET /api/jobs/stems` every 5 s through `refetchInterval` while queued or
running and stops otherwise. Stems are first-class playable tracks with their own ULIDs.

### 5.8 `/tags`, vocabulary desk

A real table at 40px per row: `dim · count | value | source | [rename 220px] [Rename] [Delete]`.
Currently ~130px per row, so seven tags fill a 1440×900 screen; the r6 acceptance is 15+. Rename into
an existing value merges, with existing human rows winning. Derived tags are protected from deletion
and show it.

### 5.9 `/collections` and `/collections/[ulid]`

**List.** 44px rows matching library rows exactly (currently 96px): name · track count · notes. Three
creation paths, all named at creation: from the current **selection** (song ULIDs, resolved
server-side to each song's newest mirrored bounce), the current **filter** (re-run server-side, no
explicit song list accepted), or the current **queue** (explicit bounce ULIDs). `source` is a required
discriminated union in the request type, so a `POST` carrying only a name is not constructible: r6
§11's defect made impossible at the type level rather than caught at runtime. If the queue equals the
whole library the client says so and the server requires `confirm_all: true`.

**Detail.** Back · `ordered collection · N songs` (its own count) · name · notes · `play` · `shuffle`.
Numbered rows via a CSS counter, playable, per-row remove, positions re-packed server-side.

**Reorder is `Move up` / `Move down` in the `…` menu plus keyboard, and nothing else in v1.** One
collection exists. Drag inside a list that also carries a keyboard cursor is the most fragile
interaction in the app, and it costs three dependencies to build the plan's own named risk. Both paths
hit the same `PUT /api/collections/{ulid}/order` permutation endpoint, so drag is additive later.

### 5.10 `/triage`

One decision at a time: `N decided today`, then a card (`version_label · date` · title · `key ·
camelot · duration` · `Play bounce`) and three verdicts. Committing swaps in the next card without
navigation and raises an undoable toast. The selection algorithm already exists in Python, so the
route is a card and three buttons.

### 5.11 `/activity`

Open alerts above the reaction feed, newest 80, with the actor display mapping applied.

**No `EventSource`.** The whole reaction table is 25 rows in the app's life, and the SSE path costs a
root-layout connection, a module-level StrictMode guard, a Caddy `flush_interval -1` handle, a named
risk and an acceptance test, plus a 2 s database poll per subscriber, to keep 25 rows warm. The query
refetches on window focus and on a 30 s interval. `/activity/events` stays mounted on FastAPI, unused.

### 5.12 `/settings`

A section rail on the left reusing `FilterRail` geometry, and a single reading-width main column at
max-width ~720px, left-aligned rather than centred (a centred hero is banned and this is a reference
surface). Each section: a `--text-title` with a dim `--text-2` description, both outside the card;
then a grouped card whose rows are separated by hairlines, each row `label` + `description` on the
left and **exactly one** control on the right. A row needing two controls needs two rows. Groups
separated by `--space-8`, never a card inside a card. Changes save on interaction with a quiet `saved`
settle and an undoable toast; no Save button.

Sections: **Account** (absorbs `/members`) · **Playback** · **Sign out** · **Danger**, last, quiet
`--status-error` with a `ConfirmDialog` naming the consequence. There is no Display section, because
density and default view are the two `ListHeaderBar` toggles and they persist themselves; and no
tag-deck manager, per §5.6. Empty states are stated plainly and centred in their card, in the audit's
voice: `No audio playing / Play a track to see its available options.`

### 5.13 `/login` and `/setup`

Next owns both pages: a bare shell mounting no store and loading no player JS, with a visible field
border and a visible focus ring (the shipped form has neither and is illegible), 16px inputs, and a
generic `Login failed.` on 401 that never says which field was wrong.

They submit JSON to `POST /api/session` and `POST /api/setup` through the same fetch wrapper as
everything else, so both carry `X-CR8-Request: 1`. That is what keeps the front door simple: Caddy
needs no method matcher over `/login`, `/setup` needs no CSRF exemption, and the invariant "every
request the Next client issues is under `/api/`" holds. `DELETE /api/session` is logout.

### 5.14 Queue tab

The queue's desktop home is a tab in the right inspector, not a `<details>` in the dock. Rows diff by
`data-queue-id` and `scrollTop` is preserved; the current implementation calls `replaceChildren()` on
every track change, which is ~1900 elements at 472 items and resets the scroll. Never render 472
remove buttons: removal lives in the shared tail pattern. `Jump to now playing` is always visible.
Reorder uses the same menu and keyboard path as collections. `Save queue as collection` is the
promotion path; the queue is the ephemeral shortlist, not a third bucket.

### 5.15 Keyboard help

`?` opens a `ModalDialog` with the full map (§6, Keyboard). Every single-key binding is suppressed
while an input, textarea, select or contenteditable has focus, and modified keys are ignored except
Cmd/Ctrl+Z.

The share modal is specified in `specs/DEFERRED.md`, not here. r6 §7 removes the share nav item and
the guest app; v1 ships neither the modal nor the inspector card, and the inspector carries a
documented seam where the card goes.

---

## 6. Feature parity checklist

Every feature in the inventory appears here exactly once. **DROP** is a deliberate removal with a
stated reason; **DEFER** is specified but not built in v1. Everything else must ship.

### Shell and player

- [ ] Persistent `<audio>` surviving navigation: module singleton in `lib/audio.ts`, held above the router outlet
- [ ] **Silent-WAV audio unlock** armed synchronously in the first `pointerdown`/`keydown`/`touchstart`, before any `await`. Every play path fetches a queue first and the gesture permission is lost across that await
- [ ] **The media-element source node is created at most once per document**, behind `let graph = null`, from inside that same gesture handler, never from a `useEffect`
- [ ] `ctx.resume()` on every play; first play after a cold load is audible with `ctx.state === 'running'`
- [ ] ReplayGain as a `GainNode`, `10^(dB/20)`, feature-detected off on any Safari, default off
- [ ] Level-match defeat toggle, disabled until the graph exists **[new]**
- [ ] **`loadSequence` guard**: a stale `/api/tracks` response is dropped by sequence number, so a slow load for a skipped track cannot retarget the player
- [ ] `data-empty` / `data-state` (paused/playing/loading/error) on the player root
- [ ] sr-only `aria-live`: `title · version_label` on track change, `Playback error · title` on failure
- [ ] Transport prev · play/pause · next; both glyphs stay in the DOM and cross-fade
- [ ] `previous()` restarts the current track when `currentTime > 3 s`, otherwise steps back
- [ ] `NowPlayingPill`: era tile · title · `version_label · key · camelot` · dig reason
- [ ] Queue readout `N of M` / `N dug · M left`; mode readout `queue` / `shuffling · label` / `digging`
- [ ] Canvas waveform over `/peaks`, click-to-seek, never client-decoded; no wavesurfer instance **[changed]**
- [ ] Time readout `m:ss / m:ss` via `useSyncExternalStore` throttled to ~4 Hz, never in the Zustand store
- [ ] Seek slider (44px hit height over a 4px track); volume slider, persisted
- [ ] Dock mode cluster owning shuffle (`aria-pressed`) · reshuffle · repeat off/all · level match, width-budgeted in the 80px row
- [ ] Shuffle un-shuffles back to source order keeping the current track
- [ ] Reshuffle Fisher-Yates the source queue with the current track pinned to index 0
- [ ] Repeat `all` wraps both `advance()` and `retreat()`
- [ ] `tag playing` opens the tag surface and focuses the first control; `stop digging` hidden unless mode = dig; queue toggle
- [ ] Queue row remove; removing the sounding track advances or empties the player
- [ ] `ended` → report heard, advance, refill dig, or emit queue-ended
- [ ] **Dig refill**: on exhaustion, silently refetch `/api/dig` with the filters that started the dig, held in the store
- [ ] `dig_reason` carried through the queue item into the player (`never played · no vibe yet`, `no vibe yet`, `never played`, `not since Jul 4`)
- [ ] Media Session: play, pause, previoustrack, nexttrack, seekbackward −10 s, seekforward +10 s, artwork from `/art/{bounce_ulid}`
- [ ] `warmNext`: prefetch the next track's peaks and first 512 KB of audio on `playing`; clear the warmed set past 60 entries
- [ ] Queue persisted to `sessionStorage` (`playQueue`, `sourceQueue`, `cursor`, `repeat`, `mode`, `label`, `shuffleEnabled`), revalidated on load; shuffle preference persisted separately
- [ ] `playQueue` / `sourceQueue` split so un-shuffling restores order with the cursor re-found by id
- [ ] Any `[data-track-url]` click starts playback; the sounding track toggles instead
- [ ] TopBar nav with `aria-current`; `Shuffle everything` primary, `Dig`, `Dig the untagged`, sentence-case sans (r6 §6)
- [ ] Dock toast slot at `bottom: 100%`, auto-dismiss at 30 s, never floating over content
- [ ] Request indicator from TanStack Query `useIsFetching`
- [ ] PWA manifest with `theme-color` at the new ground; add-to-home-screen never promoted (iOS PWA AudioContext regression)
- [ ] Self-hosted fonts, zero CDN
- [ ] **DROP** `hx-boost` / `hx-preserve` / `hx-select` / `historyCacheSize = 0`, the monkey-patched `fetch` counter and `body.is-fetching`, and the `#write-feedback` OOB region

### Subject, inspector, and detail

- [ ] `cursorUlid`, `playingUlid` and `follow: 'cursor' | 'playback'` in the store; one derived `subjectUlid`
- [ ] **The inspector re-targets the sounding song on track change while digging**, and on any advance with no intervening cursor move
- [ ] `follow` returns to `'cursor'` on the next `j`/`k` or row click
- [ ] **The list scrolls the sounding row into view on every advance** while following playback
- [ ] `TagPanel`, `t`, `1–9` and the heart bind to `subjectUlid`, never to a DOM query
- [ ] The inspector header prints the subject title
- [ ] Pin toggle (`aria-pressed`, label flips pin/pinned); unpinning snaps back immediately
- [ ] Inspector paints optimistically from row data, never flashes empty, never mixes two songs
- [ ] Cover hero · title · era + date range · `SpecGrid` · `VersionRail` · `TagPanel` · stems · downloads · `Open full catalog detail ›`
- [ ] `SpecGrid`: key · camelot · bpm (rounded) · length · added · versions · source · filename, tabular, not monospace, nulls `—`, derived dotted-underlined
- [ ] `VersionRail`: every mirrored bounce playable, connector, dot, `v3 · Jul 4`, mixrole flag, newest `aria-current`; the single-version state does not look broken
- [ ] Nothing in the inspector clips or scrolls horizontally; chip rows wrap (r6 §2)
- [ ] `‹ library` back link; `Listen | Manage` with the mode in the URL **[new]**
- [ ] Listen: `--cover-hero` · waveform · version subtree · ordered version tracklist
- [ ] Manage: `SpecGrid` · `TagPanel` · `catalog knowledge` (source + author) · `needs your ear` · stems · downloads · notes · neighbours
- [ ] Title · `released` badge · era + formatted date range · `unheard` badge
- [ ] `quick vibe`: heart + up to 6 top vibe chips
- [ ] Notes: add (280 chars, optional timecode 0–86400), newest-first list with actor and `m:ss`
- [ ] Chromaprint neighbours with similarity percentage, multi-select, `Apply my tags to selected neighbours`; empty state points at fingerprint enrichment
- [ ] Inline version subtree with connector and `⊕ Add version` below 1120px; `VersionRail` owns versions above it
- [ ] **DROP** `/songs/{ulid}/row-detail` as a route (the <900px inspector `Sheet` replaces it) and `/more/{bounce_ulid}` (Manage and the inspector `TagPanel` carry all of it)

### Library

- [ ] Full filter grammar: `q`, `status`, `era`, `key`, `dim`, `value`, `vibe[]`, `instr[]`, `collab[]`, `use[]`, `untagged_dim[]`, `unheard`, `hearted`, `untagged`, `skip_sketches`, `sort`, `seed`
- [ ] All 14 sort values, `random` auto-generating an 8-byte seed
- [ ] Released songs excluded by default; `released` only as an explicit dimmed facet
- [ ] `skip_sketches` floor: under 90 s AND (status in idea/jam OR `sketch` in the filename)
- [ ] `GET /api/library` returns the full materialised result plus `total`, `truncated` and the ordered `queue` array; **no client `limit`**, and a visible banner when `truncated`
- [ ] Row projection carries `hearted`, `has_original`, `original_size`, all batched, no per-row `stat()`
- [ ] **The shared tail reads its state from the row's data, never from the DOM it was cloned from**
- [ ] **Activating a row by any route replaces the queue with the payload's `queue` array and sets the cursor to that row's index**; `add to queue` and `play next` are the only paths that mutate an existing queue. Test: after activating row N, `queue.length === total` and `cursor === N`
- [ ] Live `{total} songs`; dig-mode status line `digging · N played · M to go`
- [ ] `Shuffle these {N}` with a label that tracks the count exactly; `Stop digging`
- [ ] `ActiveFilterStrip`: one removable token per active filter plus `Clear all`
- [ ] Search in the table header, `maxlength` 120, debounced ~120 ms
- [ ] FTS prefix search: emit `"term"*` so type-ahead matches partial words
- [ ] Search error surface (400 → `Search could not be completed.`)
- [ ] `Save as collection` from the current filter
- [ ] Row grid: checkbox · gutter · era field · title+meta · era · key · bpm · length · tail, title `minmax(0,1fr)` with ellipsis and a `title` attribute
- [ ] **Row cover is a CSS era field, not an `<img>`**; generated JPEGs appear only at ≥96px, as plain `<img>` with `width`/`height`, `loading="lazy"`, `decoding="async"`, from `/art/song/{song_ulid}`
- [ ] **`next/image` banned by lint** (`/_next/image` fetches server-side with no cookie and 401s, and it puts Node in the byte path)
- [ ] Gutter number → ▶ on hover/focus → equalizer bars while sounding, tabular so the column does not shift
- [ ] `unheard` signal badge per row; era threading class (pelicana / nova1 / working / unknown)
- [ ] Row states: cursor · panel-subject · selected · playing · revealed flash · instant-selection, one CSS property each
- [ ] `aria-current` on the sounding row with the era-coloured 3px inset rail, never `border-left`
- [ ] **One shared hover tail** moved between rows on `pointerover`/`focusin`, rendered once at table level, not 472 copies (r6 §10)
- [ ] Tail: version badge · heart (optimistic, state from row data) · `…`
- [ ] `…` menu: **tag this song** · play next · add to queue · add to collection · rip stems · download original · download mp3 · reveal in list · open detail
- [ ] `tag this song` moves the cursor, targets the inspector at that song and focuses the vibe typeahead, replacing the shipped one-click `#`
- [ ] Download original hidden when no lossless original exists, from `has_original`
- [ ] Column-header sorting via `SortHeaderCell`, surfacing the latent `header_sorts` **[new]**
- [ ] Grid/list toggle and density toggle (44 / 56), both persisted to `localStorage` **[new]**
- [ ] Batch bar idle: `Select songs to tag or download` only, as a distinct render branch (r6 §8)
- [ ] Batch bar active: `N selected`, download selected, bulk status, tag dim + value + Add/Remove, `more` (make collection · mark released with URL · queue stems)
- [ ] Client-side ZIP cap (50 files / 2 GB) with a skipped-count note, from `GET /api/downloads?ulids=`
- [ ] Rail view group: all · unheard · hearted by me · no vibe yet · skip sketches, each with a live count
- [ ] Rail era / key / status groups with counts, suppressed when a dimension has ≤1 distinct value
- [ ] Rail vibe / instr / collab / use groups with per-value counts
- [ ] `— untagged —` pseudo-facet per dimension with its own count, repeatable as `untagged_dim[]`
- [ ] Per-facet type-to-filter once a group exceeds 12 values; sort and collections groups in the rail; groups collapsible, open by default
- [ ] **Cmd/Ctrl-click a facet to multi-select** within a dimension (OR); dimensions combine with AND
- [ ] Chip triple-action: toggle the tag, filter the library to it, and start a shuffled queue of every song carrying it
- [ ] **Selection survives a filter change**: the checked set is store state keyed by `song_ulid`, not DOM state, so it is re-applied by render rather than re-checked after a swap
- [ ] **Scroll restoration for five containers** (list, rail, each facet group, inspector, version rail): `scrollTop` captured in a `useLayoutEffect` cleanup and restored on show, because `<Activity/>` hides with `display: none` and resets it. Acceptance is within 1px after a hide/show cycle
- [ ] **`reflowListToQueue`**: shuffle and dig visibly reorder the rendered list into queue order and scroll the sounding row into view. Without it shuffle is invisible and the list disagrees with what is playing
- [ ] `content-visibility: auto` + `contain-intrinsic-size: 0 44px`; no virtualisation until a named trigger fires
- [ ] `scroll-margin-block: 88px 96px` so `j`/`k` never parks under the header or the dock
- [ ] Empty state `No songs on this shelf / Clear a filter to return to the crate.`; filtered-to-zero always offers `Clear all`
- [ ] Responsive: inspector → overlay <1400, rail → icon strip <1120, phone stack + filter sheet <900
- [ ] Grid view: `auto-fill minmax(196px, 1fr)`, two type sizes, hover dims and centres the play triangle, equalizer while playing, `--signal` dot for unheard, no carousel **[new]**
- [ ] Grid/list swap preserves cursor, selection, queue and playback
- [ ] `GET /art/song/{song_ulid}` with `Cache-Control: private, max-age=86400` and an ETag **[new]**
- [ ] **DROP** infinite scroll and `offset` paging; one payload, with `content-visibility` governing paint

### Tagging

- [ ] Seven dimensions: status (6 fixed) · keeper (0–5 segmented) · key (top 30, allows new) · vibe · instr · collab · use
- [ ] `your ear` marker on the subjective dimensions
- [ ] Chips 32px tall, 13px, `0 10px`, gap 6, wrapping (r6 §2, §3); 40px hit area on fine pointer, 44 on coarse
- [ ] Chips post unconditionally so an applied chip can be removed **[fixes a live defect]**
- [ ] **Optimistic `aria-pressed` with rollback**: Query `onMutate` snapshots, `onError` restores the exact prior value, `onSettled` invalidates. Replaces `dataset.optimisticPrevious`
- [ ] **Frequency gate**: chip toggles, `j`/`k` and `space` are `--dur-0` with no press-scale; everything else animates at 180 ms on `--ease`. `prefers-reduced-motion` means gentler, not zero
- [ ] Provenance as one line under the group, no glyph, no layered label; `TagChip` props cannot accept provenance children (r6 §4)
- [ ] Derived / filename / mixrole / proposed / catalog provenance styling
- [ ] Per-dimension `+ value` typeahead with vocabulary counts inline and create-on-no-match
- [ ] `status` / `keeper` / `key` write the songs row and set `human_touched`
- [ ] `vibe` / `instr` / `collab` / `use` / `problem` toggle `song_tags` with `source='human'` plus an audit reaction
- [ ] Undo toast on every toggle; rail counts refreshed after every toggle and after undo
- [ ] Tagbar for the sounding track in the dock, discarded if the track changed in flight
- [ ] `1–9` bound to a nine-slot deck with printed digits; an unbound digit opens the typeahead bound to that slot **[behaviour change, deliberate]**
- [ ] **DROP** the Settings tag-deck manager; the deck is derived and configured by use

### Stems

- [ ] Source exports rail (vox/novox/inst/acap/bass/gtar/stems), playable, downloadable with size
- [ ] Separated rail ordered vocals · instrumental · drums · bass · other (`· leftovers`), FLAC download with size
- [ ] Recipe flag per stem (default-v1 / hq-v1); stale-source badge on sha256 mismatch
- [ ] Empty state copy preserved verbatim
- [ ] Job states queued · separating · pass 1–2 · failed with error + retry
- [ ] Action states: separate · redo from current source · redo in high quality · retry
- [ ] 5-second polling while queued/running, stopped otherwise
- [ ] Phase label + indeterminate spinner, never a percentage
- [ ] Stems are first-class playable tracks resolvable by `/api/tracks`, `/m` and the queue

### Tags desk

- [ ] One row per (dim, value) across vibe/instr/collab/use at 40px
- [ ] Counts combine `song_tags` and non-audit chip reactions
- [ ] Rename (220px input) with merge-on-collision, human rows winning
- [ ] Delete: soft-delete chip reactions, hard-delete `song_tags`
- [ ] Derived tags protected from deletion and shown as protected
- [ ] Empty state `No subjective vocabulary yet.`; 15+ rows at 1440×900 (r6 acceptance)

### Collections

- [ ] List with track counts, 44px rows
- [ ] Create from selection · filter · queue, all named, `source` a required discriminated union (r6 §11)
- [ ] Whole-library guard: client confirmation plus server `confirm_all`
- [ ] Detail: back · own count · name · notes · play · shuffle
- [ ] Numbered ordered rows, playable, per-row remove, positions re-packed server-side
- [ ] Reorder via `Move up` / `Move down` in the `…` menu and keyboard, persisted as a complete permutation
- [ ] Rename / edit notes **[new]**; delete a collection **[new]**
- [ ] Empty state naming all three creation paths
- [ ] **DEFER** drag reorder, and its three dependencies, until reordering is a frequent gesture

### Triage

- [ ] `N decided today` headline
- [ ] Card: `version_label · date` · title · `key · camelot · duration` · `Play bounce`
- [ ] Three verdicts (gem / keep / archive), next card without navigation
- [ ] Queue of up to 20 from the newest 500 with no live verdict from this actor
- [ ] `gem` raises keeper to `MAX(keeper, 5)`; a verdict supersedes any prior verdict from this actor
- [ ] Undo toast with the gem/keeper rollback
- [ ] Empty state `Queue clear for now.`

### Activity

- [ ] Open alerts with kind, message, optional label, `Acknowledge`
- [ ] Activity list, newest 80: `actor · kind value` + song title + timestamp
- [ ] Actor display mapping and `:audit:` suffix stripping
- [ ] Refetch on window focus and on a 30 s interval
- [ ] Empty state `No reactions yet.`
- [ ] **DROP** the `EventSource` on `/activity/events`, its root-layout mount, its StrictMode guard and its Caddy `flush_interval` handle. 25 reactions exist in the app's life; the route stays on FastAPI, unused

### Account, auth, settings

- [ ] Member list, add member, generated 16-char password shown once
- [ ] Remove member (deletes sessions + user); self-removal refused
- [ ] Duplicate username and invalid input surfaces
- [ ] Argon2id unchanged (time 3, memory 64 MB, parallelism 2)
- [ ] Login with autocomplete tokens, visible border and focus ring, generic 401 copy, 16px inputs
- [ ] `/setup` first-owner bootstrap, 12-char minimum, refuses a second owner
- [ ] Next owns `GET /login` and `GET /setup`; both submit JSON to `POST /api/session` / `POST /api/setup` through the fetch wrapper, so both carry `X-CR8-Request: 1` and neither needs a CSRF exemption or a Caddy method matcher **[changed]**
- [ ] `DELETE /api/session` logout; `GET /api/session` whoami **[new]**
- [ ] Cookie-presence redirect in `proxy.ts` (convenience, never a boundary) **[new]**
- [ ] Settings as grouped rows: Account · Playback · Sign out · Danger **[new]**
- [ ] **DROP** the Settings Display section; density and default view are the `ListHeaderBar` toggles, persisted to `localStorage`

### Writes, undo, and history

- [ ] **Every write the client issues is under `/api/`**: `POST /api/songs/{ulid}/tags/toggle`, `/edit`, `/apply-neighbours`; `POST /api/reactions/{ulid}/heart|note|chip`; `POST /api/reactions/{id}/undo`; `POST /api/selection`; `POST /api/progress/{ulid}`; `POST /api/undo`; `POST /api/stems/{ulid}`; `POST /api/triage/{ulid}`; `POST /api/tags/rewrite`; `POST|PATCH|DELETE /api/collections*`; `POST /api/alerts/{id}/ack`; `/api/members`; `/api/session`; `/api/setup`. Nothing hits a bare `/selection`, `/undo`, `/progress` or `/songs/*` path, which keeps the Caddyfile fourteen lines and avoids a path collision with the Next `/songs/[song_ulid]` route
- [ ] `POST /api/selection` bulk: status · released + URL · multi-song tag add/remove · instr · collab · collection · stems
- [ ] Bulk writes never overwrite an existing human row
- [ ] **One `bulk` undo entry** capturing prior tag provenance and prior status / released_url / human_touched per song, not forty entries
- [ ] Write-without-navigation: only changed rows are patched into the query cache, and rows that fell out of the filter are removed from it
- [ ] JSON write paths must not reuse `_write_result()`, which runs `library_songs(limit=10_000)` twice per write
- [ ] Undo stack: heart · tag · field · bulk, session-scoped, survives reload, popped by `u` / Cmd-Z / any toast
- [ ] **Undo restores the exact prior `source`, `author` and `created_at`**, or deletes the row when there was none. Snapshotting the prior author is what keeps a derived tag derived after an undo
- [ ] Undoing a tag appends an `:audit:undo` reaction; history stays append-only and is never mutated
- [ ] `Nothing to undo.` on an empty stack; per-reaction undo for triage verdicts
- [ ] `POST /api/progress/{ulid}` on start, on `ended` with full duration, on queue removal, and at checkpoints; debounced, never on `timeupdate`
- [ ] `heard_s` monotonic; `started=true` appends the `playback_events` row that dig reads
- [ ] Heart append-only with soft delete, optimistic, `heart-pop` only on unset → set
- [ ] Chip reaction (always `dim='vibe'`) writes through to `song_tags` plus the audit reaction
- [ ] Every write returns a toast `… — undo`, auto-dismissing at 30 s

### Media and downloads

- [ ] `/m/{ulid}` Range-backed audio, FastAPI through Caddy, never through Node
- [ ] `/peaks/{ulid}` pre-generated peaks, never client-decoded, prefetched for the next track
- [ ] `/art/{ulid}` and `/art/song/{song_ulid}`; path containment on every artifact (symlink-poisoning defence) unchanged
- [ ] `Range` cap (200 chars / 4 ranges) unchanged; auth required on every byte
- [ ] Download original (best lossless `.wav > .aif > .aiff > .flac`) with the human filename preserved; mp3 · 320 named `{source stem}.mp3`; stem FLAC named `{source}-{kind}.flac`; labels carry extension and human size
- [ ] Streaming selection ZIP: data descriptors, UTF-8 names, on-the-fly CRC, never buffered
- [ ] Caps 50 files / 2 GB, `X-CR8-Included-Count` / `X-CR8-Trimmed-Count`, 413 when nothing fits
- [ ] Duplicate filename `-2` / `-3` suffixing; corpus path containment; `Cache-Control: private, no-store`
- [ ] **Every download is a programmatic `<a download>` click, including the ZIP.** `location.assign` is not sanctioned: a non-attachment response such as the ZIP's 413 renders as a page, which unloads the document and kills the persistent player

### Queue APIs

- [ ] `GET /api/library-queue` with the identical filter grammar, item shape fattened with `song_ulid`, `duration_s`, `era_css`, `peaks_url`, `artwork_url`
- [ ] `GET /api/dig` priority order (untagged + never-played → untagged → never-played → least recently played) with a random tie-break pre-shuffle
- [ ] `GET /api/tag-queue` (dim + value)
- [ ] `GET /api/tracks/{ulid}` fetched once per track **activation**, never per row; resolves stem ULIDs
- [ ] `replaygain` never appears in a list payload (mutagen ID3 parse per call)

### Keyboard

- [ ] `j` / `k` cursor, instant, moves the subject, focuses the row
- [ ] `Enter` plays the cursor row; double-click plays; single click only moves the cursor
- [ ] `Space` play/pause, animation suppressed
- [ ] `/` search · `t` first tag input on the subject · `d` download the cursor row's original
- [ ] `L` reveal now playing: centre, focus, flash 900 ms; on a miss, drop the excluding filter tokens visibly through the `ActiveFilterStrip` and retry, then toast `Now playing isn't in this view.` **[miss path is new]**
- [ ] `u` and Cmd/Ctrl+Z undo · `Esc` stop digging, or blur the focused field
- [ ] `1–9` tag deck on the subject
- [ ] `x` toggle selection on the cursor row · `←`/`→` seek · `↑`/`↓` volume · `m` mute · `?` help **[new]**
- [ ] Cmd/Ctrl-click facet multi-select
- [ ] All bindings suppressed while typing; modified keys ignored except Cmd/Ctrl+Z
- [ ] Roving tabindex: only the cursor row is tabbable, row checkboxes are `tabindex="-1"`
- [ ] Media keys via Media Session

### Cross-cutting

- [ ] `X-CR8-Request: 1` on every non-GET, from one fetch wrapper, enforced by lint
- [ ] No CORS middleware, ever; one origin
- [ ] `SecurityHeadersMiddleware`, `RangeLimitMiddleware`, `RateLimitMiddleware` unchanged except the `/m` `/peaks` `/art` exemption
- [ ] Runtime floors (Starlette ≥ 0.49.1, SQLite ≥ 3.53.2) and the 0600 / ≥32-byte session secret unchanged
- [ ] FTS quoted and length-capped; SQLite errors laundered into `SearchError`
- [ ] `docs_url` / `redoc_url` / `openapi_url` stay disabled in production; OpenAPI generated at build time from a local run
- [ ] Internal integer rowids (`song_id`, `bounce_id`) stripped from every payload
- [ ] snake_case on the wire everywhere, including `track_url` / `audio_url` (`_queue_items()` emits camelCase today and changes)
- [ ] Python remains the only writer to the catalog tables; `/healthz` unchanged
- [ ] **DEFER** the share modal and the inspector `ShareCard` to `specs/DEFERRED.md`; the inspector carries a documented seam

---

## 7. Build order

Thirteen steps, sixteen commits: step 4 splits into three and step 12 into two, because in both cases one
commit changed more than one thing that could break independently. Every step ends in something the
owner can open and use at `https://<machine>.ts.net:8443/app`, and the funnel on 443 keeps serving Jinja
until 12b. **From the end of step 4a the new app is the daily driver** for browsing and playing, so
every later step lands on software already in use rather than accumulating ten steps of unexercised
behaviour before anyone tries it.

### 7.1 What the running app shares

"The old app is never touched until cutover" would be false, so it is not claimed. Five steps change a
surface the running Jinja client depends on.

| Step | Shared surface | Why it is safe |
|---|---|---|
| 0 | rate-limit exemptions, the new login budget, Caddy in front of every byte | Additive; the cap is unchanged. Step 0's acceptance is the **old** app working end to end through 8443. |
| 1 | `/api/library-queue` and `/api/dig` item shape | Fields are added, never renamed. The four camelCase keys (`trackUrl`, `audioUrl`, `id`, `reason`) keep emitting beside their snake_case siblings until the templates are deleted. `normalizeItem()` already dual-reads all four, but that is luck, and luck is not a migration plan. |
| 6 | `_write_result()` | Bypassed, not modified. Jinja routes keep calling it; the JSON siblings never do. |
| 8 | `collection_tracks()` batched to one `WHERE b.public_id IN (...)` | Identical return shape, 472 connections down to one. The Jinja collection page is the only other caller. |
| 12a | Caddyfile catch-all, `/login`, `/logout` | Reversible by reverting one file. |

pytest exercises the server, not `playback.js`, so 73 green tests prove nothing about whether the old
player still plays. `scripts/smoke-old-app.sh` drives the same headless `browse` binary `ui-audit.sh`
uses, against Jinja on `:8443`. It logs in, plays a row, shuffles, digs, seeks mid-track, removes a
queue row, toggles a chip and undoes it, and it runs at the end of every step in that table.

---

### Step 0 — Runtime, front door, supervision

Node 24 LTS by absolute path, pnpm 10, `apps/web` scaffolded with `basePath: '/app'`,
`output: 'standalone'`, `outputFileTracingRoot: __dirname` and `allowedDevOrigins` (§2.5). Caddy with
the §2.1 Caddyfile routing **everything** to 8080, both new LaunchDaemons and `deploy-web.sh` from §2.6,
`tailscale serve --bg --https=8443`. Plus §2.4's two config lines: `/m`, `/peaks`, `/art` exempted from
the rate limiter with the cap left at 240, and the username-keyed login budget. They land here, not with
the grid at step 9: they depend on nothing, and leaving media unexempted through eight steps of
development against a 472-row list is a latent afternoon.

**Acceptance.** `node -v` reports 24.18.x from the path in the plist. `lsof -nP -iTCP:8443 -sTCP:LISTEN`
shows Caddy on `127.0.0.1` only while `tailscale serve status` shows the 8443 proxy up.
`sudo launchctl kickstart -k system/com.cr8.cr8-caddy` restores the front door and killing Caddy
brings it back within five seconds unattended; a hand-started `caddy run` does not count. `:8443/` serves Jinja
with working login, playback and seeks, and `curl -sI` on a `/m/{ulid}` Range request returns 206 with
`Content-Range`. The health monitor moves to 8443. Eleven failed logins for one username return the
generic 401 while a second username signs in. The funnel on 443 is untouched.

### Step 1 — The reads something renders today

`GET /api/session`, `GET /api/library`, and the fattened `/api/library-queue` and `/api/dig`. **Every
other read moves into the step that renders it.** A read shipped five steps before its screen is a
Pydantic model for a payload whose shape is not settled. `apps/web` gets one unstyled `/app` route that
prints 472 titles.

Four binding rules. (1) `/api/library` never calls `bounce_download_options()` per row; today `GET /`
does, which is ~950 queries and ~950 `stat()`s at 472 rows, and sizes move to `/api/downloads?ulids=`.
(2) The row projection gains `hearted`, `has_original` and `original_size`, each as one batched query.
`LIBRARY_SQL` projects only `unheard`, which is why the shipped shared tail blanks every heart to
`aria-pressed="false"` and why its `↓` has no per-row source. Both are projections over existing rows.
(3) `replaygain()` parses ID3 per call and lives on `/api/tracks/{ulid}` and nowhere else, forever.
(4) Every `/api` route declares a Pydantic `response_model`; FastAPI emits an empty schema for a bare
`JSONResponse`, which `openapi-typescript` renders as `unknown`, against which "no `any`" passes
vacuously.

**Acceptance.** pytest green with zero modifications and `smoke-old-app.sh` passing. `/api/library`
returns **every** row of the filtered result with `total`, `facets`, `counts` and an ordered `queue`; a
test inserting a 501st song asserts `rows.length === total` and that nothing silently disappears. Under
20 SQL statements per request, under 250 ms, under 400 KB. No `song_id` or `bounce_id` in any payload.
`grep ': unknown'` over the generated response types returns zero. `:8443/app` lists 472 titles.

### Step 2 — Shell, tokens, primitives, audit harness

The §5.0 shell with a real TopBar, an empty PlayerDock and a placeholder work area; `tokens.css`
verbatim; Instrument Sans self-hosted at 400/500/650; Radix installed; only the atoms the shell needs.
Caddy moves `/app/*` to 3100. `ui-audit.sh` takes `CRATE_UI_AUDIT_BASE` and a page list. Rule 2
(`small-target`) takes the larger of the element's rect and its `::before` rect, or every honest 32px
chip with a 40px pseudo hit area reports a false medium and the report becomes one nobody reads. The
selectors `.chip`, `[data-tag-filter]`, `.tag-toggle`, `.rail-filter` become part of the component
contract, because renamed, rule 4 matches nothing and passes silently. And:

> **`inflated-chip`, `heavy-dom` and `font-sprawl` are promoted from `"medium"` to `"high"`, and
> `font-sprawl`'s threshold changes from `famNames.length > 3` to `> 1`.** Three one-line edits, and the
> difference between §8.2 being a gate and being decoration: the gate is zero *high*, and those three
> rules are the stated demonstrations for r6 §3, §6 and §10, the three defects this port exists to fix.
> At medium they cannot fail anything, and `font-sprawl` at `> 3` is silent at two families, so it could
> never report the "one family" it is cited for.

**Acceptance.** `:8443/app` renders the shell, dark, correct ground, one font family, no more than five
distinct radii, zero high findings. Devtools shows 200s on every `/_next/static/chunks/*` through
`:8443`. That is the `allowedDevOrigins` proof, whose failure mode is a dev app that loads nothing. A
Radix `Menu` inside an `overflow: hidden` ancestor portals to `document.body`, does not clip, traps
focus, closes on Escape and restores focus to its trigger. Each audit selector matches an element.
`0123456789` at 400/500/650 measures to equal widths, or `--font-numeric` falls back to the unloaded
system `--font-mono` for numeric cells. Retiring the mono webfont assumes Instrument Sans ships `tnum`,
and columns of digits jitter if it does not.

### Step 3 — The player, standalone

Ships `POST /api/progress/{ulid}`, the write behind the unheard badge, the unheard filter and the whole
dig ordering, sent on start, `ended`, queue removal and checkpoints, never on `timeupdate`. Then the
§5.0 store and queue machine, sessionStorage persistence, the silent-WAV unlock, the `loadSequence`
guard, Media Session, `warmNext`, the cross-faded transport glyph, volume, level-match defeat, and the
time readout through a throttled `useSyncExternalStore`, never in the store, or every tick re-renders
the list. Shuffle, reshuffle and repeat get an explicit dock cluster between volume and the queue
toggle, budgeted into the 80px row; they currently share a `<details>` with the tagbar and queue list
that §5.14 moves away. `/app/player-lab` lists 20 tracks so all of it is exercisable before the library.

**The audio graph is one module singleton, and this is correctness, not style.** `lib/audio.ts` owns the
element, the `AudioContext`, the `GainNode` and the single `createMediaElementSource` behind
`let graph: AudioGraph | null = null`. A second call on the same element throws `InvalidStateError`, and
an element routed into a *suspended* context is silent with nothing in the UI to say why. Construct
lazily inside a real user gesture; `ctx.resume()` on every play. Level match is feature-detected off on
Safari, not UA-sniffed off on iOS. macOS Safari is the primary browser, and Web Audio there also takes
the element off the native AirPlay path.

**Acceptance.** Transport, seek and hardware media keys work; playback survives a client-side navigation
with no gap; `previous()` restarts above 3 s; the queue survives a reload. Two StrictMode mounts produce
exactly one `MediaElementSource`, the first play after a cold load is audible with
`ctx.state === 'running'`, and the level-match toggle is disabled until the graph exists. Playing after
an `await` on a queue fetch works on the first gesture. Skipping through ten tracks never leaves the
dock describing a track other than the one sounding. `currentTime` causes zero re-renders in 20 rows.

### Step 4a — Rows, playback, and the daily-driver switch

§5.2's `grid-template-columns` declaration, the `RowGutter` number → ▶ mechanic, the single shared
read-only `RowTail` (version badge · `…`, with the heart's grid slot reserved and empty so nothing
reflows at step 6), four row states on four properties, `content-visibility: auto`, roving tabindex, and
the search input bound to `q`. The `…` menu carries only what is reachable now: play next, add to queue,
reveal in list, open detail, download original, download mp3.

**The row cover is a CSS era field, not an `<img>`.** At 36px the Pillow-rendered title is illegible
anyway, it is what ships today, and it is 472 requests per paint that never happen. `/art/song/{ulid}`
and lazy `<img>` arrive with the grid at step 9.

**Activation seeds the queue.** Gutter ▶, Enter, double-click and `…` → play all replace the queue with
the payload's `queue` array and set the cursor to that row's index; `add to queue` and `play next` are
the only paths that mutate an existing queue. Today two play paths on the same row build two different
queues, one of them a dead end of length 1. The owner switches at the end of this step.

**Acceptance.** Zero high findings at 1440×900 with a row hovered and a `…` menu open; under 3000 DOM
nodes; 24 rows visible. Across 472 rows from 900 to 2560 px the tail's `left` is ≥ the title's `right`.
Nothing scrolls sideways. First paint issues zero `/art` requests and no 429, measured in the browser
network log, not with `curl -w`, which does not fetch subresources. After activating row N,
`queue.length === total` and `cursor === N`. The list scrolls the sounding row into view on every advance.

### Step 4b — Filters, and reveal that actually reveals

§5.2's `FilterRail` and `ActiveFilterStrip`, all filter state URL-encoded so back/forward works and the
queue endpoints take `window.location.search` verbatim, `Shuffle these {N}`, the dig and shuffle
gestures, and the list reflow into queue order.

**Reveal gets its miss path.** When the sounding track is not in the current result set —
`Shuffle everything`, then filter to `unheard` — `L` and the `NowPlayingPill` click both drop the filter
tokens that exclude it, mutating the `ActiveFilterStrip` so the change is visible, then centre, focus and
flash for 900 ms. If it still cannot be found, the dock toast reads `Now playing isn't in this view`.
Today both do nothing at all, silently.

**Acceptance.** `Shuffle these N` matches the result count across five filter changes. Cmd-click adds a
second value within a dimension and the URL shows both. A dig started under a filter refills from
`/api/dig` with those filters. Reveal from a filtered-out state clears exactly the excluding tokens and
lands on the row. Zero high findings.

### Step 4c — Sort, density, persistence

`SortHeaderCell` across all 14 server-side sort modes, asc → desc → off — the Python `header_sorts`
computation exists and no template has ever rendered it. Density toggle. Scroll restoration for the
list, the rail, each facet group, the inspector and the version rail. Selection by checkbox and
shift-click, surviving a filter change. `BulkTagBar`'s idle branch as a distinct render tree; its write
verbs arrive at step 6.

**Acceptance.** Applying a filter and clearing it restores scroll and the checked selection, all five
containers within 1px. `random` renders as a scramble button beside shuffle, not a sort option. The idle
`BulkTagBar` render contains exactly one text node. Zero high findings.

### Step 5 — The right inspector

Ships `GET /api/songs/{ulid}` and `GET /api/downloads?ulids=`, then §5.4's composition with optimistic
first paint from the row's own data. The `TagPanel` slot renders a skeleton until step 6.

**The subject is state, never a DOM query.** `cursorUlid`, `playingUlid` and
`follow: 'cursor' | 'playback'` live in the store. `follow` snaps to `'playback'` whenever the mode is
`dig` or playback advances without an intervening cursor move, and back to `'cursor'` on the next
`j`/`k`. One derived `subjectUlid` drives the inspector, and step 6 binds `t` and the `1–9` deck to it.
During a dig you are listening, not pressing `j`/`k`, so a cursor-only inspector parks on whatever row
you last touched and the tag keys write to the wrong song, this document's own stated worst outcome.
The subject title prints in the inspector header so the target is never ambiguous.

**`<Activity/>` preserves state and DOM, not scroll.** It hides with `display: none`, destroying the
scroll container's layout box, so `scrollTop` returns as 0. The inspector and the `VersionRail` capture
it in a `useLayoutEffect` cleanup and restore it in a `useLayoutEffect` on show.

**Acceptance.** `j`/`k` moves the cursor and the inspector follows with no empty flash; while digging it
re-targets the sounding song on every track change. Pinning freezes it against both cursor moves and
track changes; unpinning snaps back immediately. It never shows fields from two songs at once, asserted
by rendering `subjectUlid` into a `data-` attribute and comparing it against every rendered field's
source. A hide/show cycle restores both `scrollTop` values to within 1px. Zero high findings, and
specifically zero `clipped-text` inside the 380px panel.

### Step 6 — Tagging and the write API

The largest behavioural step, and the one with no reachable server endpoint until now. It ships the JSON
write surface: `POST /api/songs/{ulid}/tags/toggle`, `POST /api/reactions/{ulid}/heart`,
`POST /api/selection`, `POST /api/undo`, `POST /api/reactions/{id}/undo`, plus `GET /api/vocabulary` and
`GET /api/songs/{ulid}/tag-panel`. Every one exists today only outside `/api/*`, where the Caddyfile does
not route it and where `POST /songs/{ulid}/tags/toggle` collides on path with the Next route
`/songs/[song_ulid]`. They are `/api` siblings with the same handler bodies, which is what keeps §2.1's
Caddyfile two handles long instead of needing a method matcher on an auth-adjacent path. They do not
call `_write_result()`.

Client side: §5.6's `TagPanel`, unconditional posts so an applied chip can be un-pressed, optimistic
`aria-pressed` with rollback, `TypeaheadInput`, `KeeperSegment`, the `1–9` deck bound to `subjectUlid`
with digits printed on the slots, `t`, the chip triple-action, `BulkTagBar`'s active branch, and undo on
`u`, Cmd/Ctrl+Z and the toast. Mutations run `onMutate` → `setQueryData` → `onError` rollback →
`onSettled` invalidate, rail counts included. The row tail's reserved heart slot fills in here.

**Acceptance.** Toggling an applied chip removes the tag, which works nowhere in the app today. A chip
toggle animates nothing, asserted as `transition-duration: 0s` on the fill. A failed write reverts the
chip and raises an error toast. Undo restores the exact prior `source`/`author`/`created_at` against the
database and appends an `:audit:undo` reaction rather than mutating history. A bulk write across 40 songs
is one undo entry covering both tag and field changes. Rail counts move with the write and revert with
the undo. `1–9` and `t` write to the printed subject, asserted by digging to a track without touching
`j`/`k` and pressing `1`. Every mutation routes through the one fetch wrapper and carries
`X-CR8-Request: 1`. Zero high findings with the panel open.

### Step 7 — Song detail, `Listen | Manage`, stems

Ships `GET /api/songs/{ulid}/stems`, `GET /api/jobs/stems`, `POST /api/stems/{ulid}`,
`POST /api/reactions/{ulid}/note`, `POST /api/songs/{ulid}/edit`,
`POST /api/songs/{ulid}/apply-neighbours`, and §5.5's two compositions with mode in the URL. `StemsList`
gets its four action states, phase labels and 5-second polling.

**Acceptance.** The waveform renders from `/peaks/{ulid}` and `decodeAudioData` is never called, asserted
by stubbing it and failing on call, run with the peaks request artificially delayed, which is the case
that actually fires. Clicking the waveform seeks the shared element rather than creating a second source.
Switching Listen ↔ Manage does not interrupt playback and survives reload. Enqueuing a separation returns
202, shows `queued`, polls, and stops polling on completion. A single-bounce song renders the
single-version rail without looking broken. Every download is a programmatically created `<a download>`
click, never `location.assign`: the selection ZIP's 413 has no `Content-Disposition`, so navigating to
it unloads the document and kills the player. Zero high findings in both modes.

### Step 8 — Collections, triage, tags desk, activity

`/app/collections`, `/app/collections/[ulid]`, `/app/triage`, `/app/tags`, and `/app/activity`, which
refetches `GET /api/activity` on mount and on window focus. There is no `EventSource` in v1 and no
`/activity/events` handle in the Caddyfile. Reorder is `Move up` / `Move down` in the `…` menu plus the
keyboard path; §3 drops drag from v1. `collection_tracks()` is batched here, with its endpoint. Writes:
`POST/PATCH/DELETE /api/collections`, `PUT .../order`, `DELETE .../tracks/{ulid}`,
`POST /api/triage/{ulid}`, `POST /api/tags/rewrite`, `POST /api/alerts/{id}/ack`.

**Acceptance.** `POST /api/collections` with only a name is a TypeScript compile error and a 400 at
runtime. A queue equal to the whole library requires explicit confirmation. Menu reorder persists across
reload. A collection page shows its own count and updates it on removal. `/api/collections/{ulid}` opens
exactly one SQLite connection. `/app/tags` shows 15+ rows and `/app/collections/{ulid}` 15+ track rows at
1440×900. Zero high findings on all four.

### Step 9 — The grid library

The grid/list toggle, `LibraryGrid`, `CoverCard`, and `GET /art/song/{song_ulid}` with
`Cache-Control: private, max-age=86400` and an ETag. The rate-limit exemption landed at step 0.

**Acceptance.** 472 covers load at 1440×900 with no 429, no layout shift, and first contentful paint
under 1.5 s on the tailnet. Toggling grid ↔ list preserves cursor, selection, queue and playback. Cover
titles never wrap mid-word. Zero high findings.

### Step 10 — Settings, members, auth siblings, keyboard help

`/app/settings` per §5.12, absorbing `/members`; `/app/login` and `/app/setup` on the bare shell; the `?`
overlay; `x` select, `←`/`→` seek, `↑`/`↓` volume, `m` mute. Auth siblings: `POST /api/session`,
`DELETE /api/session`, `POST /api/setup`, `GET/POST /api/members`, `DELETE /api/members/{id}`.
`POST /api/setup` needs no CSRF exemption: the setup page posts through the same fetch wrapper as every
other write, so it carries `X-CR8-Request: 1` (§2.3). Caddy still routes `/login` to 8080 here,
because Jinja is still the front door on 443; the Next login page is built and tested but not yet the
one users reach.

**Acceptance.** A new member is created, sees the generated password exactly once, and signs in from a
private window; self-removal is refused. `POST /api/setup` succeeds against an empty database rather than
403ing, which is a live bug in the shipped app. Every settings row has exactly one control, asserted by
counting interactive descendants. Login fields have a visible border and focus ring at 1440×900 and
390×844. Every shortcut in the `?` overlay fires, asserted one by one. Zero high findings.

### Step 11 — Parity sweep and quiet period

Walk §6 line by line; anything unchecked is built now or moved to a written deferred list with a reason.
The app has been the daily driver since step 4a, so this is a sweep, not first contact.

**Acceptance.** Every §6 checkbox is ticked or deferred with a reason. `ui-audit.sh` across `/app`,
`/app/tags`, `/app/collections`, `/app/triage`, `/app/settings` reports zero high on every page. Every
row of §8.2 is demonstrated. Then **three consecutive quiet days**, with the reset rule stated: only a P1
restarts the clock — a crash, stuck playback, a lost write — and everything else is logged and fixed
without resetting. An open-ended "seven days with no defect" becomes a month, because every fix restarts it.

### Step 12a — The app moves, the funnel does not

Drop `basePath: '/app'`. Move the Caddy catch-all from 8080 to 3100. Delete the `/login` and `/logout`
handles so Next owns `GET /login` and `GET /setup` and the forms post to `POST /api/session` and
`POST /api/setup`. **Keep `handle /static/*`**: Jinja is still mounted and loads `/static/owner.css` and
`/static/owner.js`, so without it the pages that still render render unstyled. This commit touches every
internal link, every `router.push`, `proxy.ts`'s redirect target and any URL assumption in the persisted
queue. It is where the bugs are, and the old app is on 443 throughout, so it is verified at leisure.

**Acceptance.** `:8443/` serves the Next app at the root. A cold browser profile lands on the **new**
`/login`, signs in, and arrives at the library. `/setup` is reachable and its POST is not 403ed. Audio
plays and seeks. A queue persisted before the basePath drop either restores or is discarded cleanly; it
never restores a broken URL. `https://<machine>.ts.net/` still serves Jinja.

### Step 12b — The funnel moves

```sh
tailscale funnel --bg --https=443 http://127.0.0.1:8443    # was 8080
```

**Rollback is one file, not one command.** `git revert` the 12a Caddyfile change, putting the catch-all
back on 8080 and restoring `/login` and `/logout`, then
`sudo launchctl kickstart -k system/com.cr8.cr8-caddy`.
That restores Jinja on **both** 443 and 8443 at once, which keeps the side-by-side rig alive at exactly
the moment you need it to work out why you reverted. Repointing the funnel back at 8080 is faster and
also works, but it leaves `:8443` serving a Next app with `basePath` already dropped and no Jinja
anywhere. Either way FastAPI, the database, the mirror and the session cookie are untouched.

**Acceptance.** `https://<machine>.ts.net/` serves the Next app, login works from a cold profile, and
`curl -sI` on a Range request through 443 returns 206. The rollback above is **actually performed once**,
verified to restore a working Jinja login and playback on both ports, and then re-cut over. The Jinja
templates stay mounted on 127.0.0.1:8080 for one month; deleting them, the four camelCase queue keys and
the `/static/*` handle is a later commit.

---

## 8. Acceptance criteria

### 8.1 Global gates

- `scripts/ui-audit.sh` reports **zero high-severity findings** on every page the step touched, at
  1440×900, with a row hovered and an overlay open. This is the only gate, which is why step 2 promotes
  the three rules §8.2 depends on. Medium and low findings are recorded in `reports/ui-audit.md` with a
  written disposition; they do not block, but an undocumented medium does.
- No `transition: all`, and no colour, radius, duration or size literal outside `tokens.css`, enforced
  by stylelint. No numeric `--text-N` inside `@theme`: Tailwind v4 maps `--text-*` to font-size, so
  re-exporting the colour ladder there emits `.text-2 { font-size: oklch(…) }`.
- `prefers-reduced-motion` keeps colour and opacity and drops travel and scale, implemented once by
  flipping four tokens inside the media query so no component carries a branch.
- Every pressable element scales `var(--press-scale)` on press, including anchors, excluding full-width
  rows. Every text/background pair meets 4.5:1 body and 3:1 large. One font family loaded,
  `font-synthesis: none`, `tabular-nums` inherited from `html`.
- pytest green, `smoke-old-app.sh` passing, and nothing writing to `catalog.db` from Node.

### 8.2 The r6 defects are structurally impossible

Demonstrated, not asserted. Every rule named here emits at `"high"` after step 2.

| r6 | Structural guarantee | Demonstration |
|---|---|---|
| §1 row controls collide | The tail is a `grid-template-columns` track at `var(--row-tail-w)`; the title track is `minmax(0, 1fr)`. No absolute positioning in the row. | 472 rows at 900–2560 px: the tail's `left` is always ≥ the title's `right`. |
| §2 panel clips horizontally | Chip containers are `flex-wrap: wrap`, no `overflow-x`, no fixed-width children. | `clipped-text` (high) returns zero inside `.inspector` at 380px; `scrollWidth === clientWidth`. |
| §3 controls too big | Heights come only from `--control-h` 32, `--row-h` 44, `--tag-row-h` 40. Components take no height prop. | `inflated-chip` (high) returns zero; screenshots confirm 15+ tag rows and 15+ collection rows at 1440×900. |
| §4 stray glyph + overlapping provenance | `TagChip`'s props are `{value, active, source, onToggle}`. No `children`, no slot. Provenance renders once from `TagGroup`, below. | `TagChip` renders exactly one text node. |
| §6 mixed type grammar | One family loaded; `text-transform: uppercase` banned by stylelint. | `font-sprawl` (high, threshold `> 1`) reports one family; grep for `uppercase` returns nothing outside the `--tracking-label` comment. |
| §8 bulk bar noise | `BulkTagBar` returns a different element tree at `selection.size === 0`; the cap note is a child of the active branch only. | The idle render contains exactly one text node. |
| §10 438 KB library page | A React shell fetching JSON; the tail exists once in the document. | Document under 120 KB, JSON under 400 KB, and `heavy-dom` (high) returns zero at the 3000-node threshold. |
| §11 collections | `source` is a required discriminated union in the request type. | `POST` with a name alone does not compile; a runtime test asserts 400. |

### 8.3 The at-risk behaviours have named tests

Each in the step that ships it: silent-WAV unlock across an `await` · one `MediaElementSource` per
document · `loadSequence` out-of-order guard · queue seeded from the payload on every activation path ·
list reflow and sounding-row scroll on shuffle, dig and advance · the single shared row tail · scroll
restoration for all five containers · selection survival across a filter change · optimistic
`aria-pressed` with rollback · the frequency gate (`transition-duration: 0s` on chip, cursor,
play/pause) · exact undo provenance restoration · one undo entry per bulk write · dig refill with the
originating filters · dig reason strings carried into the player · `warmNext` prefetch · pin snap-back ·
`1–9` and `t` targeting `subjectUlid` · reveal's filtered-out miss path · Cmd/Ctrl-click multi-select ·
the `— untagged —` pseudo-facet · `skip_sketches` · released excluded by default · the chip
triple-action · ZIP cap on both sides · downloads via `<a download>` only · stems as first-class
playable tracks · `X-CR8-Request` on every write · `previous()` restart above 3 s · per-actor unheard
state · 30-second toast dismissal · roving tabindex.

---

## 9. Risks

Only what can still go wrong after this plan is followed. Hazards already closed structurally in §2,
§3 and §5 — the CSRF header, the Secure cookie in dev, Range through the proxy, the store leaking to
a server component, virtualisation versus the keyboard cursor, the shared undo stack — are not
repeated as risks here.

**The `/api/library` ceiling.** Era, key and `skip_sketches` filtering happens in Python *after* the SQL
`LIMIT`, so any data cap truncates the candidate set before filtering and shortens the `queue` array,
which is the exact divergence this port exists to fix, arriving silently as the archive grows. Mitigation:
`total` and `truncated` ride in the payload, the client banners when `rows.length !== total`, and step 1
inserts a 501st song. Any ceiling is a virtualisation trigger on row count, never a data limit.

**The audio graph.** `createMediaElementSource` may be called once per element per document, and an
element routed into a suspended context is silent with no error anywhere; StrictMode and Fast Refresh
both make a second call easy. Mitigation: one module singleton behind a module-level guard rather than a
`useEffect`, constructed only inside a user gesture, `ctx.resume()` on every play, two tests at step 3.

**`<Activity/>` and scroll.** It hides with `display: none`, destroying the layout box; it preserves
state, not scroll offsets, and two of the five scroll containers live inside the inspector. Mitigation:
explicit `scrollTop` capture and restore, asserted to within 1px.

**`response_model` is real work.** Pydantic models for the API surface are plausibly the largest single
chunk in the port and are invisible in a one-line acceptance criterion. Mitigation: models are written
only for the endpoints a step actually ships, which is why the reads are distributed across steps 1, 5,
7 and 8 rather than front-loaded.

**The `1–9` rebinding.** Moving from "the first nine chips of the focused song" to a managed deck changes
the highest-frequency gesture in the app. Mitigation: the deck derives its default from the most-used
vocabulary values, the digits print on the slots so the mapping is never invisible, and the subject title
prints in the inspector header so the target never is either.

---

## 10. Open questions for the owner

Only the decisions this document cannot make for itself. Everything else above is settled.

1. **The share surface.** The audit calls the share modal "what we need most", and r6 §7 deletes the
   share nav item and the guest app. v1 therefore ships neither the modal nor the inspector
   `ShareCard`, only a documented seam. Confirm that is right, or pull the modal back in as its own
   step.
2. **The one sans family.** §4 names Instrument Sans, self-hosted at 400/500/650. Confirm the choice,
   and confirm the fallback: if it ships no `tnum`, numeric columns fall back to the unloaded system
   mono stack, which partly re-opens the thing the audit called our biggest tell.
3. **`1–9`.** The deck replaces "the first nine chips of the focused song" with nine slots configured
   by use. It changes the highest-frequency gesture in the app, and it is the one change here that
   only your own muscle memory can accept or reject.
4. **Level match.** The `GainNode` is feature-detected off on any Safari, and macOS Safari is the
   primary browser, so ReplayGain would effectively never run for you. Keep the Web Audio graph in v1
   with its singleton, unlock and `resume()` machinery, or drop it and re-open it later.
5. **Starting vocabulary.** The archive holds three human tags across two vibe values, and no
   hardcoded vocabulary may write to the database. Seed your own vibe / instr / collab / use lists
   before step 6, or start from the empty state and let the typeahead accumulate?
6. **Node 24 machine-wide.** Step 0 replaces the EOL Node 23 on this Mac and adds Caddy as a new
   supervised front door. Confirm nothing else on the machine pins 23.x and that nothing else wants
   8443.
7. **Reorder in v1.** Collections and the queue reorder through `Move up` / `Move down` in the `…`
   menu and the keyboard only; drag costs three dependencies and is deferred. Acceptable for the one
   collection that exists?
8. **Cutover scope and the Jinja tail.** Step 8's four surfaces (collections, triage, tags desk,
   activity) are the least-used in the archive today — do they gate cutover, or ship after it? And
   how long do the Jinja templates, the four camelCase queue keys and the `/static/*` handle stay
   mounted past 12b? One month is written down, but nothing forces it.
