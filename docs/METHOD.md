# How cr8 got built — the process, distilled for reuse

Written 2026-08-01 for an agent whose job is to turn this into a repeatable
skill for building other products. Everything here happened in this repo; every
claim is checkable against the commit log, `ops notes (private)`, `project history (private)`,
and `specs/`. The day this document describes shipped, in one session: an
upload-pipeline fix, a date backfill, a mobile player fix, an in-app queue,
public share links, collections CRUD, a discovery page, a bulk tag type-ahead,
live listening presence, a 38GB two-directory archive ingestion with new
multi-root corpus support, a full library filter-bar redesign, an inspector
redesign, a download consolidation, generative cover art in two generations,
and three live production bugs found and fixed mid-run.

## The shape of the whole thing

1. **State lives in documents, not in anyone's head.** `ops notes (private)` is
   rewritten at the end of every working session with two audiences: the owner
   (what changed, what needs them) and the next agent (how this codebase fails,
   what is load-bearing, what mistakes were already made — kept verbatim
   session over session). `specs/` are binding. `project history (private)` is the honest
   backlog with dated session logs. A session starts by reading these, not by
   asking questions.

2. **Research before planning, fanned out in parallel.** Before the day's plan
   existed: one agent inventoried the product surface (every page, every API
   endpoint without a frontend, every table with data no UI reads), one agent
   hunted quick wins (specs vs code, legacy-only features, dead ends), one
   external model gave an independent ideation pass, and root-cause agents dug
   the three reported bugs to file:line. Research reports carry evidence
   (`file:line`, live counts, measured behavior), never vibes. Cross-check
   them against each other — the external pass proposed a feature that had
   shipped the day before.

3. **The owner's feedback dump becomes one master plan with waves.**
   Bugs first, then features, then UX polish. Every ambiguity became a
   decision brief: 2-4 options, a recommendation with a reason, one tap to
   answer. Batched, never dribbled. The plan is a living file — owner adds
   ("dig should just be added too") land in the right wave with renumbering,
   mid-run feature requests get designed first (see 6), and the plan records
   every decision with its date.

## The execution engine

4. **Judgment and typing are different jobs.** The orchestrating agent keeps:
   specs, architecture decisions, diff review, visual validation, git, deploys,
   and production verification. Implementation dispatches to a coding agent
   (Codex here) running in an **isolated git worktree per task**, in parallel
   when tasks touch disjoint files, serialized when they share a file (the
   library page was the contention point all day — collections → tag input →
   filter bar ran in strict sequence while server-side work ran beside them).

