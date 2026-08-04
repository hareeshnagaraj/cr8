"use client";

import {memo} from "react";
import {coverFor} from "@/lib/cover";
import type {Track} from "./PlayerProvider";

// The cover is two elements and no network. memo matters here specifically:
// rows are recycled by the virtualiser, so without it a fling would rebuild the
// same handful of covers over and over.
export const Cover = memo(function Cover({
  track,
  className = "row-art",
}: {
  track: Track;
  className?: string;
}) {
  const {background} = coverFor(track);
  // One element, no children: the whole cover is layered gradients.
  return <span className={className} style={{background}} aria-hidden="true" />;
});
