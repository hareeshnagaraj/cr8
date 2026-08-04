"use client";

// Digging, in the browser.
//
// The library page already holds every track the server returned, so narrowing
// while you type does not need a network round trip or a second copy of the
// catalogue. You type, the pile narrows under your fingers, and the server's
// answer replaces it a moment later for anything that must be exact - a deep
// link, a tag filter, a shared URL.
//
// The scoring is deliberately explicable. Nothing should surface without an
// obvious reason, because a crate you cannot predict is one you stop trusting.

import type {Track} from "@/components/PlayerProvider";

function haystacks(track: Track) {
  return {
    title: String(track.title ?? "").toLowerCase(),
    key: String(track.key_canon ?? "").toLowerCase(),
    camelot: String(track.key_camelot ?? "").toLowerCase(),
    era: String(track.era ?? "").toLowerCase(),
    status: String(track.status ?? "").toLowerCase(),
  };
}

function score(track: Track, needle: string): number {
  const {title, key, camelot, era, status} = haystacks(track);
  if (title === needle) return 1000;
  if (title.startsWith(needle)) return 800 - title.length;
  const at = title.indexOf(needle);
  if (at > 0) {
    // A hit at a word boundary reads as intentional; mid-word is incidental.
    const boundary = /[\s\-_.()]/.test(title[at - 1]);
    return (boundary ? 600 : 450) - Math.min(at, 40);
  }
  if (camelot === needle) return 400;
  if (key.includes(needle)) return 300;
  if (era.includes(needle)) return 250;
  if (status === needle) return 200;
  return -1;
}

// Fuzzy in the way that matters when you half-remember a name: the letters you
// typed appear in order. "skydrv" finds "Skylinedrive".
function subsequence(haystack: string, needle: string): boolean {
  let index = 0;
  for (const character of haystack) {
    if (character === needle[index]) index += 1;
    if (index === needle.length) return true;
  }
  return false;
}

export type LocalNarrow = {
  q?: string;
  bpmMin?: number | null;
  bpmMax?: number | null;
};

export function narrow(tracks: Track[], filters: LocalNarrow): Track[] {
  const needle = (filters.q ?? "").trim().toLowerCase();
  const scored: {track: Track; rank: number}[] = [];

  for (const track of tracks) {
    const bpm = track.bpm ?? null;
    if (filters.bpmMin != null && (bpm == null || bpm < filters.bpmMin)) continue;
    if (filters.bpmMax != null && (bpm == null || bpm > filters.bpmMax)) continue;

    if (!needle) {
      scored.push({track, rank: 0});
      continue;
    }
    let rank = score(track, needle);
    if (rank < 0 && needle.length >= 3) {
      if (subsequence(haystacks(track).title, needle)) rank = 50;
    }
    if (rank >= 0) scored.push({track, rank});
  }

  if (!needle) return scored.map((entry) => entry.track);
  scored.sort(
    (a, b) =>
      b.rank - a.rank ||
      String(a.track.title).localeCompare(String(b.track.title)),
  );
  return scored.map((entry) => entry.track);
}

export function bpmBounds(tracks: Track[]): [number, number] {
  let low = Infinity;
  let high = -Infinity;
  for (const track of tracks) {
    const bpm = track.bpm ?? null;
    if (bpm == null) continue;
    if (bpm < low) low = bpm;
    if (bpm > high) high = bpm;
  }
  if (low === Infinity) return [60, 180];
  return [Math.floor(low), Math.ceil(high)];
}

export function bpmHistogram(
  tracks: Track[],
  low: number,
  high: number,
  buckets = 28,
): number[] {
  const counts = new Array(buckets).fill(0);
  const span = Math.max(1, high - low);
  for (const track of tracks) {
    const bpm = track.bpm ?? null;
    if (bpm == null) continue;
    const slot = Math.min(
      buckets - 1,
      Math.max(0, Math.floor(((bpm - low) / span) * buckets)),
    );
    counts[slot] += 1;
  }
  return counts;
}
