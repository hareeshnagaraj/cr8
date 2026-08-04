# Samply — visual audit from 10 screenshots (2026-07-30)

Reference for the port. Everything here is observed in the screenshots, not inferred.
This is the target feel. It is NOT a licence to copy Samply's product — we keep our own
model (songs, bounces, versions, tags, stems, dig) and adopt their *composition*.

## Why it reads clean and ours doesn't

| | Samply | Ours today |
|---|---|---|
| Type | one sans, sentence case, throughout | mono everywhere + lowercase-as-style |
| Data | a real table with column headers and dividers | pseudo-rows with ad-hoc metadata strings |
| Controls | a fixed right rail per row, revealed on hover | seven buttons stacked under the title |
| Fills | flat; white is the only accent | gradients on chips, cards, inputs |
| Chrome | quiet — dividers and spacing carry structure | boxes inside boxes |
| Density | generous, consistent | inflated in places, cramped in others |

The single biggest tell: **Samply has no monospace anywhere.** We used mono as a
personality shortcut and it reads as a terminal, not a music app. Mono stays only
where digits must align in a column.

## Layout

Three regions, resizable feel:
- **Sidebar ~360px** — logo top-left, collapse toggle top-right. Then: account row
  (avatar + name + search icon), `Notifications` with a count pill, `Library`,
  then a **white filled pill** `+ New project` as the one primary action, then
  `My projects ›` as a quiet disclosure. Footer card: `What's New` / `Add to Queue`.
- **Main** — the work surface.
- **Right inspector ~340px**, optional, toggled by a slider icon in the header.

## The track table (the core screen)

A genuine table. Columns: `#` · Name · Duration · Sample rate · LUFS-I · Last updated ·
Kind · [version badge] · [avatar] · `…`

- Header row: small, quiet, sentence case, thin vertical dividers between groups,
  a search icon at the far right of the header itself.
- Row ~55px. Hover lifts the background one step — nothing else moves.
- **The row number in the gutter becomes a ▶ play triangle on hover.** That is the
  whole play affordance. No separate play button competing with the title.
- The right rail is fixed-width and always in the same place: version badge (`v3`),
  contributor avatar, `…` overflow. Everything else lives in `…`.
- The active row is a **light/white background** — unmissable, no glow, no border.
- **Versions expand inline** as an indented subtree with a vertical connector line and
  a `⊕ Add Version` affordance at the top of the group. This is exactly our
  song → bounces → versions shape and we should adopt it directly.

## Project header

Back chevron · small square artwork · title over artist · then round icon buttons
(play, `+`, `…`) pushed right. A segmented `Listen | Manage` control centred at the top.
`Share 🔗` as a white pill top-right.

`Listen` vs `Manage` is worth stealing: one surface for consuming, one for editing,
instead of our single mode that tries to do both and collides.

## Share (this is what we need most)

Centred modal, ~470px:
- artwork + title + `6 Tracks`
- `Anyone with the link` as a select (the access model, stated plainly)
- `Just you, for now` / `Invite collaborators or share the project link` + invite icon
- toggle rows: `Password`, `Downloads`, `Comments`, `Versions` — each with a one-line
  description underneath in dim text
- footer: `Invite Collaborators` (quiet) and `Copy Link` (white, primary)
- toast `Copied link` bottom-right

The same controls also live in the right inspector as a persistent card — modal for the
act of sharing, inspector for the current state.

## Library (grid)

Big `Library` title. Filter tabs `All / Mine / Shared with Me`. Sort select
(`Last Accessed`) and a **grid/list toggle** on the right. Then large square artwork
cards, 5 per row at 1440px, title + artist beneath in two type sizes. Round white `+`
FAB and a search icon top-right.

Our era covers are already generated — this view is available to us cheaply and would
make the archive feel like a library instead of a spreadsheet.

## Settings

Sidebar of account sections. Main column: large sans section title, a dim description
line, then **grouped cards** where each row is `label + description` on the left and a
control (toggle / select / button) on the right, separated by hairlines. Section
headers sit outside the cards. Empty states are stated plainly and centred:
`No audio playing / Play a track to see its available options.`

## Tokens observed

- Ground `#0d0d0f`–`#111113`; card `#161618`–`#1a1a1c`; hairline `rgba(255,255,255,.08)`
- Text: white / `rgba(255,255,255,.62)` / `rgba(255,255,255,.38)`
- Accent: **white**. Blue (`#4a7dff`) only for the one inline `Add Version` link.
- Radius: ~10px cards, ~8px controls, full-round pills and avatars
- Toggles are iOS-style, ~44×26
- One sans family. Titles ~34px/600. Body 14–15px. Table text 14px.

## What we take, and what we do not

Take: the table with a gutter play affordance, inline version subtree, the fixed right
rail, `Listen | Manage`, the share modal, the grid library, settings-as-grouped-rows,
sentence-case sans, white-only accent, flat fills.

Do not take: their information model, their empty aesthetic where we have more to say
(key, BPM, camelot, stems, tag provenance are *our* value), or their lack of a
persistent global player — ours must survive navigation, which is the whole reason for
the port.
