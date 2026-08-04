# SPEC v2 — desktop-first cr8, one link, tagging as the point

Owner's direction, verbatim in effect:
- **"Forget even the phone."** Desktop is the primary surface. Phone must still work, but the
  desktop layout is designed first and is not a stretched phone column.
- **"Delete this idea of a small batch."** One link type. If you have the link, you see
  **everything**. Batches/listen-throughs are removed as a concept.
- **"Revisit some of your first designs from the bake-off. They were way better looking,
  way smoother."**
- **"How do I even tag stuff? That was the initial entire point."**

Read before building: `reports/qa-2026-07-30.md` (observed bugs, with measurements),
`canon/ratifications/2026-07-29-crate-design.md` (binding tokens), and the mockups
`canon/references/mockups/{B-vault-balanced,B-vault-pole,A-console-balanced}.html`.

## 0. The damning fact to fix first

At 1440×900 the app renders everything in a ~620px left column and leaves **the right two
thirds of the screen empty black**. There is no desktop layout. This is the top priority.

## 1. Desktop layout (design this first, ≥1100px)

Three regions, full-bleed, no dead space:

```
┌──────────────────────────────────────────────────────────────────────┐
│ cr8            Library · Tags · Collections · Triage · Share       │  56px bar
├───────────────┬──────────────────────────────────┬───────────────────┤
│ FILTERS       │ LIST (the cr8)                 │ DETAIL / NOW      │
│ rail 240px    │ fluid, fills all remaining width │ 380px             │
│ sticky        │ dense rows, virtualized          │ sticky            │
│ era/key/      │ columns: title · era · key ·     │ cover, version    │
│ status/vibe/  │ bpm · len · vers · tags · dot    │ rail, spec grid,  │
│ instr/collab  │ sortable headers                 │ TAG PANEL, stems  │
│ + counts      │                                  │                   │
├───────────────┴──────────────────────────────────┴───────────────────┤
│ PLAYER BAR  ◀ ⏯ ▶  waveform────────────  0:42/3:04  vol  queue ▸     │  72–88px
└──────────────────────────────────────────────────────────────────────┘
```

- The list is the hero and gets the width. **Never** cap the content column below the viewport.
- Filters live in the left rail permanently (no hiding behind a player), each with a live count.
- Right panel shows the selected/playing song: generated cover, version rail (from B-vault),
  the spec grid (from A-console), the tag panel, stems.
- Player is a **slim bar** (72–88px), always visible, never 252px, never covering content.
- Below 1100px: right panel collapses into the row-expand; below 760px: rail becomes a filter
  sheet and the layout becomes the phone stack. Phone must remain usable — it is just not what
  we design around.

## 2. Match the bake-off quality (this is a fidelity task, not a reinterpretation)

Open the mockups side by side and match them: gradient elevation (`#242424→#101010`) instead of
flat fills, the white-alpha text ladder (1 / .48 / .28), IBM Plex Mono for **all** metadata,
Instrument Sans for titles, radius 8/12, era colour as a restrained 3px thread and cover field,
generous row rhythm, `tabular-nums` everywhere, one 180ms easing token.
Steal directly: **version rail** (B-vault), **mono spec grid** (A-console), **era cover fields**.
If a shipped screen looks flatter or more cramped than the mockup, it is wrong.

## 3. Kill the batch concept (deletion, not deprecation)

- Remove `kind='listen_through'` end to end: routes, templates, progress model, the
  `/listen` surface, the share-picker's track selection, and its tests.
- **One share kind.** Minting produces a link granting the **entire library**. No scope picker.
  Keep: label, revoke, expiry (default: none), and per-person links so you can see who heard what.
- Existing listen_through shares: migrate to full-library shares (data migration, not a shim).
- Scope enforcement code stays (it is the security boundary) — the scope is simply "everything".
- The guest surface becomes the **same library UI** as the owner's, minus owner-only actions
  (triage, share minting, batch ops, vocabulary editing, stems queuing).

## 4. Tagging becomes the point (first-class, everywhere)

- **Tag panel** in the right detail column: every dimension (status / vibe / instr / collab /
  key) editable inline, chips toggle instantly with commit-ack, "add new value" inline with
  typeahead over existing values.
- **Multi-select in the list** → tag many songs at once (checkbox column already exists;
  give it a real batch tag bar: add/remove chips across the selection).
- **Keyboard**: `1–9` toggles the first nine chips of the focused song, `t` focuses the tag
  input, `space` play/pause, `j/k` move, `/` search. Desktop tagging should feel like a tool.
- **Untagged view**: a filter for "no vibe yet" so there is an obvious pile to work through,
  with a count. No global percent-complete anywhere (guilt-dashboard rule stands).
- **Vocabulary management**: a Tags page listing every tag, its count, rename, merge, delete.
- Every tag chip anywhere is also a filter link (already true — keep it).

## 5. Collections (new, replaces batches)

- A **collection** is a named, ordered, hand-built set of songs. Create from a multi-select,
  from the queue, or from a filter's results. Drag to reorder (SortableJS, vendored).
