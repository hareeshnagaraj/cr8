"use client";

import {useCallback, useState} from "react";

export function useSelection() {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [selectMode, setSelectMode] = useState(false);

  const togglePicked = useCallback((bounceUlid: string) => {
    setPicked((previous) => {
      const next = new Set(previous);
      if (next.has(bounceUlid)) next.delete(bounceUlid);
      else next.add(bounceUlid);
      return next;
    });
  }, []);

  const dropPicked = useCallback(() => {
    setPicked(new Set());
  }, []);

  const clearPicked = useCallback(() => {
    setPicked(new Set());
    setSelectMode(false);
  }, []);

  return {picked, selectMode, setSelectMode, togglePicked, dropPicked, clearPicked};
}
