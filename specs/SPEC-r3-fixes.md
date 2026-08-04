# SPEC r3 — download, the DIG flow, and the AI tells

Owner's words, in priority order. Everything here is diagnosed from the live app, not inferred.

---

## 1. DOWNLOAD (do this first — explicitly the immediate feature)

> "The immediate feature I need is the ability to just download the sample straight from the
> browser. It should just transfer and let me download it."

**Owner app — download anything, anywhere it appears:**
- Song detail: a **Download** control offering the **original bounce** (the real wav/aif from the
  corpus, full quality — this is the thing a producer actually wants to drag into Ableton) and
  the mp3-320 rendition. Label them by what they are and their size: `original · wav · 38 MB`,
  `mp3 · 320 · 7 MB`.
- Every **stem** already has a share button — give each a download too (the FLAC from `stems/`).
- Row hover-tail: a download affordance next to the existing ▶ ♡ # + controls.
- Multi-select → **Download selected** as a zip, streamed (never buffered fully in memory).
- Keyboard: `d` downloads the cursor subject's original.

**Implementation notes (binding):**
- New route `GET /download/{bounce_ulid}?format=original|mp3` on the OWNER app. Resolve the path
  from the catalog (never from a client-supplied path), `resolve(strict=True)` + containment
  check against the corpus root and the mirror root, then `FileResponse` with
  `Content-Disposition: attachment; filename="<clean original stem>.<ext>"` and Range support.
- **The corpus stays read-only** — this reads and serves; it never writes, moves, or renames.
- Stems: `GET /download/stem/{stem_ulid}` from `stems/`.
- Zip: `GET /download/selection?ulids=...` streaming a zip; cap the count and total bytes, and
  say so in the UI when the cap trims a selection.
- Filenames must be the human name, not the ULID: `7-29-26-stayhere-cm-v2.wav`.

**Guest app:** downloads are **off by default** per share, with a per-share `allow_download`
toggle on the mint screen (the safe-share default already specified). When enabled, guests get
the mp3 only — never the original, never stems, unless separately enabled.

**Verify:** click download on a song → the real file lands with its original name and byte-for-byte
matches the corpus file (`shasum` both); a guest with downloads off gets 403; with downloads on
gets the mp3 and still 403 on `format=original`.

---

## 2. THE DIG FLOW IS INCOHERENT (highest UX severity)

> "Once you enter a flow it's really confusing. The DIG flow, I don't really get what's going on.
> I get that you were supposed to add tags, but it's not really working as it should be."

**Diagnosed, with evidence from a live DIG:** pressing DIG started *Suntribe Unmastered* — but
the list still showed all 471 rows starting at Stayhere, and the right panel still read
`CURSOR SUBJECT: Stayhere`. **The song you just dug up appears nowhere except one small line in
the player bar, and the tag editor is still pointed at a different song.** So "DIG, then tag what
you heard" tags the wrong thing. That is the whole complaint, and it is a real bug, not taste.

**Root cause:** the detail panel follows the *cursor*, and playback never moves the cursor.

**Fixes:**
1. **Playback moves the cursor.** DIG, SHUFFLE, auto-advance, and any row play all set the
   cursor to the now-playing song, so the detail panel and tag editor always describe what you
   are hearing. The pin toggle (lock panel to a chosen song) stays as the deliberate override.
2. **Scroll the playing row into view and mark it** — a persistent now-playing marker in the
   list, not only in the player bar.
3. **DIG is a mode, and the app must say so.** When digging: the header states it
   (`DIGGING · 8 played · 463 to go`), the DIG button reads as engaged, and there is an obvious
   way out (`Stop digging` / Escape). Right now the only hint is the word "digging" in 9px grey
   in the player subtitle.
4. **Say why it picked this.** Per SPEC-v2 A3, show the reason inline: `never played`,
   `not since Mar`. Currently invisible.
5. **Tag-while-digging is the point** — with the cursor following playback, the existing chip row
   and `1–9` keys now act on the right song. Add a compact "tag what's playing" affordance in the
   player bar itself so the hand never has to travel to the right panel mid-listen.
6. **Fix the counter.** The player reads `0 played · 1 of 471` while digging, which means nothing.
   During DIG show dug-count and remaining; during shuffle show queue position.

**Verify:** press DIG → the right panel shows the song you are hearing, its row is marked and
scrolled to, the header says you are digging with a way out, and a chip tap tags the song you
just heard.

---

## 3. AI TELLS — the sidebar, and a sweep

> "There's a lot of weird AI slop and obvious tells too. Like the left side sidebar, when it
> selected that highlighting... it kind of just sucks."

**Confirmed at `owner.css:134`:**
```css
.rail-filter[aria-pressed="true"]{
  color:var(--t1);background:rgba(255,255,255,.085);box-shadow:inset 3px 0 var(--era-nova1)
}
```
That is the **coloured accent-bar-on-selected-row** pattern — named explicitly in the anti-slop
banlist. It is also *wrong on its own terms*: it paints the selected filter in the **NOVA1 era
colour** regardless of which filter is selected, so a colour that means "era: NOVA1" everywhere
else in the app here means "selected". The palette's own grammar is broken.

**Replace with a chosen treatment, not a default.** Options that fit the ratified system: weight
and colour shift alone (mono label goes from `--t2` to `--t1` at a heavier weight, count goes
white); or an inset fill with **no** rail; or the count pill inverting. Pick one, apply it to
every selected/active state in the app consistently, and delete the era-coloured inset rail.

**Then sweep the whole UI for the same class of tell** against the banlist in
`canon/ratifications/2026-07-29-crate-design.md`: accent rails on cards, uniform 8px-everything
spacing, default focus rings, three-equal-columns, centred empty states, generic microcopy
("Metadata first. Tags stay optional." reads like filler — make it say something true or delete
it), and any place a colour is used decoratively rather than semantically. Era colours are for
eras. The signal red is for UNHEARD. Nothing else gets hue.

---

## 4. Also confirmed while looking

- `cr8 build` crashed with `KeyError: 'use'` — FIXED, but it means the nightly would have
  failed silently every night. Add a test.
- Tags page Delete buttons carry a stray red underline — diagnose and remove.
- `POST /collections` with only a name returns 400; the UI's "Save as collection" must send
  whatever the handler actually requires, or the handler must accept a name plus the current
  filter query.
