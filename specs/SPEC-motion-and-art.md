# Cover art and motion

Written 2026-07-31, after the phone layout landed (`ac260d9`). This is the plan
for making cr8 feel alive without making it slower, and the rule it is written
against is the owner's: *"it has to feel as snappy as it is right now. There can
be no regression in terms of the speed of the thing."*

That is treated here as a budget with numbers, not a preference.

---

## 1. The problem with the covers

Every song has a generated cover. `generate_cover_bytes` in `cr8/mirror.py`
builds a two-tone linear gradient — colours derived from the era and a sha256 of
the title — and then draws the title text on top of it in PIL's default font.

At the size the library actually shows them, 48×48, that text is three or four
illegible smudges. The thing meant to identify a track identifies nothing.

### The part that turned out to matter more

Those files are **1400×1400 pixels**.

A browser does not decode an image at the size you display it. It decodes the
whole thing into memory as raw pixels — four bytes each for red, green, blue and
alpha — and only then scales it down to fit. So:

```
1400 × 1400 × 4 bytes  =  7.5 MB of memory, per cover
```

The file on disk is only 49 KB, which is why this has been invisible. But about
22 rows are on screen at once, so the library is holding:

```
22 × 7.5 MB  ≈  164 MB of bitmap
```

…churned continuously as you fling through the list, in order to paint squares
the size of a fingernail. This is almost certainly the weight you feel when
scrolling on a phone, and no amount of animation polish will fix a list that is
recycling 164 MB of pixels behind it.

So the cover art is not a decoration question. It is the single largest
performance item in the app, and fixing it *buys* headroom for everything else.

---

## 2. Camelot tiles

Replace the image with a cover drawn by CSS from data the browser already has.
No network request, no decode, no `<img>` at all.

### It reuses a mapping that already exists

`web/lib/colors.ts:19` already turns a Camelot key into a hue:

```ts
const hue = (195 - (position - 1) * 27 + 360) % 360;
```

That is the circle of fifths laid onto the colour wheel — the reason two songs
in harmonically adjacent keys already get adjacent colours in the Key column.
DJs have used this mapping for decades; it is why Camelot notation exists.

The tile calls that same function. One source of truth: if the key colour ever
changes, the covers change with it, and they cannot drift apart.

### What varies, and what stays constant

Four properties of the music drive four properties of the tile:

| Musical property | Visual property |
|---|---|
| Camelot key | hue of both gradient stops |
| Era | offset of the second stop (four values) |
| BPM | gradient angle, quantised to 15° |
| Duration | where the gradient splits, six steps |
| ULID + version count | position and size of one small geometric mark |

Quantising matters. Continuous values produce 470 tiles that are all slightly
different and therefore all the same — mush. Snapping to a small number of steps
produces *families*: you learn that steep-angled amber tiles are your fast tracks
in that key, because the set has a vocabulary rather than a spectrum.

Then every single tile shares two constants: a 1px inset hairline
(`inset 0 0 0 1px rgba(255,255,255,.08)`) and an identical bottom-right vignette.
This is the difference between a set that looks art-directed and a set that looks
generated. The shared frame is what makes them read as a series.

And no text, ever. Under about 11px of cap height, type is texture, not
information.

### What it costs

470 tiles × roughly 120 bytes of style string ≈ **56 KB**, computed once when
the library loads, in under 2 ms, memoised by ULID so a row scrolling back into
view recomputes nothing.

Against 164 MB of churning bitmap. This is not a close call.

### What it will look like

A wall of small, dense, jewel-toned chips sitting in one lightness band, each
with one quiet geometric mark, tinted by key — so as you scroll, tracks that
would mix together are visibly the same colour. Colour as a data channel, the
way Serato uses it, rather than as decoration.

---

## 3. Motion, in priority order

### 1. Sheets, dialogs and the row overflow menu

Use the platform: `popover` + `@starting-style` + `transition-behavior:
allow-discrete`. This is the modern CSS way to animate something into existence
that was previously `display: none`, which used to require JavaScript.

Two reasons it is first. It costs **0 KB and 0 ms** — no library, no mount state
machine, no `AnimatePresence`. And `popover` renders in the browser's *top
layer*, above everything, which means the virtualised list's `overflow` cannot
clip it and opening a sheet cannot force the scroll container to recalculate.

### 2. The now-playing row

Full-opacity text while other rows sit at about 70%, and a three-bar CSS
equaliser in place of the version badge. It animates `transform: scaleY`, and
its `animation-play-state` is bound to the *actual* player state — so it stops
when the audio stops.

Spotify's equivalent is a looping GIF that keeps dancing when playback pauses.
Ours will not lie about what the audio is doing.

Jank case: exactly one row in the entire list animates, ever, and it animates a
compositor-only property.

### 3. Hearting

120 ms, `scale(1.15)` and back, `cubic-bezier(0.2, 0, 0, 1)` — no overshoot, no
bounce. On the icon only, never the row. One element, one pointer event.

### 4. Filter feedback

A count near the search field animating "470 → 38" as you type, on a node the
virtualiser does not own.

**The list itself gets no transition whatsoever.** This is a deliberate refusal.
Animating a filtered list is where apps lose their responsiveness, and
keystroke-to-repaint is the property this app is best at. The count carries the
feedback; the rows just change.

### 5. The player pill and a scroll-driven hairline

The player slides up on first play (`translateY` + `opacity`, using a `linear()`
easing curve sampled from a spring — real spring physics, zero JavaScript). The
header grows a hairline as the list scrolls under it, via
`animation-timeline: scroll()`.

