"use client";

import {useCallback, useEffect, useRef, useState, type RefObject} from "react";
import {filtersToQuery, type Filters} from "@/components/FilterRail";
import {type Track} from "@/components/PlayerProvider";
import {narrow} from "@/lib/dig";

export type LibraryTrack = Track & {
  ears?: number | null;
  vibe_tags?: string[] | null;
};

export function useLibraryQuery({
  q,
  sort,
  filters,
  bpm,
  scrollRef,
}: {
  q: string;
  sort: string;
  filters: Filters;
  bpm: [number | null, number | null];
  scrollRef: RefObject<HTMLDivElement | null>;
}) {
  const [allTracks, setAllTracks] = useState<LibraryTrack[]>([]);
  const [tracks, setTracks] = useState<LibraryTrack[]>([]);
  const [libraryTracks, setLibraryTracks] = useState<LibraryTrack[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [shuffled, setShuffled] = useState(false);
  const [randomRequest, setRandomRequest] = useState(0);

  const queryRef = useRef<string | null>(null);
  const filterQuery = filtersToQuery(filters);
  const filtersActive = Boolean(
    q || filterQuery || bpm[0] !== null || bpm[1] !== null,
  );

  // Narrow what is already here on every keystroke and every drag of the tempo
  // handles. This is the whole feel of the thing: no spinner, no wait, the pile
  // just gets smaller. The server catches up underneath.
  useEffect(() => {
    setTracks(narrow(allTracks, {q, bpmMin: bpm[0], bpmMax: bpm[1]}));
    if (shuffled) setShuffled(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allTracks, q, bpm[0], bpm[1]]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setLoading(true);
      const query = filterQuery;
      const randomSeed = sort === "random" ? window.crypto.randomUUID() : "";
      const key = `${q}|${sort}|${randomSeed}|${query}`;
      const range =
        (bpm[0] != null ? `&bpm_min=${bpm[0]}` : "") +
        (bpm[1] != null ? `&bpm_max=${bpm[1]}` : "");
      const seed = randomSeed
        ? `&random_seed=${encodeURIComponent(randomSeed)}`
        : "";
      fetch(
        `/api/library?limit=1000&sort=${sort}&q=${encodeURIComponent(q)}${query ? `&${query}` : ""}${range}${seed}`,
        {
          credentials: "same-origin",
        },
      )
        .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
        .then((data) => {
          const nextTracks = data.tracks as LibraryTrack[];
          setAllTracks(nextTracks);
          if (!filtersActive) setLibraryTracks(nextTracks);
          setTotal(data.total);
          setShuffled(false);
          if (queryRef.current !== null && queryRef.current !== key) {
            if (scrollRef.current) scrollRef.current.scrollTop = 0;
            sessionStorage.setItem("cr8:libraryScroll", "0");
          }
          queryRef.current = key;
        })
        .catch(() => setAllTracks([]))
        .finally(() => setLoading(false));
      // A keystroke no longer blocks on this, so it can settle rather than
      // firing at typing speed.
    }, q ? 260 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, sort, filters, bpm[0], bpm[1], randomRequest]);

  const requestRandom = useCallback(
    () => setRandomRequest((request) => request + 1),
    [],
  );

  return {
    tracks,
    setTracks,
    allTracks,
    libraryTracks,
    total,
    loading,
    shuffled,
    setShuffled,
    filtersActive,
    requestRandom,
  };
}
