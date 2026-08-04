"use client";

import {useEffect, useState} from "react";
import {usePlayer, type Track} from "@/components/PlayerProvider";

export default function Triage() {
  const [queue, setQueue] = useState<Track[]>([]);
  const [today, setToday] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [failed, setFailed] = useState<string | null>(null);
  const player = usePlayer();

  useEffect(() => {
    fetch("/api/triage", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => {
        setQueue(data.queue ?? []);
        setToday(data.today ?? 0);
      })
      .catch(() => setQueue([]));
  }, []);

  const track = queue[cursor];

  // Verdicts advance the cursor optimistically. A triage pass is a rhythm, and
  // waiting for a round trip between songs breaks it.
  async function verdict(kind: string) {
    if (!track) return;
    // The field is "value". It was "verdict" here, which the handler reads as
    // the empty string and rejects with a 400 - so even once the route was
    // proxied, every verdict would still have been thrown away.
    const body = new URLSearchParams({value: kind});
    setCursor((c) => c + 1);
    if (queue[cursor + 1]) player.play(queue, cursor + 1);
    const response = await fetch(`/triage/${track.bounce_ulid}`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CR8-Request": "1",
      },
      body,
    }).catch(() => undefined);
    // Swallowing the result is what let this go unnoticed: the queue advanced
    // and the next track played, so a discarded verdict was indistinguishable
    // from a saved one. Say so instead, and put the track back.
    if (!response || !response.ok) {
      setFailed(track.bounce_ulid);
      setCursor((c) => Math.max(0, c - 1));
      return;
    }
    setFailed(null);
    setToday((n) => n + 1);
  }

  if (!track) {
    return (
      <div className="lib page-scroll">
        <h1 className="lib-title">Triage</h1>
        <p className="empty">
          {queue.length ? "Queue finished. Nice work." : "Nothing waiting."}
        </p>
      </div>
    );
  }

  return (
    <div className="lib">
      <header className="lib-head">
        <div>
          <h1 className="lib-title">Triage</h1>
          <p className="lib-count num">
            {queue.length - cursor} waiting · {today} done today
          </p>
        </div>
      </header>

      <div className="triage-card">
        {failed ? (
          <p className="empty" role="alert">
            That verdict did not save. Nothing has been recorded for this track.
          </p>
        ) : null}
        <h2 className="triage-title">{track.title}</h2>
        <p className="ins-sub num">
          {[
            track.key_canon,
            track.bpm ? `${Math.round(track.bpm)} bpm` : null,
            track.duration_label,
            track.date_label,
          ]
            .filter(Boolean)
            .join("  ·  ")}
        </p>

        <div className="triage-actions">
          <button className="primary" onClick={() => player.play(queue, cursor)}>
            Play
          </button>
          <button className="triage-verb" onClick={() => verdict("gem")}>Gem</button>
          <button className="triage-verb" onClick={() => verdict("keep")}>Keep</button>
          <button className="triage-verb" onClick={() => verdict("archive")}>Archive</button>
          <button className="triage-verb" onClick={() => setCursor((c) => c + 1)}>Skip</button>
        </div>
      </div>
    </div>
  );
}