Both are compositor-only. Firefox does not have scroll timelines in stable yet,
where it degrades to a permanently-visible hairline — which is fine.

### Why "compositor-only" is the recurring phrase

The browser draws a frame in stages: compute styles → lay out geometry → paint
pixels → composite layers onto the screen. Animating `width`, `height`, `top` or
`margin` forces the whole pipeline every frame. Animating **`transform` and
`opacity`** skips straight to the last stage, on the GPU, off the main thread —
so it cannot block a keystroke. Everything above animates transform or opacity.

---

## 4. What we are deliberately not doing

This section matters as much as the previous one.

- **Staggered fade-in-up rows** (`delay: index * 0.05`). The signature of a
  templated site, and *fatal* in a virtualised list: rows are recycled DOM nodes,
  so a row scrolling back into view would fade in again, forever.
- **Shared-element transitions across the list** — Motion's `layoutId`, and
  React's `<ViewTransition>`. Both measure the DOM up front and mis-measure
  recycled nodes; React additionally skips the transition when either element is
  off-screen, so in a virtualiser it silently does nothing while still costing a
  document freeze. Possibly worth it later scoped to a single clicked cover.
- **Animated row hover.** Rekordbox, Serato, Ableton and Bandcamp all swap hover
  state instantly. Honest sub-100ms feedback beats a prettier 200 ms ease.
- Hover-scale on cards, spring bounce on everything, parallax, purple gradient
  orbs, glassmorphism beyond the single player pill, and audio-reactive glow
  driven by an FFT of the playing track. That last one is genuinely tempting and
  genuinely a gimmick.
- Never animate an inherited CSS custom property — it repaints every descendant.

---

## 5. The budget

| Metric | p75 | fails above |
|---|---|---|
| keystroke → repaint | 32 ms | 50 ms |
| blocking time per keystroke | 0 ms | 0 ms |
| row style + layout, 22 rows | 6 ms | — |
| dropped frames over a 2 s fling | 2% | 5% |
| cumulative layout shift | 0.02 | 0.05 |

Measured by a new `scripts/perf_probe.js`, driven the same way
`scripts/ui-audit.sh` already drives a headless browser.

It registers three `PerformanceObserver`s: **Long Animation Frames** (the honest
number — it reports the real time from the input event to the frame appearing),
**Event Timing**, and **layout-shift**. It types five real keystrokes — synthetic
events do not count, because the browser marks them `isTrusted: false` and gives
them no interaction id — then samples frames across a fling. Twelve iterations,
discard the slowest three, report p75 and max.

Two traps it avoids: gate on Long Animation Frames rather than Event Timing,
because Chrome deliberately rounds the latter to 8 ms for privacy; and measure a
production build only, because development mode double-renders every component
and the numbers are fiction.

**This lands before the animations**, so the budget is enforced from the first
commit rather than asserted afterwards.

---

## 6. The risk, and how it gets caught

**The real risk is not performance. It is that 470 generated tiles read as "no
cover set" — a wall of identicons — and a track you know by sight becomes
unrecognisable.**

Caught before it ships by a throwaway contact-sheet page rendering all 470 tiles
at 48px in one grid, screenshotted next to the same grid of current JPEGs, and
looked at. Plus a collision audit across the whole parameter tuple (hue × angle ×
split × mark position × size). If more than about 2% of tracks collide, or the
sheet reads as noise rather than as a family, quantisation gets tuned before the
merge rather than after.

---

## 7. Outcome: the covers were measured and reverted

The tiles were built, reviewed on a contact sheet at 0.4% collision, cut over to
every surface, and then **reverted**, because the probe measured them slower than
the 164 MB of JPEG they replaced:

| | baseline (JPEG) | tiles |
|---|---|---|
| keystroke -> repaint p75 | 13.4 ms | 13.0 ms |
| blocking per keystroke | 0.0 ms | 4.7 ms |
| dropped frames p75 | 1.5% | 2.2% |

Keystroke latency did improve. Scroll got worse, and scroll is what a crate is
for. The likely cause is that a decoded image uploads to the GPU once as a
texture and composites almost free thereafter, while layered CSS gradients
re-rasterise as the row moves - so removing the download cost added a per-frame
paint cost, and the second is the one you feel.

Two later attempts made it worse rather than better: removing the blurred inset
shadow, then replacing the child element with a radial-gradient layer, measured
8.7 ms and 90.1 ms of blocking. At that point a control run of the *unchanged*
build measured 8.7 ms and then 90.1 ms as well, which showed the blocking metric
was reporting ambient load on a machine also running Ableton and a VM, not the
code.

What is genuinely true and worth keeping:

- The measurement caught a real regression before it shipped. That is the whole
  point of building it first.
- 1400x1400 covers for a 48px thumbnail are still indefensible. The unexplored
  option is the boring one: regenerate the JPEGs at 256px and drop the text,
  which would cut 164 MB to about 5 MB with no rendering change at all.
- `web/lib/cover.ts`, `web/components/Cover.tsx` and `/covers` remain in the
  tree, imported by nothing, so the idea can be picked up again cheaply.

The motion work below has not been started.

## 8. Build order

1. `scripts/perf_probe.js` and a recorded baseline of the app as it is today.
2. Camelot tiles behind the contact sheet, reviewed by eye against the JPEGs.
3. Cut over the covers. Re-run the probe: this should be a large *improvement*.
4. Motion items 1 → 5, re-running the probe after each.

Every step is independently revertable, and none of them touch the catalogue,
the pipeline, or the audio element.
