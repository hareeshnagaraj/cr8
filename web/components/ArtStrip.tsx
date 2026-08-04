"use client";

import {useEffect, useState} from "react";

import {usePlayerTimeline} from "./PlayerProvider";

const STRIP_TRAVEL_PERCENT = 87.5; // (2048 - 256) / 2048; 238px at a 34px tile.
const FINE_POINTER = "(hover: hover) and (pointer: fine)";

function StaticArt({bounceUlid, onPointerEnter}: {
  bounceUlid: string;
  onPointerEnter?: () => void;
}) {
  return (
    <img
      className="row-art"
      src={`/art/${bounceUlid}`}
      alt=""
      loading="lazy"
      onPointerEnter={onPointerEnter}
    />
  );
}

export function ArtStrip({bounceUlid}: {bounceUlid: string}) {
  const {position, duration} = usePlayerTimeline();
  const [failed, setFailed] = useState(false);
  // The strip is garnish and weighs ~200KB; on a slow connection it must not
  // fight the track's startup buffer for the pipe. Static art carries the
  // first seconds; the glide arrives once playback is rolling.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setReady(false);
    const timer = window.setTimeout(() => setReady(true), 3500);
    return () => window.clearTimeout(timer);
  }, [bounceUlid]);

  if (failed || !ready) return <StaticArt bounceUlid={bounceUlid} />;

  const fraction = duration ? Math.max(0, Math.min(1, position / duration)) : 0;
  return (
    <span className="row-art art-strip" aria-hidden="true">
      <img
        className="art-strip-image art-strip-playing"
        src={`/art-strip/${bounceUlid}`}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        style={{transform: `translateX(${-fraction * STRIP_TRAVEL_PERCENT}%)`}}
      />
    </span>
  );
}

// Non-playing rows stay as the original static image until a fine pointer is
// actually over one. Leaving swaps the static image back in, resetting the CSS
// drift while the browser keeps the fetched strip in its cache.
export function HoverArt({bounceUlid}: {bounceUlid: string}) {
  const [hovered, setHovered] = useState(false);
  const [failed, setFailed] = useState(false);

  const enter = () => {
    if (failed || typeof window === "undefined") return;
    if (window.matchMedia(FINE_POINTER).matches) setHovered(true);
  };

  if (!hovered || failed) {
    return <StaticArt bounceUlid={bounceUlid} onPointerEnter={enter} />;
  }

  return (
    <span
      className="row-art art-strip"
      aria-hidden="true"
      onPointerLeave={() => setHovered(false)}
    >
      <img
        className="art-strip-image art-strip-hover"
        src={`/art-strip/${bounceUlid}`}
        alt=""
        loading="lazy"
        onError={() => {
          setFailed(true);
          setHovered(false);
        }}
      />
    </span>
  );
}
