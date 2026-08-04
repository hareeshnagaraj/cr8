# cr8 — composition audit + archetype cluster (Phase 1–2)

Date: 2026-07-29. References audited via live fetch + shipped-CSS inspection (workflow
`wf_6bf24428-c60`, full audits in the workflow output): nts.live, daily.bandcamp.com,
bandcamp artist pages, ableton.com, teenage.engineering, poolsuite.net, boilerroom.tv,
samply.app, untitled.stream.

## Cross-reference laws (what the best in this space converge on)

1. **One sans + optionally one mono. Never a second display family.** (NTS: one condensed
   grotesque, 2 weights. Ableton/TE: 2 weights, zero letter-spacing overrides. Boiler Room's
   6-family sprawl is its documented weakness.)
2. **Near-black chosen grounds, never #000.** (#0a0a0a BR, #0D0D0D Samply, #121212 NTS,
   #191919 Untitled.) Hierarchy from white-alpha ladders (Samply 1/.48/.28) or 1px rules
   (NTS), not borders/cards/shadows.
3. **Artwork is the only saturated color; UI accents are quarantined.** NTS reserves red for
   LIVE. cr8 reserves its signal color for UNHEARD.
4. **Metadata IS the design.** (Boiler Room chips; TE spec-sheet numerals; Samply version
   rail.) Key/date/version/tags rendered as precise tabular objects make demos feel like
   engineered artifacts.
5. **The player is the masthead, not a footer afterthought.** (NTS broadcast header;
   Poolsuite player-as-beloved-object; Samply waveform canvas + snapshot underlay.)
6. **Voice: terse, specific, signed, zero hype.** Shelf-tag one-liners (NTS), bylines and
   named rituals (Bandcamp Daily), bandmate-blunt intimacy (Untitled, one notch less precious).

## Banlist (what the references NEVER do — violations = slop)

Inter/Roboto body (Samply proves mechanics without typographic identity = anonymous) ·
purple gradients · glassmorphism · centered-hero-two-CTAs · three-cards-in-a-row ·
horizontal carousels for primary content · second display typeface · pure #000 ground ·
borders-as-structure · engagement metrics / percent-complete (guilt dashboard ruling) ·
exclamation points · algorithm-speak · marquee for load-bearing info on mobile ·
the bracket gimmick (Untitled owns it) · desktop-OS metaphor (Poolsuite owns it) ·
"sacred/nurture" preciousness past one notch · unquarantined accents competing with era
colors · placeholder-gray artwork blocks.

## Photography stance

No stock photography exists in this product. The "image system" is **generated era covers**
(flat color field + big title typography, deterministic per song) — the equivalent of a
consistent filmic grade. Covers supply the saturation; UI stays grayscale + quarantined signal.

## Era palette (chosen, OKLCH — the color spine across all archetypes)

- `PELICANA` (2023–24): `oklch(0.72 0.15 25)` — pelican rose/coral
- `NOVA1` (2024–25): `oklch(0.78 0.13 195)` — nova teal
- `working` (2026): `oklch(0.86 0.16 115)` — acid chartreuse
- Signal/UNHEARD (quarantined, never decorative): `oklch(0.60 0.22 27)` — broadcast red

## The three archetypes

### A — BROADCAST CONSOLE (NTS × Boiler Room)
- **Type:** Archivo (superfamily) — Archivo Narrow for UI/labels, Archivo 600 for display.
  Two weights. Uppercase microlabels at +0.055em tracking; sentence case titles. tabular-nums.
- **Ground:** #0a0a0a; ladder #121212/#1a1a1a/#2a2a2a; text #fff/#999/#666; 1px #2a2a2a rules,
  NO cards, NO radius (0–2px max).
- **Layout:** player-as-masthead pinned from first paint; dense vertical lists; chips as tiny
  bordered uppercase badges; era shown as colored tick on the row edge.
- **Signature move:** the UNHEARD red dot system — every bounce not yet heard by you carries
  the broadcast dot; the masthead marquees the now-playing title.
- **Voice:** shelf-tag register. "rough mix, drums too hot."

### B — STUDIO VAULT (Untitled × Samply mechanics × TE precision)
- **Type:** Instrument Sans (variable; 400/500/650) + IBM Plex Mono 400 for ALL metadata
  (dates, keys, durations, version labels). iOS-HIG ramp: body 17px, footnote 13px mono,
  negative tracking on display only. font-synthesis:none.
- **Ground:** #191919 soft black; elevation via subtle vertical gradients (#242424→#101010),
  never borders; white-alpha text ladder 1/.48/.28; radius 8/12px; safe-area tokens.
- **Layout:** roomy list; **version rail** (git-graph dot column) beside titles; waveform with
  snapshot underlay; tape-strip overlay for now-playing metadata; era color as a 3px thread on
  covers and active chips.
- **Signature move:** the version rail — a song's history rendered as a commit graph you can tap.
- **Voice:** intimate but bandmate-blunt. "4 of 6 heard. EJ hearted two."

### C — FLYER (Boiler Room energy × Poolsuite commitment × Bandcamp ritual)
- **Type:** Archivo Black display (uppercase, tight) + Space Grotesk 400/500 body +
  tabular-nums. Display sizes brave (clamp 40–72px).
- **Ground:** #0a0a0a; ONE acid accent `oklch(0.93 0.19 115)` (#ecff49-class) used at chip AND
  display scale — braver than A/B; relative timestamps ("3 weeks ago") everywhere.
- **Layout:** the listen-through renders as a GIG FLYER — batch name huge, tracklist as a
  setlist, guest verdicts as stamps. Library is a dense archive table.
- **Signature move:** every share is a flyer with a name ("FOR EJ — NEW BATCH · JUL 29").
  Named rituals as UI objects (Bandcamp's "Album of the Day" pattern → "FRESH BATCH").
- **Voice:** flyer-terse. Two-word verbs. Specificity does the hype's job.

## Unchosen defaults caught and overridden

- Samply's Inter-everywhere → all three archetypes name a chosen family.
- Samply's dual unrelated accents (sky + orchid) → one signal + era palette only.
- Link-blue anywhere → banned; era colors carry identity.
- Bootstrap-radius cards → A: none; B: 8/12 chosen; C: none.
