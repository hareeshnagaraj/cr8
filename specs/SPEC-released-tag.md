# SPEC: released tag (follow-up unit — dispatch AFTER the band app lands)

## Why

Songs already released on Spotify (artist names in this catalog: "all vars", "Sambrum",
possibly "Hareesh Nagaraj") must not show up in band listening/triage flows — the band listens
to *unreleased* work. Released tracks stay in the catalog (full coverage) but are excluded by
default everywhere.

## Changes (touch ONLY these areas; no refactors)

1. **Schema migration (bump version):** extend `songs.status` CHECK to
   `('idea','jam','demo','mixed','finished','released')` (SQLite: recreate-table migration or
   drop/re-add CHECK per existing migration pattern). Add `songs.released_url TEXT` (nullable —
   Spotify/streaming link, informational).
2. **CLI:** `cr8 set <slug> status=released released_url=...` already works via `set` once the
   enum accepts it; ensure csvio vocab validation includes it.
3. **Mirror:** `TXXX:STATUS=released` flows automatically; no change beyond enum.
4. **Owner app:**
   - Library, search, triage queue, and share-picker EXCLUDE `status=released` by default.
   - A "released" filter chip (rendered last, dimmed) shows them deliberately.
   - Batch-ops screen: multi-select → "mark released" (sets status, optional URL field).
   - Song detail shows a small "RELEASED" badge (white-alpha, not era color; never the signal red).
5. **Guest app:** share scopes are snapshotted track lists — no change needed, but the share
   CREATION picker (owner side) hides released songs by default.
6. **Verify:** V4 counts released songs as fully covered regardless of other dimension gaps
   (they're archived from the workflow's perspective) — report their count separately.

## Candidate pre-flagging (data task, not code)

`reports/released-candidates.md` will be produced from streaming-discography research
(matching released track titles against catalog slugs). The owner confirms each via batch-op —
NEVER auto-mark from name matches alone.

## Acceptance

pytest green (enum migration round-trip, default-exclusion in library/triage/share-picker
queries, chip shows them, batch op works); `cr8 set` accepts released; verify reports the
released count; zero corpus writes.

## Appendix: visual punch list from live validation (fix in this unit, same surfaces)

1. BPM displays as float ("112.0 bpm", "119.1") everywhere — render as rounded integer ("112 bpm").
2. Song-detail date range renders as raw ISO "2026-07-27-2026-07-29" — render "Jul 27 – Jul 29"
   (year only when it differs or isn't current).
3. Library row metadata line wraps mid-token ("C# minor · 12A ·" breaking after "C#") — keep each
   token atomic (white-space:nowrap per token span, allow wrap only between tokens).
4. "unheard 472" filter chip: when EVERY song is unheard the counter reads as noise — show the
   count only when < total (otherwise just "unheard").

## Punch-list status (re-verified 2026-07-30)

Items 1–4 landed on the LIBRARY rows only. Still broken on the SONG DETAIL page:
- spec grid shows `bpm 119.1` — must render integer (`119`)
- header shows `2026-07-27-2026-07-29` — must render `Jul 27 – Jul 29`
Apply the same helpers used for the library rows to the detail template (owner and any
guest/band detail view). Add a template-level test so the raw-ISO and float-bpm forms cannot
reappear anywhere.
