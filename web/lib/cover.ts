// Covers drawn from the music itself.
//
// Every song used to get a 1400x1400 JPEG: a two-tone gradient with the title
// written across it. At the 48px the library actually shows, the text was
// illegible, and the real cost was invisible - a browser decodes an image at its
// natural size regardless of display size, so each 49KB file became 7.5MB of
// bitmap and twenty-odd visible rows held about 164MB, churned on every fling.
//
// These are CSS. No request, no decode, no <img>. What varies is the music:
//
//     hue        Camelot key, through the same function the Key column uses
//     second hue era
//     angle      tempo, quantised to 15 degrees
//     split      duration, six steps
//     mark       one square placed from the ULID, sized by version count
//
// Quantising is the point. Continuous values give 470 covers that are all
// slightly different and therefore all the same; steps give families, so a
// steep amber tile becomes recognisable as a fast track in that key.
//
// Every tile then shares an identical hairline and vignette. That constant is
// what makes a set read as art-directed rather than generated.

import type {Track} from "@/components/PlayerProvider";
import {camelotForKey, camelotHue} from "@/lib/colors";
import {COVER_HUE_SHIFT} from "@/lib/eras";

function camelotOf(track: Track): {code: string; minor: boolean} | null {
  const raw =
    track.key_camelot?.trim() ||
    camelotForKey(String(track.key_canon ?? "").trim()) ||
    "";
  const match = /^(\d{1,2})([AB])$/i.exec(raw);
  if (!match) return null;
  const position = Number(match[1]);
  if (position < 1 || position > 12) return null;
  return {code: `${position}${match[2].toUpperCase()}`, minor: match[2].toUpperCase() === "A"};
}

// A small deterministic hash of the ULID. Only used for placing the mark, so a
// track with no key still differs from its neighbours.
function hash(value: string): number {
  let out = 0;
  for (let i = 0; i < value.length; i += 1) {
    out = (out * 31 + value.charCodeAt(i)) >>> 0;
  }
  return out;
}

export type Cover = {background: string};

const cache = new Map<string, Cover>();

export function coverFor(track: Track): Cover {
  const key = track.bounce_ulid || track.song_ulid || track.title;
  const cached = cache.get(key);
  if (cached) return cached;

  const camelot = camelotOf(track);
  const seed = hash(key);

  // Unkeyed tracks still get a stable hue rather than falling back to grey,
  // otherwise anything undetected reads as broken.
  const hue = camelot ? (camelotHue(camelot.code) as number) : seed % 360;
  const minor = camelot ? camelot.minor : true;

  const era = String(track.era ?? "undated");
  const hue2 = (hue + (COVER_HUE_SHIFT[era] ?? COVER_HUE_SHIFT.undated) + 360) % 360;

  // Tempo becomes the angle. 60-180bpm across 180 degrees, in 15 degree steps.
  const bpm = track.bpm ?? 0;
  const angle = bpm
    ? Math.round(Math.min(180, Math.max(0, ((bpm - 60) / 120) * 180)) / 15) * 15
    : 45;

  // Duration moves where the two colours meet, in six steps.
  const seconds = track.duration_s ?? 0;
  const split = seconds
    ? 32 + Math.round(Math.min(1, seconds / 360) * 5) * 7.2
    : 50;

  const step = ((seed >>> 23) % 3) - 1; // -1, 0, 1
  const light = (minor ? 0.58 : 0.68) + step * 0.045;
  const chroma = (minor ? 0.105 : 0.128) + step * 0.012;

  // One mark on a 6x6 grid, off the outermost ring so it never touches the
  // hairline, and never mirrored - mirroring is the strongest tell of a
  // generated avatar.
  //
  // It is a background LAYER rather than a child element, and nothing here
  // carries a blur radius. The first version used an inset box-shadow with an
  // 18px blur plus a shadowed child, and the probe measured the result: scroll
  // got worse than the 164MB of JPEG it replaced, because a blurred shadow
  // re-rasterises as the row moves while an image uploads to the GPU once.
  const versions = track.version_count ?? 1;
  const size = versions > 2 ? 30 : versions > 1 ? 23 : 17;
  const left = 14 + ((seed >>> 3) % 5) * 14;
  const top = 14 + ((seed >>> 11) % 5) * 14;

  const base =
    `linear-gradient(${angle}deg, ` +
    `oklch(${light} ${chroma} ${hue}) ${split.toFixed(0)}%, ` +
    `oklch(${(light - 0.26).toFixed(2)} ${(chroma * 0.5).toFixed(3)} ${hue2}) 100%)`;

  // Hard-stopped radial gradients are flat fills to the rasteriser: a square of
  // colour with no blur to compute.
  const mark =
    `radial-gradient(circle at ${left + size / 2}% ${top + size / 2}%, ` +
    `rgba(255,255,255,0.30) 0 ${size / 2.4}%, rgba(255,255,255,0) ${size / 2.4}%)`;

  // A corner shade, as a gradient rather than a blurred inset shadow.
  const shade =
    "linear-gradient(315deg, rgba(0,0,0,0.34) 0%, rgba(0,0,0,0) 58%)";

  const cover: Cover = {background: `${mark}, ${shade}, ${base}`};
  cache.set(key, cover);
  return cover;
}
