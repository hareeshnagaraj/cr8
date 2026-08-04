# SPEC: the cr8 is for DIGGING (shuffle-first library, owner + band)

## The reframe (supersedes the earlier listen-through-only ruling)

An archive of 472 unreleased demos is a **cr8 you dig through for ideas**, not a playlist you
walk. Observed live: a bandmate holding a 472-track band token could only advance sequentially —
the setlist rows are non-interactive `<div>`s. That is the wrong metaphor at any size.

**The core interaction is: shuffle it, jump anywhere, surprise me.** Tagging is what makes the
digging progressively sharper ("shuffle the dreamy unheard ones"), so every filter the catalog
can express must be shuffleable. Curated listen-throughs remain as ONE mode — the "here are 6
songs, in this order" mode — not the only way to hear anything.

Applies to BOTH apps. The owner needs it most (it is how you rediscover your own archive);
bandmates need it to explore; listen-through guests keep their curated flow.

## Non-negotiables carried forward
Guest origin imports zero owner routes. Every track access is scope-checked per request against
the share's snapshotted list — **shuffle and dig NEVER escape scope**. Reactions stay
append-only and attributed. Ratified design tokens
(`canon/ratifications/2026-07-29-crate-design.md`) govern all visuals; reuse existing template
and CSS language rather than inventing new components.

## 1. Playback engine (shared, both apps) — build this first

A real queue, not a single-track player:
- `playQueue`: ordered bounce ULIDs + cursor. Auto-advance on `ended`. Prev/next.
- **Shuffle**: Fisher-Yates over the *current result set* (whatever is on screen after
  search/filters), so "shuffle" always means "shuffle what I'm looking at". Toggle persists per
  session; explicit reshuffle.
- **Repeat**: off / all. (No single-track repeat — wrong for an idea crate.)
- Queue is client-side state on the `hx-preserve`d player node so it survives htmx navigation.
- Media Session metadata per track (lock-screen title/artwork). Keep the plain `<audio>` +
  pre-generated peaks rules from SPEC-band-app.md; no Web Audio in the playback path on iOS.

## 2. The three entry gestures (prominent, thumb-reachable)

1. **SHUFFLE EVERYTHING** — primary action on the library screen. Shuffles the full in-scope set
   and starts playing on one tap.
2. **SHUFFLE THIS** — same, applied to the active filter/search result; the label reflects the
   set: "shuffle 34 dreamy", "shuffle unheard", "shuffle NOVA1".
3. **DIG** (surprise me) — plays ONE random track weighted toward the un-dug: never-played
   first, then least-played, then least-recently-played. The serendipity engine; it should feel
   like pulling a record out at random. It keeps digging (queue of random picks) until the user
   taps something else.

## 3. Browsable library (both apps)

- Every song row is tappable and plays immediately; the visible list becomes the queue from that
  point. Rows are real `<button>`/`<a>` elements — **never bare divs**.
- Rows show: title, era tick, key/camelot, BPM (integer), duration, version count, unheard dot.
- Search (FTS5; scope-limited on guest) + composable filter chips: era, key, status,
  unheard-by-me, hearted-by-me, and — as they accumulate — vibe/instr/collab tags.
- Sort: newest / oldest / longest / shortest / random (random sort re-orders the *list*, which
  is distinct from shuffle playback).
- Windowed rendering so 472+ rows stay fast (server-rendered pages of ~60 with htmx
  infinite-scroll; first paint under 150 KB).
- Song detail: version chain with each version tappable to play, waveform, metadata, tag
  controls.
- **Queue view**: see what's next, drag to reorder (SortableJS, already vendored), remove items.

## 4. Tag-driven digging (the payoff loop)

- Every chip on a song is also a filter: tapping a chip in the player jumps to "everything tagged
  that", already shuffled. This is what makes tagging pay for itself — tag five things "dreamy"
  and `shuffle dreamy` is immediately worth having.
- Filter chip rows are generated from tags that actually exist (never a hardcoded vocabulary),
  sorted by frequency, so the UI gets richer as the archive gets tagged.
- Empty states teach the loop: "no vibes tagged yet — tag a few while you listen and they show up
  here as filters."

## 5. Routes

Guest app: `kind='band'` → `/library` (this surface). `kind='listen_through'` → `/listen`
(existing curated flow) **plus** tappable setlist rows and shuffle-this-batch. Band tokens
hitting `/listen` redirect to `/library`; listen-through tokens hitting `/library` get the
generic unauthorized shell.

Owner app: the existing library gains the same engine — shuffle / dig / queue / tappable rows /
tag-chip jumping. Triage keeps its focused flow, but its "play bounce" enters the same queue.

## 6. Acceptance

- pytest green, including: shuffle output is a permutation of exactly the in-scope set (never
  more, never fewer); dig never returns an out-of-scope track; band token reaches `/library` and
  a listen_through token cannot; a template test asserts row partials contain no bare-div rows;
  tag-chip filter returns only songs carrying that tag; queue reorder persists within a session;
  auto-advance moves the cursor.
- Manual, using the live band token (label `henry — full library`, 472 tracks) through the public
  funnel: SHUFFLE EVERYTHING starts audio within a tap; a row tap plays that song; DIG returns
  something unheard; auto-advance moves on without interaction; a chip tap filters and
  reshuffles; all of it works at phone width.
- Owner app: the same gestures work against the full 472-song library.