5. **Every dispatch carries a SPEC.md.** Objective in one paragraph; the exact
   acceptance command that must go green (`PYTHONPATH=$PWD <venv-python> -m
   pytest -q` plus `npx tsc --noEmit` here); the design intent for anything
   visual (layout, states, tokens, tap targets); explicit hard-won context
   ("Next's plain-array rewrites are checked after the filesystem, so POST to
   a page path 405s — use API aliases, not a rewrite"); and a **Do NOT touch**
   list. Unspecced dispatches come back wrong and cost more than the spec.
   Findings from research go into specs verbatim — the implementer should
   never rediscover what the researcher proved.

6. **New feature ideas get designed before they get built.** A mid-run "I want
   to see who's listening" became: a mock in the product's own design tokens
   with real usernames and track titles, three placement options, a
   recommendation, three one-tap decisions — and only then a dispatch. When
   the owner rejected all three generated cover styles and posted reference
   screenshots, the response was rendered samples from their actual audio in
   four palettes plus a live CSS mock of the animation mechanism, then two
   decisions, then the build. Concrete beats abstract; the owner judges
   pixels, not paragraphs.

7. **Review is real.** Every returned diff gets read — the security-sensitive
   ones line by line (the public share surface got a token/oracle/scope
   audit), the mechanical ones structurally. Two dispatches hung silently and
   were killed and re-dispatched; one committed its SPEC.md against
   instructions twice and main got cleaned both times. Trust the suite, read
   the diff anyway: the presence build shipped a wrong-variable bug ("you ·
   you") that only a rendered screenshot caught.

## The verification religion

8. **Reproduce before reading code; prove on production after deploying.**
   Every feature was verified against the live site with a real session:
   upload proven by pushing a 15MB body through the full proxy chain (and a
   110MB body to find the CDN's real ceiling, which changed the UI copy);
   share links minted, streamed logged-out with a Range request, revoked, and
   confirmed dead; collections created, mutated, deleted; test data always
   removed after. When the owner said "it's kind of broken" the answer was a
   screenshot and a network log within two minutes, not a question.

9. **Budgets are gates, not aspirations.** The perf probe runs ×3 before any
   verdict (it's noisy under load, and dev-server numbers are meaningless —
   only production builds count). The day's biggest UI change shipped
   marginally *faster* than baseline. Anything animated must be
   compositor-only, concentrated where the eye is (the playing row), with a
   reduced-motion behavior that is gentler, not none.

10. **Hunt the silent failure.** This codebase's signature bug is a feature
    that does nothing and says nothing: unproxied routes, fire-and-forget
    fetches, a rate limiter that counted cover images and starved the app,
    a discovery page that made 650 API calls to render once, backfills that
    "rendered 6" into an empty directory. The countermeasures are structural:
    a test that fails if any frontend fetch lacks a proxy rewrite, checked
    responses everywhere, counters that distinguish rendered from skipped,
    and end-to-end proof as the only definition of done.

11. **Guards that refuse plausible disasters.** The scanner refuses a mass
    disappearance (it once declined to delete 297 tracks during a half-done
    copy). The same guard was extended per-root when archive roots landed. A
    missing directory must never read as a deletion.

## The cadence

12. **Merge → full suite → deploy → live-verify, per feature.** Small deploys
    all day (a dozen-plus), each independently verified, meant the owner's
    three live bug reports were reproduced, root-caused, fixed, and redeployed
    same-hour without destabilizing anything else. The test count only ever
    goes up (305 → 356 across the day).

13. **The owner's time is spent only on judgment calls.** Taste decisions
    (palette, placement, tap behavior), scope confirms (what gets ingested),
    and verdicts on rendered previews. Everything else proceeds autonomously
    with periodic one-paragraph updates — or silence, when asked, with one
    ping at the end. Preferences observed once become durable rules
    (design-first for new features; batch the questions; updates as things
    land).

## What to turn into the skill

- The wave structure (bugs → features → polish) applied to any feedback dump.
- The parallel research fan-out with evidence-bearing reports, run before
  planning anything.
- The spec-dispatch-review loop with worktree isolation and file-contention
  sequencing.
- The design-gate for anything the user will look at, mocked in the product's
  real design system with their real data.
- The verification battery: acceptance commands in every spec, production
  proof with real sessions, budget gates ×3, test-data hygiene.
- The handoff document format: owner summary / next-agent warnings /
  load-bearing list / mistakes carried forward verbatim.
- The failure taxonomy to hunt on any codebase: silent no-ops, unproxied
  paths, unchecked responses, lying counters, N+1s behind rate limits,
  first-draw-before-layout rendering.

## Artifacts to mine in this repo

- `ops notes (private)` — the state-handoff format, including "how this codebase
  fails" and "mistakes from the previous session, still true".
- `project history (private)` — dated session logs showing the honest-backlog style.
- `specs/` — seventeen binding specs; compare SPEC-dig or SPEC-stems against
  their implementations.
- `tests/web/test_proxy_coverage.py` — a repeated bug turned into a structural
  guard.
- Today's commit log (`git log --since="2026-08-01"`) — the cadence, the
  commit-message voice (what failed, how it presented, why the fix is right),
  and the three live-bug fixes are all visible in it.
