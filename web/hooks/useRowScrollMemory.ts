"use client";

import {useEffect, useRef, type RefObject} from "react";

const KEY = "cr8:libraryScroll";

export function useRowScrollMemory(
  scrollRef: RefObject<HTMLDivElement | null>,
  trackCount: number,
) {
  // Restore only once the rows exist, otherwise there is nothing to scroll to.
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current || !trackCount) return;
    const saved = Number(sessionStorage.getItem(KEY) || 0);
    restored.current = true;
    if (saved <= 0) return;
    let tries = 0;
    const settle = () => {
      const viewport = scrollRef.current;
      if (!viewport) return;
      viewport.scrollTop = saved;
      tries += 1;
      if (Math.abs(viewport.scrollTop - saved) > 2 && tries < 12) {
        requestAnimationFrame(settle);
      }
    };
    requestAnimationFrame(settle);
  }, [trackCount, scrollRef]);

  useEffect(() => {
    const viewport = scrollRef.current;
    if (!viewport) return;
    // Write when the scroll stops, not on every frame of it.
    //
    // This wrote to sessionStorage on every scroll event - up to 120 times a
    // second on a flick - and setItem is synchronous and disk-backed, on the
    // same thread that is recycling the virtualised rows. It was the only
    // per-frame main-thread work left in the list, and the value it saved
    // mid-flick was thrown away by the next event anyway.
    const save = () => {
      sessionStorage.setItem(KEY, String(viewport.scrollTop));
    };
    if ("onscrollend" in window) {
      viewport.addEventListener("scrollend", save, {passive: true});
      return () => viewport.removeEventListener("scrollend", save);
    }
    // Older WebKit has no scrollend; settle on a timer instead. Still one
    // write per gesture rather than one per frame.
    let timer = 0;
    const settle = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(save, 120);
    };
    viewport.addEventListener("scroll", settle, {passive: true});
    return () => {
      window.clearTimeout(timer);
      viewport.removeEventListener("scroll", settle);
    };
  }, [scrollRef]);
}
