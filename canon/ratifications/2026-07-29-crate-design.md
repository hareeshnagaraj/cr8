# Ratification — cr8 design system

**Date:** 2026-07-29
**Ratified by:** Hareesh (owner), after reviewing the 6-mockup bake-off board
(`canon/references/mockups/board.html`), choosing the recommended synthesis.

## The ruling

**B — Studio Vault is the base system** for the owner app, with two grafts:
- **A's signal discipline:** broadcast-red UNHEARD system (dot/square + counter chip + filter)
  and the spec-grid metadata block on song detail.
- **C's guest poster:** guest listen-through pages get the Flyer treatment — batch name huge in
  the batch's era color, setlist typography, ticket-punch verdict stamps. The gift moment lives
  ONLY on the guest origin; daily-use calm and share-moment drama never fight.

Registry variants kept (not dead ends): A-pole's archive table (candidate future "dense mode"
for the library), C-balanced's FRESH BATCH ritual strip (candidate for the owner home).

## Ratified tokens

**Type:** Instrument Sans (variable, 400/500/650) for UI/display; IBM Plex Mono 400 for ALL
metadata (dates, keys, camelot, durations, version labels, counts). Self-hosted woff2 in
production. Body 17px (iOS-HIG ramp), footnote 13px mono. Negative tracking on display only.
`font-synthesis: none`, `tabular-nums` on every numeral. Display case: sentence case (owner),
uppercase reserved for microlabels at +0.055em and the guest poster headline (Archivo Black
joins ONLY on the guest poster surface).

**Ground:** `#191919` soft black (never #000); elevation via subtle vertical gradients
`#242424 → #101010`, never borders. Text ladder: white-alpha 1 / .48 / .28. Radius 8/12px
(owner); guest poster surface may go 0.

**Era palette (validated across all six mockups):**
- PELICANA `oklch(0.72 0.15 25)` · NOVA1 `oklch(0.78 0.13 195)` · working `oklch(0.86 0.16 115)`
- Signal/UNHEARD `oklch(0.60 0.22 27)` — quarantined: never decorative, only unheard state.
- Era color appears as: cover fields, one 3px thread per screen, active-chip tint. Guest poster
  may use the batch's era color at display scale (C graft).

**Signature moves:** the version rail (tappable commit graph, mono labels, filled current dot);
the UNHEARD red system; guest shares as named posters ("for EJ — new batch").

**Motion:** one easing token `cubic-bezier(.32,0,.16,1)`, 150–200ms micro-transitions only,
`prefers-reduced-motion` kill-switch. Heart pop + chip fill are the only choreographed moments.

**Covers:** generated era-color fields + typographic titles (Pillow, deterministic). Craft rule
from the anti-slop pass: cover titles never mid-word wrap — single line clipped, or fitted size.

**Voice:** intimate but bandmate-blunt ("4 of 6 heard. EJ hearted two."), mono shelf-tag
metadata, zero exclamation points, no preciousness past one notch.

## Craft standards proven in the bake-off

tabular-nums everywhere · null values render as clean "—" (never dash-quote artifacts) ·
1px rules or gradient elevation instead of card borders · chips are real buttons with
aria-pressed and 44px hit areas · waveforms always deterministic pre-rendered bars, never
placeholder images · no global percent-complete anywhere.

## Playback rulings folded in from Grok/X validation (2026-07-29)

- Mobile-first for **Safari tab** use; PWA manifest ships but add-to-home-screen is never
  promoted (iOS 26 home-screen PWA audio regression: AudioContext hard-fails, track-advance
  stalls under lock — MacRumors thread through 26.2).
- Track-advance under lock can stall even in tabs → the guest listen-through is designed as an
  interactive foreground flow (tag between tracks), and the limitation is documented, not fought.
- Peaks always pre-generated server-side (audiowaveform) — wavesurfer never client-decodes;
  plain `<audio>` + Media Session is the playback spine (already in SPEC-band-app.md).
- Timestamped waveform comments (Notetracks/Samply pattern) confirmed as the v2 feature via
  wavesurfer Regions.

## Banlist (inherited from composition audit, binding for production)

Inter/Roboto body · purple gradients · glassmorphism · centered-hero-two-CTAs ·
three-cards-in-a-row · carousels for primary content · pure #000 grounds ·
borders-as-structure · engagement metrics/percent-complete · exclamation points ·
bracket gimmick · desktop-OS metaphor · unquarantined accents.
