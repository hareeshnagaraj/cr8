# SPEC r6 — UI issues from real use (screenshot evidence, 2026-07-30)

Owner is using the app daily. These are observed defects, not opinions. The through-line:
**everything is oversized, and content clips or collides.** Design B is calm and dense; the
build is inflated and colliding.

---

## 1. Row hover controls collide with the row (HIGH)

The hover tail (pause · heart · # · + · ↓ · … · ›) **overlaps the row content and each other**,
and the trailing `›` renders as a large white block that covers the row edge. A dark panel also
appears beneath it. Nothing should overlap.

Fix: the tail is a fixed-width flex group pinned right, inside the row's own padding box, with
the title/meta column shrinking to make room (`min-width:0` + ellipsis). The `›` is a normal
32×32 icon button, never a filled block. No absolute positioning that escapes the row.

## 2. Right panel clips horizontally (HIGH)

In the tag editor: `release` is cut off mid-word at the right edge, `D minor` is cut off,
and the chips run past the panel. The panel is 380px and the chips assume more.

Fix: the chip rows wrap (`flex-wrap:wrap`) inside the panel's padding; nothing horizontally
scrolls or clips. Combined with §3 the chips get small enough that they fit.

## 3. Controls are far too big (HIGH — this is the main visual problem)

Measured from the screenshots: status chips ~60px tall, keeper digits ~60px, key chips ~60px,
Tags-page rows ~130px each, collection rows ~96px each. Seven tags fill an entire 1440×900
screen. Nine collection tracks fill the screen.

Fix, matching `B-vault-*.html`:
- **Chips** (status / keeper / key / vibe / instr / collab): height **32px**, font 13px mono,
  padding `0 10px`, gap 6px. They are labels, not buttons-of-consequence.
- **Keeper 0–5**: a compact segmented row, 32px tall, ~34px per digit.
- **Library rows**: 44px default (56px comfortable toggle), single line + one mono meta line.
- **Tags page**: a real table — one row per tag at ~40px: `dim · count | value | source |
  [rename field] [Rename] [Delete]`. The rename input is ~220px, not 570px. All 40+ tags should
  be scannable on one screen.
- **Collection rows**: 44px, matching library rows exactly.

## 4. Selected status chip has a stray glyph and overlapping label (MEDIUM)

The selected `demo` chip shows a `↗` arrow floating beside it and a second, overlapping label
underneath ("source"/"human" style text colliding with the chip).

Fix: provenance belongs as a small mono note *under the group*, not layered on the chip.
One line: `demo · set by you`. Delete the arrow glyph.

## 5. Stray numbers in a broken left column (MEDIUM)

At the far left of the library, disconnected numbers appear (16, 2, 14, 3, −, 470) with no
labels or alignment — a column bleeding through or a mis-scoped count render.

Fix: identify the element and either give it a proper header and alignment or remove it.
Nothing unlabelled may render in the gutter.

## 6. Top-bar type grammar is mixed (MEDIUM)

Nav items are sentence-case sans (`Library`, `Tags`, `Collections`) while the actions are
lowercase mono in pills (`shuffle everything`, `dig`, `dig untagged`). Two grammars in one bar.

Fix: pick one. Per B: the actions become sentence-case sans matching the nav, with the primary
(`Shuffle everything`) as the filled chip and the others quiet. Keep mono for metadata only.

## 7. `Share` nav item must go (LOW — part of the unify work)

Guest/share links are being removed. Drop the nav item and its page.

## 8. Bulk bar copy is confusing (LOW)

`0 selected · Download selected · up to 50 originals · 2 GB · status — · tag — · tag value ·
Add · Remove · more` reads as noise, and the cap text shows even when nothing is selected.

Fix: when nothing is selected the bar is quiet — `Select songs to tag or download` only.
When a selection exists: `12 selected` + actions, and the cap note appears only if the
selection exceeds it.

## 9. Duplicate-looking rows (INVESTIGATE)

`Newyearsday Oliver Remix Scratch1` and `Newyearsday Oliver Remix`, both `C# minor · 12A · 2:45`.
Same key, same duration — likely the same audio under two names, or a resolver miss.
Check whether these are distinct bounces; if the fingerprint matches, file a merge-review item.

---

## Acceptance

Screenshot at 1440×900 after the fixes: the Tags page shows 15+ tags on one screen; a
collection shows 15+ tracks; no element overlaps another anywhere; nothing clips at the right
edge of the detail panel; the top bar reads as one typographic system.

---

## 10. PERFORMANCE — the library page is 438 KB (HIGH, this is the "feels slow")

Measured: `GET /` returns **438,767 bytes** of HTML in 245 ms for 120 rows — about **3.6 KB of
markup per row**, because every row ships a checkbox, a seven-button hover tail, chips, and
data attributes whether or not it is ever hovered. Tailscale is NOT the cause (3 ms over the
tailnet); the DOM is.

Fix:
- **Render the hover tail on demand.** One shared tail element that moves to the hovered/focused
  row, or a template cloned on hover — not 120 copies in the document.
- Trim per-row data attributes to what the player actually needs (track id, audio url, title);
  derive the rest from the catalog when the row is activated.
- Target: **under 120 KB** for the first library page, and no more than ~120 row elements in the
  DOM at any time.
- Verify with `curl -w '%{size_download}'` and a DOM node count.

## 11. COLLECTIONS ARE BROKEN (HIGH)

Both existing collections contained **all 472 songs** — "Create from queue" collects the entire
library rather than a meaningful set, so a collection is indistinguishable from the library.
(The two test collections have been deleted.)

Fix:
- **Create from queue** uses the *current queue* — and if the queue is the whole library, say so
  and require a name plus confirmation, or refuse.
- Add the paths that actually matter: **create from the current selection** (multi-select) and
  **create from the current filter**, both named at creation.
- A collection page must show *its* count, allow removing a track, and drag-reorder must persist.
- `POST /collections` previously 400'd with only a name — make the contract explicit and make
  every UI entry point satisfy it.
