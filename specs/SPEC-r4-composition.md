# SPEC r4 — composition, clarity, and getting out of a flow

Owner: *"I thought you applied a bunch of anti-slop and styling skills. I don't see any benefits
from that here."* · *"Make it super clear and let users reset earlier."*

## The honest diagnosis (measured, not asserted)

10 of 12 mechanical craft rules ARE implemented in `owner.css` — press feedback on `<a>`,
`scale(.97)`, no `transition:all`, no blanket reduced-motion kill, `oklch(1 0 0 / .1)` cover
outlines, the dark single-ring shadow, `font-synthesis:none`, root `tabular-nums`, `.055em`
uppercase tracking, the `blur(4px)` icon cross-fade. (Missing: `text-wrap: balance` anywhere,
and 24 hex colours against 5 OKLCH.)

**So the rules are not the problem. Craft rules are hygiene — invisible when correct.** They stop
an interface feeling broken; they do not make it feel designed. What is missing is composition
and identity, and that is what was lost between the ratified mockups and the build:

| `B-vault-balanced.html` | shipped |
| --- | --- |
| song title ~28px with air around it | everything 11–13px, no focal point |
| era cover as a real object with weight | 28px thumbnail in a table cell |
| version rail as a hero element | one small box among five small boxes |
| deliberate rhythm, a place for the eye to land | uniform dense admin table |

The shipped app reads as a **database front-end**. The mockup read as a **music app**. Fix that.

## 1. Give every screen ONE focal point

- **Song detail:** the title becomes the hero — Instrument Sans ~30/34px, `text-wrap: balance`,
  negative tracking, real space above and below. The era cover grows to a genuine object
  (~140px) with its `oklch(1 0 0 / .1)` outline. The spec grid recedes to supporting mono
  metadata. The version rail gets its own breathing room, as in `B-vault-pole.html`.
- **Library:** the count and the three gestures are the head; the table is the body. Increase row
  rhythm from cramped to 44px with real vertical padding, and let the title column dominate —
  title at 15px, everything else at 11px mono in `--t2`/`--t3`. Right-align every numeral column.
- **Guest page:** keep the poster treatment from `C-flyer-pole.html`. It should not look like the
  owner's admin table.

## 2. Make the current state impossible to misread

Everywhere, at all times, the app must answer: *what am I looking at, and how do I get back?*

- **A state line under the title** that says what is filtered, in words:
  `471 songs · all` / `34 songs · vibe: dreamy · unheard` / `DIGGING · 8 played`.
- **`Clear all` / `Reset` is always visible when any filter, search, or mode is active** — not
  hidden in a corner, not only on hover. `Escape` clears the deepest active thing (search →
  filters → mode) and the UI says what Escape will do.
- **Modes are chrome, not subtitles.** When digging or shuffling, the header carries a visible
  mode chip with an X. The current 9px grey word "digging" in the player subtitle is not enough.
- Selected filters read as sentences in the rail, not just highlighted rows.

## 3. Kill the remaining tells

- The era-coloured inset accent rail on selected filters (`owner.css:134`) — banlisted pattern,
  and it misuses the NOVA1 era hue to mean "selected", breaking the palette's grammar. Replace
  with a chosen treatment (weight + colour shift, or an inverted count pill) applied
  consistently to every active state in the app.
- Convert the 24 hex colours to OKLCH tokens so the palette is one system, not two.
- `text-wrap: balance` on every heading, `pretty` on descriptions.
- Replace filler microcopy ("Metadata first. Tags stay optional.") with something true and
  specific, or delete it.
- No colour without meaning: era hues mean eras, signal red means UNHEARD, nothing else gets hue.

## 4. Downloads — keep all three options

Owner: *"Just MP3 is fine. Actually we want those options."* Ship the full set from SPEC-r3:
**original** (wav/aif from the corpus), **mp3-320**, and **each stem** — labelled with format and
size so the choice is obvious. mp3 is the default action; the others sit behind the same control.

## 5. Not now

The rename is parked — the owner is choosing a name. Do not touch naming.

## Acceptance

- Screenshot the song detail at 1440×900 next to `B-vault-balanced.html`: the title is the
  clear focal point, the cover reads as an object, the rail has room.
- With a filter and DIG both active, the header states both in words and offers a visible way
  out; Escape clears one layer at a time and says what it will clear.
- `grep -c 'era-nova1' owner.css` shows the era hue used only for eras.
- Downloads offer original / mp3 / stems with format and size, mp3 as the default.
