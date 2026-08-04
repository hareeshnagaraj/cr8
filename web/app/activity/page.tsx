"use client";

import Link from "next/link";
import {useEffect, useState} from "react";

import {
  usePresenceContext, type PresenceListener,
} from "@/components/Shell";

type Event = {
  actor?: string;
  kind?: string;
  dim?: string;
  value?: string;
  title?: string;
  created_at?: string;
  song_ulid?: string;
};

function actorName(actor?: string) {
  if (!actor) return "someone";
  // Actors carry provenance suffixes like "hareesh:audit:add"; the feed wants
  // the person, not the bookkeeping.
  return actor.split(":")[0];
}

type PresenceGroup = PresenceListener & {actors: string[]};

function groupedPresence(listeners: PresenceListener[]) {
  const groups = new Map<string, PresenceGroup>();
  listeners.forEach((listener) => {
    const current = groups.get(listener.bounce_ulid);
    if (current) {
      current.actors.push(listener.actor);
      current.seen_s_ago = Math.min(current.seen_s_ago, listener.seen_s_ago);
    } else {
      groups.set(listener.bounce_ulid, {
        ...listener,
        actors: [listener.actor],
      });
    }
  });
  return Array.from(groups.values());
}

function freshness(seconds: number) {
  return seconds < 10 ? "just now" : `${seconds}s ago`;
}

export default function Activity() {
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const {listeners} = usePresenceContext();
  const live = groupedPresence(listeners);

  // The feed refreshes itself while you sit on the tab — leaving and coming
  // back was the only refresh before. Same lifecycle as the presence poller:
  // visible-only, one interval, cleaned up on unmount.
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setInterval> | undefined;
    const load = () =>
      fetch("/api/activity?limit=80", {credentials: "same-origin"})
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((data: Event[]) => {
          if (live) setEvents(data);
        })
        .catch(() => {
          if (live) setEvents((prev) => prev);
        })
        .finally(() => {
          if (live) setLoading(false);
        });
    const stop = () => {
      if (timer === undefined) return;
      clearInterval(timer);
      timer = undefined;
    };
    const start = () => {
      if (document.visibilityState === "hidden" || timer !== undefined) return;
      void load();
      timer = setInterval(() => void load(), 12_000);
    };
    const visibilityChanged = () => {
      stop();
      if (document.visibilityState === "visible") start();
    };

    start();
    document.addEventListener("visibilitychange", visibilityChanged);
    return () => {
      live = false;
      stop();
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, []);

  return (
    <div className="lib page-scroll">
      <header className="lib-head">
        <div>
          <h1 className="lib-title">Activity</h1>
          <p className="lib-count num">
            {loading ? "Loading" : `${events.length} recent`}
          </p>
        </div>
      </header>

      {live.length ? (
        <section className="activity-presence" aria-labelledby="listening-now">
          <h2 className="activity-presence-label" id="listening-now">
            Listening now
          </h2>
          <ul className="activity-presence-list">
            {live.map((listener) => {
              const meta = [
                listener.key_canon,
                listener.bpm ? `${Math.round(listener.bpm)} bpm` : null,
              ].filter(Boolean).join(" · ");
              return (
                <li key={listener.bounce_ulid}>
                  <Link
                    className="activity-presence-row"
                    href={`/songs/${listener.song_ulid}`}
                  >
                    <span
                      className={`presence-dot era-${listener.era_css}`}
                      aria-hidden="true"
                    />
                    <span className="activity-presence-main">
                      <span className="activity-presence-who">
                        {listener.actors.join(" + ")}
                      </span>
                      <span className="activity-presence-separator"> · </span>
                      <span className="activity-presence-track">
                        {listener.title}
                      </span>
                    </span>
                    <span className="activity-presence-meta num">{meta}</span>
                    <span className="activity-presence-when num">
                      {freshness(listener.seen_s_ago)}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {!loading && !events.length ? (
        <p className="empty">Nothing yet. Tag something.</p>
      ) : null}

      <ul className="feed">
        {events.map((event, i) => (
          <li className="feed-row" key={i}>
            <span className="feed-who">{actorName(event.actor)}</span>
            <span className="feed-what">
              {event.dim && event.value ? (
                <>
                  tagged <b>{event.value}</b>
                  <span className="dim"> · {event.dim}</span>
                </>
              ) : (
                event.kind ?? "did something"
              )}
            </span>
            <span className="feed-song">{event.title ?? ""}</span>
            <span className="feed-when num dim">{event.created_at?.slice(0, 16) ?? ""}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
