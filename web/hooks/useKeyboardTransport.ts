"use client";

import {useEffect} from "react";
import {usePlayer} from "@/components/PlayerProvider";

export function useKeyboardTransport() {
  const player = usePlayer();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName)) return;
      if (event.key === " ") {
        event.preventDefault();
        player.toggle();
      } else if (event.key === "j") {
        player.next();
      } else if (event.key === "k") {
        player.prev();
      } else if (event.key === "/") {
        event.preventDefault();
        document.querySelector<HTMLInputElement>(".search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [player]);
}
