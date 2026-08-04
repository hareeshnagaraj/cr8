# SPEC r5 — ADOPT DESIGN B WHOLESALE. Delete the A and C grafts.

Owner: *"combining designs A and C is why it looks like shit — fully adopt design B."*
*"Make it super fucking smooth."* Speed matters. This is a fidelity port, not a redesign.

## The ruling

**Studio Vault (B) is the ENTIRE system now.** The earlier ratification grafted A's console
signal language and C's flyer poster onto B. That hybrid is the problem — three visual grammars
fighting. Delete the grafts.

**Reference implementation — read these files and port them:**
- `canon/references/mockups/B-vault-balanced.html` — chrome, list, player, tag bar
- `canon/references/mockups/B-vault-pole.html` — song detail (version rail as hero), and the
  calm ceremonial guest page

## Tokens — take these verbatim from the mockup, replace what is in `owner.css`

```css
--page:#050505; --ground:#191919; --elev-hi:#242424; --elev-lo:#101010;
--t1:rgba(255,255,255,1); --t2:rgba(255,255,255,.48); --t3:rgba(255,255,255,.28);
--era-pelicana:oklch(0.72 0.15 25);
--era-nova1:oklch(0.78 0.13 195);
--era-working:oklch(0.86 0.16 115);
--signal:oklch(0.60 0.22 27);
--r-s:8px; --r-m:12px;
--ease:cubic-bezier(.32,0,.16,1); --dur:180ms;
```
Elevation is the **vertical gradient** `--elev-hi → --elev-lo`, never a flat fill and never a
border. Type: **Instrument Sans** for everything human-readable, **IBM Plex Mono** for every
piece of metadata. Two families, no third.

## Delete, explicitly

1. **All uppercase-console density from A.** No `TITLE / KEY / VER / DUR / DATE` uppercase table
   headers, no uppercase microlabel on every value, no teletext feel. B labels are lowercase
   mono in `--t2`. Keep uppercase ONLY for the few true microlabels the B mockup itself uses.
2. **All flyer/acid treatment from C**, including on the guest page. The guest page becomes
   B-vault-pole's calm version: generous air, `for <name>` at ~40px, a sparse mono tracklist,
   "4 of 6 heard" as the only progress language. No poster headline, no ticket-punch stamps,
   no acid fills.
3. **The era-coloured inset accent rail** on selected filters (`owner.css:134`). In B, selection
   is an **inverse chip**: background `rgba(255,255,255,.92)`, text `#101010`. Use that for every
   active/selected state in the app, consistently.
4. Every remaining hex colour that is not one of the tokens above → convert to a token.

## Port these B characteristics precisely

- **Song rows:** cover swatch 44px with `oklch(1 0 0 / .1)` outline and the era colour as its
  field; title Instrument Sans 15px `--t1`; metadata one mono line at 11px `--t2` reading
  `C minor · 5A · 3:04`; version count as a small dot cluster; comfortable row rhythm with real
  vertical padding — B breathes, it is not a dense admin table.
- **Song detail:** title ~30px with `text-wrap:balance` and negative tracking; cover ~140px as a
  real object; the **version rail is the hero** — dot column joined by a 1px line, mono labels,
  the current version an enlarged filled dot flagged `playing`; the spec grid recedes to a quiet
  mono block.
- **Player:** slim bar, tape-strip metadata line, waveform as texture, transport optically
  centred (play triangle `margin-left:2px`).
- **Motion:** the single `--ease` at `--dur`, micro-transitions only, frequency gate honoured
  (chip toggles, `j/k`, `space` stay instant), reduced-motion keeps opacity/colour and drops
  movement.

## Smoothness (the "why does it feel shitty / not responsive" complaint)

- Every interactive element gets an instant local state change on press — never wait for the
  server round-trip to show feedback. Optimistic chip/heart state, reconciled on the response.
- `hx-indicator` on anything that fetches, so a slow request never looks like a dead click.
- No layout shift on swap: fixed row heights, reserved space for counts and covers.
- Search debounced ~150ms with the result count updating live.
- Scroll position and rail scroll preserved across every swap (already partly done — verify).

## Acceptance

- Screenshot the library and the song detail at 1440×900 beside `B-vault-balanced.html` and
  `B-vault-pole.html`: same grammar, same rhythm, same restraint.
- `grep -ci "uppercase" owner.css` drops sharply; no console-style table headers remain.
- Guest page renders calm, not poster.
- Every active state is the B inverse chip; the era-coloured rail is gone.
- Full pytest suite green.

---

## RULINGS (design lead, 2026-07-30) — resolve before implementing chips

**Chip-pressed ambiguity.** The two B mockups disagree: `B-vault-balanced` uses an era-colour
underline (`box-shadow:inset 0 -3px 0 var(--era)`); `B-vault-pole` uses plain white with a
dot prefix and no era colour.

**RULING: use the POLE treatment for chips on every surface** — plain white pressed state,
dot prefix, no era colour. Reason: the owner's complaint is precisely that era hues are being
used to mean "selected", which breaks the palette's grammar. An era-coloured underline on a
pressed chip is that same pattern. Era hues mean eras. Accept the small churn to the owner's
existing chips; consistency across surfaces beats mockup-verbatim here.

**KEEP (do not "fix"):** the now-playing row indicator and the queue is-current indicator, both
era-coloured box-shadows. These match `B-vault-balanced`'s own `.row.now` pattern and are
semantically correct — the playing song genuinely has that era. This is NOT the graft being
removed.

**`var(--era)` as a full-surface fill is banned.** Four violations in `guest.css`
(dig-primary, bigplay, heart-pressed, chip-pressed) → all become `rgba(255,255,255,.92)`.
Era colour is for cover fields, the 3px thread, and the now-playing indicator. Nothing else.

**guest.css is the priority target** — it still carries the entire C-poster stack (Archivo Black
`@font-face`, `.hero/.hero2` clamp(52–78px) uppercase, `.poster-strip`, rotated `.stamp`
ticket-punches) plus A-console tells (1px outlines instead of gradient elevation, uppercase
filter-group labels, band rows split into six metadata spans instead of B's single mono line).
Strip all of it; the guest page becomes B-vault-pole's calm.

**owner.css remaining A tells:** the 9-column `.library-table-head`/`.song-row` admin grid
(→ B's flowing row: cover object, title, one mono metadata line, version dots) and the
era-coloured rail on selected filters at `owner.css:115-118` (→ B inverse chip).

Full mechanical file:line checklist: `reports/design-b-fidelity.md`.