- Collections appear in the left rail, are playable/shuffleable as a unit, and can be the
  landing view of a share link ("open on: whole library | collection X").
- Table: `collections(id, ulid, name, notes, created_at)` +
  `collection_items(collection_id, bounce_ulid, position)`.

## 6. Bug fixes (all from `reports/qa-2026-07-30.md`, all must be verified fixed)

1. **Transport reflects state**: ⏯ shows a pause glyph while playing, play glyph when paused;
   `aria-label` follows suit; the now-playing row is visibly marked in the list.
2. **Player is a slim bar** and never occupies more than ~10% of viewport height; add an
   expand affordance for the big view rather than the reverse.
3. **Queue drawer virtualized/collapsed** — never render 472 remove buttons; show the next
   ~20 with a count and a scroll.
4. **Login form legible**: visible field borders, real fill contrast, focus ring, sane vertical
   rhythm, no dead band above the form.
5. **No clipped chips** — the tag row wraps or scrolls with its own container; nothing overlaps.
6. Fix any remaining `119.1`-style floats and raw ISO date ranges on every surface.

## 7. Owner entry

Owner keeps password login with a durable session, at the stable tailnet HTTPS URL. Do not put
owner routes on the public origin; the two-process split stands.

## Acceptance

- Full pytest suite green and grown; a template test forbids bare-div rows and asserts the
  transport's dual state.
- Screenshots at **1440×900** and 1100×800 show zero dead columns and a layout matching §1.
- Side-by-side against `B-vault-balanced.html`: elevation, type ramp, and spacing match.
- `grep -r listen_through` returns nothing outside migration code and changelog.
- Tag a song from the detail panel, tag five at once from a multi-select, rename a tag, build a
  collection, share the library, open the link in a clean session and see everything.

---

# Amendments from adversarial review (Grok, 2026-07-30)

## A1. Auto-tagging layer — REQUIRED before manual tagging ships (new, high impact)

Finding: 472 songs × 6 manual dimensions is labor that reliably dies (Ableton's own users at
scale report taxonomy work displacing music-making). A 2026 competitor (Tuva) auto-tags
instrument/tempo categories on-device with zero manual effort.

We already own most of the signals. **Pre-populate tags from what the catalog knows, and make
the human pass a confirmation pass, not a data-entry pass:**
- `key`, `camelot`, `bpm` — already computed for 74% / 99% of songs → surface as tags/filters
  automatically (they are facts, not opinions; never ask a human for them).
- `era` (PELICANA / NOVA1 / working) — already derived.
- `collab` — already parsed from filenames and folder names for the known collaborator list.
- `instr` — derive candidates from filename tokens already parsed (gtar, vox, bass, keys,
  hangdrum, synth) plus mix-role tokens; propose, don't assert.
- `energy` — from the Mixed In Key import where present.
- Fingerprint neighbours (chromaprint, already computed) → "songs similar to this" and
  "apply this song's tags to its neighbours" as a bulk accelerator.
Only `vibe` and `status` should ever require fresh human judgment.

UI consequence: the tag panel shows **derived tags as already-set** (visually distinct from
human-set, per the existing provenance contract) so the archive never looks empty, and the
untagged pile is only about the genuinely subjective dimensions.

## A2. Share defaults — safe version of "one link shows everything"

The owner's decision stands (one link, whole library). Make it the *safe* version of itself:
- **Expiry ON by default** (365 days, renewable in one click) instead of never. Owner can set
  "no expiry" explicitly.
- **Stream-only by default**; downloads are a per-link toggle, off unless enabled.
- **Snapshot vs live — decide explicitly (the spec was silent, this is a real hole):**
  a whole-library link is **live** (it grows as new songs land) because that is what "see
  everything" means — but the mint screen must SAY so, and offer "freeze to today's 472" as a
  checkbox for links sent outside the inner circle.
- Keep per-person links + one-click revoke + "revert everything from this token".
- Show, on the shares page, exactly what each link currently exposes (count + live/frozen).

## A3. DIG weighting must be explicit and visible
Unweighted random over 472 rough bounces reads as broken. Weight by: never-played → least-played
→ least-recently-surfaced, and **show the reason** on the now-playing chip ("not played since
Mar", "never surfaced"). Add a quality floor toggle: "skip sketches under 90s" (the catalog
already marks sketches).

## A4. Timestamped comments — promote from v2 to now (guest-facing table stakes)
Every peer product treats time-anchored feedback as the point of sharing. wavesurfer Regions
make it cheap and the peaks are already generated. A guest should be able to drop a comment at
1:04 and the owner should see it anchored on the waveform.

## A5. Virtualization — use a proven primitive, not hand-rolled
The queue drawer already failed this exact test (472 buttons in the DOM). For the main list use
a maintained vanilla virtualization core (e.g. TanStack Virtual's framework-agnostic core, or
server-side windowing with htmx infinite-scroll) — and add a test asserting the DOM never holds
more than ~120 row elements regardless of result-set size.

## A6. Export path (anti-lock-in)
`cr8 export` → CSV/JSON of every song with all tags, and an M3U per collection. One command,
documented. Tags the owner spent months building must be portable out.
