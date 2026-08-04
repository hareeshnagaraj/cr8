export type Era = {
  name: string;
  css: string;
  color: string;
};

export type EraColors = Record<string, string>;

// Cover composition only: shifts the second gradient stop relative to the
// Camelot hue so the same key in a different period reads as a relative. Era
// identity and fill colour come from the eras table via /api/eras.
export const COVER_HUE_SHIFT: Record<string, number> = {
  working: 26,
  NOVA1: -30,
  PELICANA: 52,
  undated: 12,
};

let erasPromise: Promise<Era[]> | null = null;

export function getEras(): Promise<Era[]> {
  if (!erasPromise) {
    erasPromise = fetch("/api/eras", {credentials: "same-origin"})
      .then((response) => {
        if (!response.ok) throw new Error(`eras request failed: ${response.status}`);
        return response.json() as Promise<Era[]>;
      })
      .catch((error) => {
        erasPromise = null;
        throw error;
      });
  }
  return erasPromise;
}

export function colorsByName(eras: Era[]): EraColors {
  return Object.fromEntries(eras.map((era) => [era.name, era.color]));
}

/** Hydrate --era-* CSS vars from the eras table so globals.css is fallback only. */
export function applyEraCssVars(eras: Era[]): void {
  if (typeof document === "undefined") return;
  for (const era of eras) {
    document.documentElement.style.setProperty(`--era-${era.css}`, era.color);
  }
}
