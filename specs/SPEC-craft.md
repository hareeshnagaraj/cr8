# SPEC: craft rules (binding, checkable)

Distilled from the owner's own installed design skills — `better-ui`, `better-typography`,
`better-colors`, `animation-vocabulary`, `improve-animations`, `apple-design`, `emil-design-eng`
(at `~/.grok/skills/`). These are not suggestions. Every rule below is checkable in a diff.
Where a rule gives an exact value, use that exact value — do not "interpret" it.

## Surfaces

1. **Concentric border radius.** `outerRadius = innerRadius + padding`. Never the same radius on
   a parent and its nested child. This is the single most common reason an interface "feels off".
   Our tokens: cards 12px with 8px padding → inner controls 4–8px; panels 16px with 12px padding
   → inner 8px.
2. **Shadows over borders — DARK-MODE recipe.** The 3-layer stacked shadow is the *light-mode*
   recipe and must not be used here. On our dark ground: a **single ring**,
   `box-shadow: 0 0 0 1px oklch(1 0 0 / 0.08)`, hover `0.13`. (Our gradient elevation is the
   ratified alternative and is fine.) Hairlines only for genuine dividers, never to "make a box".
3. **Image / cover outlines.** Every generated era cover and artwork gets a subtle 1px outline.
   Dark mode value is exactly `oklch(1 0 0 / 0.1)` — pure white at 10%. Never a tinted neutral;
   a tinted outline picks up the surface beneath and reads as dirt on the edge.
