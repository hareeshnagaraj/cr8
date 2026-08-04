"use client";

import {useEffect, useMemo, useState} from "react";
import type {CSSProperties} from "react";
import {Cover} from "@/components/Cover";
import type {Track} from "@/components/PlayerProvider";

const SIZES = [34, 48, 76, 120] as const;
type PreviewStyle = "spectral" | "envelope";
type PreviewAvailability = Record<PreviewStyle, Set<string>>;

function bpmAngle(bpm?: number | null): number | null {
  if (!bpm) return null;
  const continuous = Math.min(180, Math.max(0, ((bpm - 60) / 120) * 180));
  return Math.round(continuous / 15) * 15;
}

function PreviewSlot({
  src,
  label,
  available,
}: {
  src: string;
  label: string;
  available: boolean;
}) {
  const [state, setState] = useState<"loading" | "ready" | "missing">("loading");

  useEffect(() => setState("loading"), [src]);

  if (!available) {
    return (
      <span
        className="sheet-tile cover-preview-slot is-missing"
        aria-label={`${label} unavailable`}
      />
    );
  }

  return (
    <span
      className={`sheet-tile cover-preview-slot is-${state}`}
      aria-label={state === "missing" ? `${label} unavailable` : undefined}
    >
      {state !== "missing" ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          onLoad={() => setState("ready")}
          onError={() => setState("missing")}
        />
      ) : null}
    </span>
  );
}

// A contact sheet for judging candidates as a set before any live-art rollout.
export default function Covers() {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [size, setSize] = useState<(typeof SIZES)[number]>(48);
  const [previews, setPreviews] = useState<PreviewAvailability>({
    spectral: new Set(),
    envelope: new Set(),
  });

  useEffect(() => {
    fetch("/api/library?limit=500", {credentials: "same-origin"})
      .then((response) => (
        response.ok ? response.json() : Promise.reject(response.status)
      ))
      .then((data) => setTracks(data.tracks ?? []))
      .catch(() => setTracks([]));
  }, []);

  useEffect(() => {
    fetch("/api/cover-previews", {credentials: "same-origin"})
      .then((response) => (
        response.ok ? response.json() : Promise.reject(response.status)
      ))
      .then((data) => setPreviews({
        spectral: new Set(data.spectral ?? []),
        envelope: new Set(data.envelope ?? []),
      }))
      .catch(() => setPreviews({spectral: new Set(), envelope: new Set()}));
  }, []);

  const collisions = useMemo(() => {
    const groups = new Map<string, number>();
    for (const track of tracks) {
      // Count the raw data tuple. Rendered strings include identity marks, which
      // hid the catalog's collisions even when key, BPM bucket, and era matched.
      const tuple = [
        track.key_camelot?.trim() || track.key_canon?.trim() || null,
        bpmAngle(track.bpm),
        track.era?.trim() || null,
      ] as const;
      const signature = JSON.stringify(tuple);
      groups.set(signature, (groups.get(signature) ?? 0) + 1);
    }
    return {
      distinct: groups.size,
      worst: groups.size ? Math.max(...groups.values()) : 0,
    };
  }, [tracks]);

  const tableStyle = {"--cover-size": `${size}px`} as CSSProperties;

  return (
    <div className="lib page-scroll">
      <header className="lib-head">
        <div>
          <h1 className="lib-title">Cover previews</h1>
          <p className="lib-count num">
            {tracks.length} tracks · {collisions.distinct} distinct data tuples ·{" "}
            worst group {collisions.worst}
          </p>
        </div>
        <div className="lib-actions" aria-label="Cover size">
          {SIZES.map((option) => (
            <button
              key={option}
              className={`btn${size === option ? " btn-main" : ""}`}
              onClick={() => setSize(option)}
            >
              {option}px
            </button>
          ))}
        </div>
      </header>

      <div className="covers-scroll">
        <div className="covers-table" style={tableStyle}>
          <div className="covers-row covers-labels" aria-hidden="true">
            <span>Track</span>
            <span>Live</span>
            <span>Spectral</span>
            <span>Envelope</span>
            <span>CSS tile</span>
          </div>
          {tracks.map((track) => (
            <div className="covers-row" key={track.bounce_ulid}>
              <span className="covers-track" title={track.title}>
                {track.title}
              </span>
              <img
                className="sheet-tile"
                src={`/art/${track.bounce_ulid}`}
                alt=""
                loading="lazy"
              />
              <PreviewSlot
                src={`/art-preview/spectral/${track.bounce_ulid}`}
                label="Spectral preview"
                available={previews.spectral.has(track.bounce_ulid)}
              />
              <PreviewSlot
                src={`/art-preview/envelope/${track.bounce_ulid}`}
                label="Envelope preview"
                available={previews.envelope.has(track.bounce_ulid)}
              />
              <Cover track={track} className="sheet-tile" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
