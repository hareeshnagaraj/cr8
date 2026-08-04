"use client";

import {useEffect, useRef} from "react";
import {usePlayer, type Track} from "@/components/PlayerProvider";
import {spreadShuffle} from "@/lib/shuffle";

export function useCuedLanding(allTracks: Track[], loading: boolean) {
  const player = usePlayer();
  const autoCueHandled = useRef(false);

  useEffect(() => {
    if (loading || autoCueHandled.current) return;
    // The first completed library request owns the decision for this page
    // load. A failure/empty result or an existing paused/playing queue is a
    // final no-op, so a later filter response cannot unexpectedly take over.
    autoCueHandled.current = true;
    if (!allTracks.length || player.current || player.queue.length) return;
    player.cue(spreadShuffle(allTracks), 0);
  }, [allTracks, loading, player.cue, player.current, player.queue.length]);
}