4. **Optical over geometric alignment.** The play triangle is the classic offender: geometric
   centering makes it look left-heavy. Nudge it right **2px** (better-ui's exact value). Same
   for any asymmetric icon in a circular button.
5. **Hit areas.** Desktop minimum 40×40px, touch 44×44px. Extend with a pseudo-element when the
   visible control is smaller (transport buttons, chip removes, row affordances). Hit areas of
   adjacent controls must never overlap.

## Motion

6. **Interruptible by default.** CSS transitions for every interactive state change (hover,
   active, selected, chip toggle). Keyframes only for staged sequences that run once.
7. **Never `transition: all`.** Always name the properties: `transition-property: opacity, scale`.
8. **Icon state changes cross-fade, never toggle `display`.** The play↔pause swap must keep both
   glyphs in the DOM (one absolutely positioned) and cross-fade with exact values:
   `scale 0.25 → 1`, `opacity 0 → 1`, `blur 4px → 0`, easing `cubic-bezier(0.2, 0, 0, 1)`.
   No motion library is installed, so this is the CSS path — both enter and exit animate.
9. **Scale on press: 0.95–0.98 band** (cross-skill consensus; the shipped `0.97` is correct —
   do not rewrite it). Never below `0.95`: reads exaggerated. Apply to **every** pressable
   thing, not just `<button>` — the filter rail is `<a>` and currently has no press feedback
   at all, which is the cheapest real fix in the app: `button:active, a:active { … }`.
10. **Split and stagger enters** at ~100ms between semantic chunks — but only where content
    genuinely enters (the detail panel populating, a toast). Never stagger the 472-row list.
11. **Exits are softer than enters** — a small fixed `translateY`, never animating full height.
12. **No animation on first paint.** The library must not play an entrance every navigation.
13. **`will-change` only on `transform` / `opacity` / `filter`, and only if stutter is observed.**
14. **`prefers-reduced-motion: reduce` means GENTLER, not zero.** (Corrected — my original
    "kill everything" rule contradicted apple-design, better-ui, and improve-animations, and
    the wrong version already shipped as a blanket
    `*{transition:none!important;animation:none!important}` in `owner.css`.) Under reduced
    motion: **keep** opacity and colour transitions, keep instant state feedback; **drop**
    movement — translate, scale, parallax, and anything that travels across the screen.
    A user who asks for reduced motion still needs to see that their chip toggled.

### 6a. The frequency gate (the most load-bearing rule across all three skills)

**Anything a user triggers 100+ times a day must not animate.** Animation is a cost paid on
every repetition. In this app that means: **chip toggles while tagging** (the `1–9` keyboard
model makes these rapid-fire), row selection while moving with `j/k`, and play/pause. These get
*instant* state change — colour, fill, weight — and at most a sub-100ms opacity settle. Save
real motion for the occasional moments: the detail panel populating, the queue drawer opening,
a toast, DIG surfacing a surprise.

Corollary already correct in our build: the 472-row list must never stagger.

## Typography

15. `font-synthesis: none` on the root. A missing weight must fail visibly, never be faked.
16. **`font-variant-numeric: tabular-nums` set once on `html` and inherited** (already done
    correctly at `owner.css:11` — simpler and more reliable than tagging individual value types).
17. **Line-height by role:** headings ~1.1, body 1.5–1.6, unitless always.
18. **Letter-spacing by size:** slight negative on large display; **+0.055em** on small
    uppercase microlabels (the ratified value, already correct in the shipped CSS); none on body.
19. **`text-wrap: balance` on headings, `pretty` on descriptions.** Never on long-form.
20. **Truncate without losing content:** ellipsis + `title` attribute (or the detail panel) so
    the full song name is always reachable. Never let a title wrap mid-token in a dense row.
21. **Size floors:** body 16px, UI 14px, captions 13px, never below 12px. Inputs 16px on mobile
    viewports (iOS zooms otherwise).
22. **Two families only:** Instrument Sans (UI/display) + IBM Plex Mono (all metadata). No third.

## Color

23. **OKLCH everywhere.** L and C to 3 decimals, H to up to 3. Drop trailing zeros.
24. **Dark-ground contrast rule:** with background L < 0.25, foreground text L > 0.9. Fix contrast
    by adjusting **L only** — chroma has negligible effect on contrast.
25. **APCA is the primary check** (`|Lc| >= 75` body, `>= 60` non-body); WCAG 4.5:1 / 3:1 is the
    minimum fallback. On our very dark ground with three alpha-blended text tiers, WCAG ratio
    math and perceived legibility diverge — the `--t3` tier at 9-10px microlabels is the pairing
    to actually measure.
26. **Era colours stay quarantined** to covers, the 3px thread, and active-chip tint. The signal
    red belongs to UNHEARD and nothing else.

## Checklist (run before declaring the UI done)

- [ ] No nested elements share a border radius
- [ ] Play triangle optically centered; play↔pause cross-fades with the exact values in §8
- [ ] Every pressable control (button AND a) scales 0.95-0.98 on press
- [ ] No `transition: all` anywhere in the CSS
- [ ] `prefers-reduced-motion` keeps opacity/colour, drops movement (NOT a blanket kill)
- [ ] Covers carry `oklch(1 0 0 / 0.1)` outlines
- [ ] All changing numerals are tabular
- [ ] `font-synthesis: none` set
- [ ] Every interactive target ≥ 40×40 desktop / 44×44 touch, no overlaps
- [ ] Every text/background pair ≥ 4.5:1
- [ ] Two font families, no third
- [ ] Dense list rows never wrap mid-token; titles truncate with the full value reachable

---

## Confirmed live defects (verified in code, 2026-07-30) — fix these

Full plans with exact values: `reports/motion-and-craft-audit.md`. Execution order 002 → 001 → 004
(they fix things that are actively *wrong*, not merely absent).

1. **Transport icon never changes state** (`base.html:42-44`, `playback.js` `toggle()`).
   The play/pause glyph is static — §8's cross-fade was never built at all. This is the app's
   worst bug: you cannot tell whether audio is playing.
2. **Reduced-motion blanket kill** (`owner.css:395`) — replace with the §14 gentler rule.
3. **Chip-fill / heart-pop keyframes replay on every htmx swap**, because they are triggered by
   attribute selectors rather than by a genuine toggle. Every already-tagged chip re-pulses on
   every filter click and every page of rows. Collides directly with the `1–9` rapid-tagging
   model. Fix: drive the pulse from a transient class applied on real interaction only.
4. **Filter rail has no press feedback** — `button:active{scale(.97)}` never matches the `<a>`
   elements the rail is built from. One-line fix (§9).
5. **Detail panel and player bar update via `textContent`** with no transition — including DIG,
   the headline discovery gesture. Give the populate a short opacity settle (§10 allows it).
6. **Queue/mode drawer toggles the `hidden` attribute**, which cannot transition, so it snaps.

Already correct — do not "fix": no `transition: all` anywhere; the 472-row list has no stagger;
`playback.js:337-341` already gates SortableJS on `prefers-reduced-motion` (use it as the
exemplar pattern).
