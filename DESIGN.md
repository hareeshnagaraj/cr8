# cr8 design law

The binding conventions for every surface. Dispatch specs cite this file;
review passes check against it. When a rule here and a new idea disagree, the
rule wins until the owner changes it.

## Color

- Color comes from the data — Camelot key hues, the era palette — never from
  decoration. A colored element answers "what key / what era", or it is not
  colored.
- Tokens only: `--ground --raise --card --t1 --t2 --t3 --accent --line
  --line-strong --r-s --r-m`. Invented variable names drop silently; check
  against `:root`, not against your own new rules.

## Verbs and icons

- **Universal verbs are icons**: play, pause, prev, next, download, share,
  send, search, shuffle, filter, edit-tags, close, more. Icon-only buttons
  with `aria-label` always, `title` for pointers. The icon set is
  `web/components/Icons.tsx` — extend it in its own style (inline SVG,
  currentColor, shared stroke), never import an icon font or library.
- **Domain verbs stay words**: Note, Rip stems, Make a link, Mark demo —
  anything a first-time friend couldn't guess from a pictogram.
- Menu items stay words even when their trigger is an icon.
- One verb, one place per screen. Duplicated downloads/shares on a single
  screen was a real shipped mistake; the canonical action row owns the verbs.

## Controls

- A verb's hit area is ≥44px on a phone; verbs wear `.triage-verb`-family
  classes, tag chips wear `.chip` and stay ≤40px. The audit's two rules argue
  on purpose — a chip and a verb are different things.
- No native control chrome: number inputs are plain text fields
  (`inputMode="numeric"`, spinners suppressed), selects are the `Menu`
  component.
- Display ≠ edit: current values render as facts (the key as a colored
  `C minor · 5A`); editing lives behind a quiet affordance. Derived values
  (`source != human`) are facts only — never buttons.
- Adjacent controls keep ≥10px of air; a crowded header row is a bug.

## Layering

- The z scale is law: content < dock (55) < sheets (59-70) < dialogs (75) <
  menus and dropdowns (80). **Nothing the user must read or tap may render
  under the dock.** A dialog on a z-40 scrim shipped with its middle covered
  by the phone player and its buttons stranded beneath it.
- Every overlay is checked at 390×844 WITH a track playing — the dock only
  collides when it exists.

## States and motion

- Empty states render nothing before they render filler. A section with no
  content is absent, not apologising.
- `prefers-reduced-motion` means gentler, not none.
- Animation is compositor-only (transform/opacity), concentrated where the
  eye already is (the playing row, the hovered tile, the open page), never
  per-frame across a table.
- Every fetch checks its response. `.catch(() => undefined)` on anything the
  user asked for is how this codebase historically failed.

## Interaction integrity

- Shared mutable state (the queue, selections) mutates atomically — reducers,
  not sibling useStates updated from callback closures. Same-tick rapid input
  losing operations was a real shipped bug ("it got stuck").
- Before shipping anything interactive, run the storm:
  `scripts/click-storm.sh` against a local production build. It spams plays,
  transport, and queue mutations, then asserts the dock, the playing row and
  the transport agree and the next click still lands.

## The battery is one command now

```
scripts/gate.sh        # tests, types, build, payload budget, probe ×3,
                       # click storm, play-latency probe — refuses on breach
scripts/deploy.sh      # the ONLY sanctioned ship path: gate → push →
                       # server install+build (unmasked exits) → live smoke
```

Budgets live in `perf-budgets.json` — including press-play-to-audible under
a 5 Mbit pacing proxy, and the decoded JS the library page actually loads.
Raising a budget is a reviewed decision, never a way to turn a red gate
green. Heavy features ship behind dynamic imports; garnish (prefetch,
strips) never competes with the track you just started for the pipe.
Run `scripts/ui-audit.sh <touched pages>` at 390px alongside for anything
visual.
