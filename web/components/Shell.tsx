"use client";

import Link from "next/link";
import {usePathname} from "next/navigation";

import {Inspector} from "./Inspector";
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";

import {usePlayer, usePlayerTimeline} from "./PlayerProvider";
import {IconMore, IconNext, IconPause, IconPlay, IconPrev} from "./Icons";
import {QueueSheet} from "./QueueSheet";
import {Waveform} from "./Waveform";
import {applyEraCssVars, colorsByName, getEras} from "@/lib/eras";

function clock(seconds: number) {
  if (!seconds || Number.isNaN(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

const NAV = [
  {href: "/", label: "Library", subtitle: "everything, filtered your way", group: "listening", adminOnly: false},
  {href: "/dig", label: "Dig", subtitle: "what the crate forgot", group: "listening", adminOnly: false},
  {href: "/for-you", label: "For You", subtitle: "tracks sent to you", group: "listening", adminOnly: false},
  {href: "/upload", label: "Upload", subtitle: "add new bounces", group: "maker", adminOnly: false},
  {href: "/collections", label: "Collections", subtitle: "sets worth keeping", group: "listening", adminOnly: false},
  {href: "/triage", label: "Triage", subtitle: "name the unnamed", group: "maker", adminOnly: false},
  {href: "/activity", label: "Activity", subtitle: "who's listening now", group: "listening", adminOnly: false},
  {href: "/admin", label: "Admin", subtitle: "invites and links", group: "maker", adminOnly: true},
] as const;

// Reached before you have an account, so there is no library to frame and no
// player to dock. Rendering the chrome here would just be furniture around a
// form you cannot use yet.
const BARE = ["/join"];

export type PresenceListener = {
  actor: string;
  bounce_ulid: string;
  song_ulid: string;
  title: string;
  key_canon: string | null;
  bpm: number | null;
  era: string;
  era_css: string;
  seen_s_ago: number;
};

type PresenceValue = {listeners: PresenceListener[]; actor: string};

const PresenceContext = createContext<PresenceValue>({listeners: [], actor: ""});

export function usePresenceContext() {
  return useContext(PresenceContext);
}

// Shell survives route changes, so this hook is the one owner of the presence
// interval. The rail reads it directly and Activity reads the same value from
// context; mounting Activity never creates a second poller.
function usePresence(enabled: boolean) {
  const [listeners, setListeners] = useState<PresenceListener[]>([]);

  useEffect(() => {
    if (!enabled) {
      setListeners([]);
      return;
    }

    let live = true;
    let timer: ReturnType<typeof setInterval> | undefined;
    const load = () =>
      fetch("/api/presence", {credentials: "same-origin"})
        .then((response) => (
          response.ok ? response.json() : Promise.reject(response.status)
        ))
        .then((data) => {
          if (live) {
            setListeners(Array.isArray(data.listeners) ? data.listeners : []);
          }
        })
        .catch(() => {
          if (live) setListeners([]);
        });
    const stop = () => {
      if (timer === undefined) return;
      clearInterval(timer);
      timer = undefined;
    };
    const start = () => {
      if (document.visibilityState === "hidden" || timer !== undefined) return;
      void load();
      // 10s, not 15: a skip's start-post lands within a second, and the
      // worst-case gap between the row changing and the rail showing it is
      // one poll. Visible tabs only, so the cost stays bounded.
      timer = setInterval(() => void load(), 10_000);
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
  }, [enabled]);

  return listeners;
}

export function Shell({children}: {children: React.ReactNode}) {
  const {
    queue, index, current, playing, repeat,
    toggle, next, prev, seek, cycleRepeat, jumpTo, move, removeAt,
  } = usePlayer();
  const {position, duration} = usePlayerTimeline();
  const pathname = usePathname();
  const [eras, setEras] = useState<Record<string, string>>({});
  const [isAdmin, setIsAdmin] = useState(false);
  const [actor, setActor] = useState("");
  const [homework, setHomework] = useState(0);
  const [sheet, setSheet] = useState(false);
  const [queueOpen, setQueueOpen] = useState(false);

  const bare = BARE.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  const listeners = usePresence(!bare);
  const presence = useMemo(() => ({listeners, actor}), [listeners, actor]);

  const closeQueue = useCallback(() => setQueueOpen(false), []);
  const openQueue = useCallback(() => {
    setSheet(false);
    setQueueOpen(true);
  }, []);

  useEffect(() => {
    if (bare) return;
    getEras()
      .then((eras) => {
        applyEraCssVars(eras);
        setEras(colorsByName(eras));
      })
      .catch(() => undefined);
    fetch("/api/me", {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((me) => {
        setIsAdmin(Boolean(me.is_admin));
        setActor(String(me.username ?? ""));
      })
      .catch(() => {
        setIsAdmin(false);
        setActor("");
      });
  }, [bare]);

  // The badge is the whole point of homework: you should not have to go looking
  // for it. Cheap count endpoint, polled, plus an immediate refresh whenever the
  // tab comes back to the front.
  useEffect(() => {
    if (bare) return;
    let live = true;
    const read = () =>
      fetch("/api/assignments/count", {credentials: "same-origin"})
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((data) => live && setHomework(Number(data.pending ?? 0)))
        .catch(() => undefined);
    read();
    const timer = setInterval(read, 60_000);
    window.addEventListener("focus", read);
    return () => {
      live = false;
      clearInterval(timer);
      window.removeEventListener("focus", read);
    };
  }, [bare, pathname]);

  useEffect(() => {
    if (bare) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey ||
        target?.isContentEditable ||
        (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
      ) return;
      if (event.key.toLowerCase() === "q") openQueue();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [bare, openQueue]);

  if (bare) {
    return <div className="bare">{children}</div>;
  }

  return (
    <PresenceContext.Provider value={presence}>
      <div className="shell">
      <aside className="rail">
        <div className="rail-head">cr8</div>
        <nav className="rail-nav">
          {(["listening", "maker"] as const).map((group) => (
            <div
              key={group}
              className={`rail-nav-group rail-nav-group-${group}${group === "maker" && !isAdmin ? " is-quiet" : ""}`}
            >
              {NAV.filter((item) => (
                item.group === group && (!item.adminOnly || isAdmin)
              )).map((item) => (
                <Link
                  key={item.href}
                  className={`rail-item${pathname === item.href ? " is-current" : ""}`}
                  href={item.href}
                >
                  <span className="rail-copy">
                    <span className="rail-label">{item.label}</span>
                    <span className="rail-subtitle">{item.subtitle}</span>
                  </span>
                  {item.href === "/for-you" && homework > 0 ? (
                    <span className="rail-badge num">{homework}</span>
                  ) : null}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="rail-bottom">
          {listeners.length ? (
            <section className="rail-presence" aria-label="Listening now">
              <div className="rail-presence-label">LISTENING NOW</div>
              {listeners.map((listener) => {
                const isSelf = listener.actor === actor;
                return (
                  <Link
                    key={listener.actor}
                    className={`rail-presence-row${isSelf ? " is-self" : ""}`}
                    href={`/songs/${listener.song_ulid}`}
                    aria-label={`${isSelf ? "You are" : `${listener.actor} is`} listening to ${listener.title}`}
                  >
                    <span
                      className={`presence-dot era-${listener.era_css}`}
                      aria-hidden="true"
                    />
                    <span className="rail-presence-copy">
                      <span className="rail-presence-who">
                        {isSelf ? "you" : listener.actor}
                      </span>
                      <span className="rail-presence-track">
                        {listener.title}
                      </span>
                    </span>
                  </Link>
                );
              })}
            </section>
          ) : null}
          <div className="rail-foot">Working : Scratch</div>
        </div>
      </aside>

      <main className="main">{children}</main>

      {/* One inspector. On a phone the same panel slides up over the library
          instead of sitting beside it, because there is no beside. */}
      <div className={`ins-shell${sheet ? " is-open" : ""}`}>
        {sheet ? (
          <button
            className="sheet-scrim"
            aria-label="Close"
            onClick={() => setSheet(false)}
          />
        ) : null}
        <Inspector track={current} onClose={() => setSheet(false)} />
      </div>

      <QueueSheet
        open={queueOpen}
        queue={queue}
        index={index}
        current={current}
        repeat={repeat}
        eras={eras}
        onClose={closeQueue}
        cycleRepeat={cycleRepeat}
        jumpTo={jumpTo}
        move={move}
        removeAt={removeAt}
      />

      <footer
        className="dock"
        onClick={(event) => {
          const target = event.target as HTMLElement;
          if (target.closest("button,a,.dock-scrub")) return;
          openQueue();
        }}
      >
        {current ? (
          <>
            <Link key={`art-${current.bounce_ulid}`} href={`/songs/${current.song_ulid}`} aria-label={`Open ${current.title}`}>
              <img className="dock-art" src={`/art/${current.bounce_ulid}`} alt="" />
            </Link>
            <div className="dock-meta" key={`meta-${current.bounce_ulid}`}>
              <Link className="dock-title" href={`/songs/${current.song_ulid}`}>
                {current.title}
              </Link>
              <div className="dock-sub num">
                {[current.key_canon, current.bpm ? `${Math.round(current.bpm)} bpm` : null, current.date_label]
                  .filter(Boolean)
                  .join("  ·  ")}
              </div>
            </div>
            <div className="dock-transport">
              <button className="tbtn" onClick={prev} aria-label="Previous" title="Previous">
                <IconPrev />
              </button>
              <button className="tbtn tbtn-main" onClick={toggle}
                      aria-label={playing ? "Pause" : "Play"}
                      title={playing ? "Pause" : "Play"}>
                {playing ? <IconPause /> : <IconPlay />}
              </button>
              <button className="tbtn" onClick={next} aria-label="Next" title="Next">
                <IconNext />
              </button>
              {/* Tagging is the point of the app, so it cannot be desktop-only.
                  On a phone this is how you reach the panel. */}
              <button
                className="tbtn tbtn-sheet"
                onClick={() => {
                  setQueueOpen(false);
                  setSheet(true);
                }}
                aria-label="Tag this track"
                title="Tag this track"
              >
                <IconMore />
              </button>
            </div>
            <div className="dock-scrub">
              <span className="num dock-time">{clock(position)}</span>
              <Waveform
                bounceUlid={current.bounce_ulid}
                progress={duration ? position / duration : 0}
                onSeek={seek}
                accent={current.era ? eras[current.era] : undefined}
              />
              <span className="num dock-time">{clock(duration)}</span>
            </div>
          </>
        ) : (
          <div className="dock-empty">Press play anywhere. Tag it while it runs.</div>
        )}
      </footer>

      {/* The same destinations as the rail, put where a thumb can reach them.
          Hidden on desktop; the rail is hidden here. */}
      <nav className="tabs" aria-label="Sections">
        {NAV.filter((item) => !item.adminOnly || isAdmin).map((item) => (
          <Link
            key={item.href}
            className={`tab${pathname === item.href ? " is-current" : ""}`}
            href={item.href}
          >
            <span className="tab-label">{item.label}</span>
            {item.href === "/for-you" && homework > 0 ? (
              <span className="tab-badge num">{homework}</span>
            ) : null}
          </Link>
        ))}
      </nav>
      </div>
    </PresenceContext.Provider>
  );
}
