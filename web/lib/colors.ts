// Camelot key colours.
//
// Camelot notation puts the twelve keys on a wheel where neighbours mix
// harmonically, and every DJ tool (Mixed In Key, Rekordbox, Serato) paints that
// wheel with the same rough hue order. Reusing it means adjacent hues in the Key
// column ARE adjacent keys, so harmonic neighbours read at a glance rather than
// being decoded one row at a time.
//
// Position 1 sits at teal and the wheel walks backwards through green, yellow,
// orange, red, magenta and violet. Minor (A) is the darker, less saturated ring;
// major (B) is the brighter outer one, matching how those tools draw it.
//
// Keep ORIGIN/STEP in lockstep with cr8/art.py (pinned by tests/test_art.py).
export const CAMELOT_HUE_ORIGIN = 195;
export const CAMELOT_HUE_STEP = 27;

export function camelotHue(camelot?: string | null): number | null {
  if (!camelot) return null;
  const match = /^(\d{1,2})([AB])$/i.exec(camelot.trim());
  if (!match) return null;
  const position = Number(match[1]);
  if (position < 1 || position > 12) return null;
  return (CAMELOT_HUE_ORIGIN - (position - 1) * CAMELOT_HUE_STEP + 360) % 360;
}

export function camelotColor(camelot?: string | null): string | null {
  const hue = camelotHue(camelot);
  if (hue === null || !camelot) return null;
  const match = /^(\d{1,2})([AB])$/i.exec(camelot.trim());
  if (!match) return null;
  const minor = match[2].toUpperCase() === "A";
  return minor
    ? `oklch(0.74 0.105 ${hue})`
    : `oklch(0.82 0.125 ${hue})`;
}

// One hue per tag dimension, so a glance at the inspector tells you WHICH kind
// of judgment is set without reading the group headings. Status and keeper stay
// white: they are the primary verdict and should outrank the descriptive tags.
const DIMENSION_HUE: Record<string, number> = {
  vibe: 320,
  instr: 195,
  collab: 55,
  use: 150,
  problem: 25,
};

export function dimensionActive(dim: string): string | null {
  const hue = DIMENSION_HUE[dim];
  return hue === undefined ? null : `oklch(0.80 0.125 ${hue})`;
}

export function dimensionWash(dim: string): string | null {
  const hue = DIMENSION_HUE[dim];
  return hue === undefined ? null : `oklch(0.80 0.125 ${hue} / 0.14)`;
}

// The inspector lists keys by name while the table has the Camelot code, so the
// two would colour differently without this. Same wheel, same hue, one source.
const CAMELOT_BY_KEY: Record<string, string> = {
  "A minor": "8A", "B minor": "10A", "C minor": "5A", "D minor": "7A",
  "E minor": "9A", "F minor": "4A", "G minor": "6A",
  "A# minor": "3A", "C# minor": "12A", "D# minor": "2A",
  "F# minor": "11A", "G# minor": "1A",
  "A major": "11B", "B major": "1B", "C major": "8B", "D major": "10B",
  "E major": "12B", "F major": "7B", "G major": "9B",
  "A# major": "6B", "C# major": "3B", "D# major": "5B",
  "F# major": "2B", "G# major": "4B",
};

export function camelotForKey(name?: string | null): string | null {
  if (!name) return null;
  return CAMELOT_BY_KEY[name.trim()] ?? null;
}

export function keyNameColor(name?: string | null): string | null {
  if (!name) return null;
  return camelotColor(camelotForKey(name) ?? name);
}
