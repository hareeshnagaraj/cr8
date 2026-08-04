"use client";

import {useCallback, useEffect, useRef, useState} from "react";

export type BulkFeedback = {
  kind: "success" | "error";
  message: string;
} | null;

export function useHearts(setBulkFeedback: (feedback: BulkFeedback) => void) {
  const [hearts, setHearts] = useState<Set<string>>(new Set());
  const heartsRef = useRef(hearts);
  heartsRef.current = hearts;

  useEffect(() => {
    fetch("/api/hearts", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((list: string[]) => setHearts(new Set(list)))
      .catch(() => undefined);
  }, []);

  const heart = useCallback(async (bounceUlid: string) => {
    const wasHearted = heartsRef.current.has(bounceUlid);
    setBulkFeedback(null);
    setHearts((prev) => {
      const next = new Set(prev);
      if (next.has(bounceUlid)) next.delete(bounceUlid);
      else next.add(bounceUlid);
      return next;
    });
    const response = await fetch(`/api/reactions/${bounceUlid}/heart`, {
      method: "POST",
      credentials: "same-origin",
      headers: {"X-CR8-Request": "1"},
    }).catch(() => null);
    const restoreHeart = () => setHearts((prev) => {
      const next = new Set(prev);
      if (wasHearted) next.add(bounceUlid);
      else next.delete(bounceUlid);
      return next;
    });
    if (!response?.ok) {
      restoreHeart();
      setBulkFeedback({kind: "error", message: "Couldn’t update that heart. Try again."});
      return;
    }
    const result = await response.json().catch(() => null);
    if (typeof result?.hearted !== "boolean") {
      restoreHeart();
      setBulkFeedback({kind: "error", message: "Couldn’t update that heart. Try again."});
      return;
    }
    setHearts((prev) => {
      const next = new Set(prev);
      if (result.hearted) next.add(bounceUlid);
      else next.delete(bounceUlid);
      return next;
    });
  }, [setBulkFeedback]);

  return {hearts, heart};
}
