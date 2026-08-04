"use client";

import {
  createContext, useCallback, useContext, useEffect, useMemo, useReducer,
  useRef, useState,
} from "react";

export type Track = {
  bounce_ulid: string;
  song_ulid: string;
  title: string;
  key_canon?: string | null;
  key_camelot?: string | null;
  bpm?: number | null;
  duration_s?: number | null;
  duration_label?: string | null;
  era?: string | null;
  date_label?: string | null;
  version_count?: number | null;
  version_label?: string | null;
  versions?: Track[] | null;
  status?: string | null;
  keeper?: number | null;
  unheard?: number | null;
};

export type PlayerState = {
  queue: Track[];
  index: number;
  current: Track | null;
  playing: boolean;
  cued: boolean;
  repeat: "off" | "all";
  getAnalyser: () => Promise<AnalyserNode | null>;
  play: (queue: Track[], index: number) => void;
  cue: (queue: Track[], index: number) => void;
  enqueue: (tracks: Track[]) => void;
  playNext: (tracks: Track[]) => void;
  removeAt: (index: number) => void;
  move: (from: number, to: number) => void;
  jumpTo: (index: number) => void;
  cycleRepeat: () => void;
  toggle: () => void;
  next: () => void;
  prev: () => void;
  seek: (fraction: number) => void;
};

type PlayerTimeline = {
  position: number;
  duration: number;
};

const Ctx = createContext<PlayerState | null>(null);
const TimelineCtx = createContext<PlayerTimeline | null>(null);

// Queue and index live in ONE reducer so every mutation computes from the
// true current state. As separate useStates mutated from callback closures,
// three same-tick removes shrank the queue by one — each handler worked from
// its own render's snapshot, operations silently vanished under rapid input,
// and the index math against stale bases is what "it got stuck" feels like.
// `replay` bumps when a step lands on the same track (repeat-all with one
// song) so the source effect knows to restart playback. `cued` keeps selecting
// a track atomic too, while withholding audio work until a play intent.
type QueueSnapshot = {queue: Track[]; index: number; replay: number; cued: boolean};

type QueueAction =
  | {type: "set"; queue: Track[]; index: number}
  | {type: "cue"; queue: Track[]; index: number}
  | {type: "requestPlay"}
  | {type: "enqueue"; tracks: Track[]}
  | {type: "playNext"; tracks: Track[]}
  | {type: "removeAt"; at: number}
  | {type: "move"; from: number; to: number}
  | {type: "jumpTo"; at: number}
  | {type: "step"; direction: 1 | -1; repeat: "off" | "all"};

function queueReducer(
  state: QueueSnapshot, action: QueueAction,
): QueueSnapshot {
  const {queue, index} = state;
  switch (action.type) {
    case "set":
      return {
        queue: action.queue, index: action.index, replay: state.replay, cued: false,
      };
    case "cue":
      return {
        queue: action.queue, index: action.index, replay: state.replay, cued: true,
      };
    case "requestPlay":
      if (!state.cued || index < 0 || index >= queue.length) return state;
      return {...state, cued: false};
    case "enqueue": {
      if (!action.tracks.length) return state;
      return {
        ...state,
        queue: [...queue, ...action.tracks],
        index: index < 0 ? queue.length : index,
      };
    }
    case "playNext": {
      if (!action.tracks.length) return state;
      const at = Math.max(0, Math.min(index + 1, queue.length));
      return {
        ...state,
        queue: [...queue.slice(0, at), ...action.tracks, ...queue.slice(at)],
        index,
      };
    }
    case "removeAt": {
      const {at} = action;
      if (!Number.isInteger(at) || at < 0 || at >= queue.length) return state;
      const shrunk = [...queue.slice(0, at), ...queue.slice(at + 1)];
      if (at < index) return {...state, queue: shrunk, index: index - 1};
      if (at !== index) return {...state, queue: shrunk};
      // Removing the current track: the next slides into its slot, or the
      // queue ends and the source effect stops the audio.
      if (at < shrunk.length) return {...state, queue: shrunk, index: at};
      return {...state, queue: shrunk, index: -1, cued: false};
    }
    case "move": {
      const {from, to} = action;
      if (
        !Number.isInteger(from) || !Number.isInteger(to) ||
        from < 0 || to < 0 || from >= queue.length || to >= queue.length ||
        from === to
      ) return state;
      const reordered = queue.slice();
      const [track] = reordered.splice(from, 1);
      reordered.splice(to, 0, track);
      let landed = index;
      if (index === from) landed = to;
      else if (from < index && to >= index) landed = index - 1;
      else if (from > index && to <= index) landed = index + 1;
      return {...state, queue: reordered, index: landed};
    }
    case "jumpTo":
      if (!Number.isInteger(action.at) || action.at < 0 || action.at >= queue.length) {
        return state;
      }
      return {...state, index: action.at, cued: false};
    case "step": {
      if (state.cued) return {...state, cued: false};
      if (index < 0) return state;
      if (action.direction === 1) {
        if (index + 1 < queue.length) return {...state, index: index + 1, cued: false};
        if (action.repeat !== "all" || !queue.length) return state;
        if (queue.length > 1) return {...state, index: 0, cued: false};
        return {...state, replay: state.replay + 1, cued: false};
      }
      if (index > 0) return {...state, index: index - 1, cued: false};
      if (action.repeat === "all" && queue.length > 1) {
        return {...state, index: queue.length - 1, cued: false};
      }
      if (action.repeat === "all" && queue.length === 1) {
        return {...state, replay: state.replay + 1, cued: false};
      }
      return state;
    }
  }
}

// A 1-sample silent wav. Playing it inside the first real user gesture is what
// buys the permission to start audio later, after an await has already thrown
// the gesture away. Without this, the first click appears to do nothing.
const SILENT =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";

export function PlayerProvider({children}: {children: React.ReactNode}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const analyserAttempted = useRef(false);
  const audioPlayingRef = useRef(false);
  const unlocked = useRef(false);
  const [snapshot, dispatchQueue] = useReducer(queueReducer, {
    queue: [], index: -1, replay: 0, cued: false,
  });
  const {queue, index} = snapshot;
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [repeat, setRepeat] = useState<"off" | "all">("off");
  // The ended-event listener registers once; it reads repeat through this ref
  // rather than re-subscribing every time the mode flips.
  const repeatRef = useRef(repeat);
  useEffect(() => {
    repeatRef.current = repeat;
  }, [repeat]);
  const replaySeen = useRef(0);

  if (typeof window !== "undefined" && !audioRef.current) {
    audioRef.current = new Audio();
    audioRef.current.preload = "auto";
  }

  const current = index >= 0 ? queue[index] ?? null : null;

  // A media element can only be claimed by createMediaElementSource once. Wait
  // until the spectrum actually asks for it, and resume the context before the
  // permanent tap so an autoplay restriction can never turn a visual into
  // silence. Any failure leaves playback alone or restores a direct path to the
  // destination before returning null.
  const getAnalyser = useCallback(async (): Promise<AnalyserNode | null> => {
    if (analyserRef.current) return analyserRef.current;
    if (analyserAttempted.current) return null;
    analyserAttempted.current = true;

    const audio = audioRef.current;
    if (!audio || typeof window === "undefined" || !window.AudioContext) return null;

    let context: AudioContext | null = null;
    let source: MediaElementAudioSourceNode | null = null;
    try {
      context = new window.AudioContext();
      audioContextRef.current = context;
      if (context.state === "suspended") await context.resume();
      if (context.state !== "running" || !audioPlayingRef.current) {
        audioContextRef.current = null;
        await context.close().catch(() => undefined);
        return null;
      }

      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.82;
      // Connect the destination first. If claiming or connecting the element
      // throws, the catch path below either leaves native playback untouched or
      // reconnects the claimed source directly.
      analyser.connect(context.destination);
      source = context.createMediaElementSource(audio);
      source.connect(analyser);
      analyserRef.current = analyser;
      return analyser;
    } catch {
      if (source && context) {
        try {
          source.disconnect();
          source.connect(context.destination);
        } catch {
          // There is no further safe visual recovery. Never rethrow into audio
          // playback or the component tree.
        }
      } else if (context) {
        audioContextRef.current = null;
        await context.close().catch(() => undefined);
      }
      return null;
    }
  }, []);

  useEffect(() => {
    const arm = () => {
      const audio = audioRef.current;
      if (!audio || unlocked.current) return;
      unlocked.current = true;
      const src = audio.src;
      audio.src = SILENT;
      const done = audio.play();
      if (done) done.then(() => {
        audio.pause();
        if (src) audio.src = src;
      }, () => undefined);
    };
    const names = ["pointerdown", "keydown", "touchstart"] as const;
    for (const name of names) {
      document.addEventListener(name, arm, {once: true, capture: true});
    }
    return () => {
      for (const name of names) {
        document.removeEventListener(name, arm, {capture: true});
      }
    };
  }, []);

  const next = useCallback(() => {
    dispatchQueue({type: "step", direction: 1, repeat: repeatRef.current});
  }, []);

  const prev = useCallback(() => {
    const audio = audioRef.current;
    if (audio && audio.currentTime > 3) {
      audio.currentTime = 0;
      setPosition(0);
      return;
    }
    dispatchQueue({type: "step", direction: -1, repeat: repeatRef.current});
  }, []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    // High-frequency timeupdate while A2HS-hidden still drives TimelineCtx and
    // the dock scrub. Skip position churn when hidden; snap once on return.
    const syncTimeline = () => {
      setPosition(audio.currentTime);
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        setDuration(audio.duration);
      }
    };
    const onTime = () => {
      if (document.hidden) return;
      setPosition(audio.currentTime);
    };
    const onMeta = () => setDuration(audio.duration || 0);
    const onEnd = () => next();
    const settleContext = (shouldPlay: boolean) => {
      const context = audioContextRef.current;
      if (!context) return;
      const transition = shouldPlay ? context.resume() : context.suspend();
      transition.then(() => {
        // Pause and play can cross while an AudioContext transition is still
        // pending during a track change. Reconcile once more with the newest
        // audio state so a late suspend can never mute a newly playing track.
        if (audioPlayingRef.current && context.state === "suspended") {
          context.resume().catch(() => undefined);
        } else if (!audioPlayingRef.current && context.state === "running") {
          context.suspend().catch(() => undefined);
        }
      }, () => undefined);
    };
    const onPlay = () => {
      audioPlayingRef.current = true;
      setPlaying(true);
      settleContext(true);
    };
    const onPause = () => {
      audioPlayingRef.current = false;
      setPlaying(false);
      settleContext(false);
    };
    const resumeContextIfPlaying = () => {
      if (!audioPlayingRef.current) return;
      audioContextRef.current?.resume().catch(() => undefined);
    };
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      syncTimeline();
      resumeContextIfPlaying();
    };
    const onPageshow = () => {
      syncTimeline();
      resumeContextIfPlaying();
    };
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("loadedmetadata", onMeta);
    audio.addEventListener("ended", onEnd);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", onPageshow);
    return () => {
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("loadedmetadata", onMeta);
      audio.removeEventListener("ended", onEnd);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", onPageshow);
    };
  }, [next]);

  // Report what actually got listened to. Without this "unheard", the dig
  // queue, and anything someone put on your plate never learn that you played
  // it — the whole port ran this way and recorded nothing.
  // Interval runs only while a track is present, audio is playing, and the
  // document is visible. Audio itself keeps going when hidden (pocket /
  // MediaSession); we stop the 1s timer and reconcile pocket seconds from
  // audio.currentTime when the tab returns.
  const heard = useRef({ulid: "", seconds: 0, sent: 0});
  const heardHiddenAt = useRef<number | null>(null);
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !current?.bounce_ulid) return;

    const post = (ulid: string, seconds: number, started = false) => {
      if (!ulid) return;
      const body = new URLSearchParams({
        state: seconds >= 30 ? "heard" : "unheard",
        heard_s: String(Math.round(seconds)),
        started: started ? "true" : "false",
      });
      fetch(`/progress/${ulid}`, {
        method: "POST",
        credentials: "same-origin",
        headers: {"X-CR8-Request": "1"},
        body,
      }).catch(() => undefined);
    };

    const tick = () => {
      const ulid = current.bounce_ulid;
      if (!ulid || audio.paused) return;
      if (heard.current.ulid !== ulid) {
        // Flush what the previous track earned before switching away.
        if (heard.current.ulid && heard.current.seconds > 0) {
          post(heard.current.ulid, heard.current.seconds);
        }
        heard.current = {ulid, seconds: 0, sent: 0};
        post(ulid, 0, true);
      }
      heard.current.seconds += 1;
      // Cheap and steady rather than chatty: every 15s of real listening, and
      // that cadence crosses the "this was a real listen" mark on its own.
      if (heard.current.seconds - heard.current.sent >= 15) {
        heard.current.sent = heard.current.seconds;
        post(ulid, heard.current.seconds);
      }
    };

    let timer: ReturnType<typeof setInterval> | undefined;
    const flush = () => {
      if (heard.current.ulid && heard.current.seconds > heard.current.sent) {
        post(heard.current.ulid, heard.current.seconds);
        heard.current.sent = heard.current.seconds;
      }
    };
    const stop = () => {
      if (timer === undefined) return;
      clearInterval(timer);
      timer = undefined;
    };
    const start = () => {
      if (
        document.visibilityState !== "visible" ||
        audio.paused ||
        timer !== undefined
      ) return;
      timer = setInterval(tick, 1000);
    };
    const reconcileHidden = () => {
      const snap = heardHiddenAt.current;
      heardHiddenAt.current = null;
      if (snap === null) return;
      // currentTime delta, not wall clock — paused stretches invent nothing.
      const gained = Math.min(Math.max(0, audio.currentTime - snap), 3600);
      if (gained < 0.5) return;
      heard.current.seconds += gained;
    };
    const sync = () => {
      stop();
      if (document.visibilityState === "hidden") {
        if (audio.paused) {
          // Freeze the pocket stretch now so a later seek/play starts clean.
          reconcileHidden();
        } else if (heardHiddenAt.current === null) {
          // Play while already hidden (lock screen / MediaSession) never
          // sees a hide transition — snap here or pocket seconds are lost.
          heardHiddenAt.current = audio.currentTime;
        }
        return;
      }
      if (!audio.paused) start();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        reconcileHidden();
        flush();
        sync();
      } else {
        stop();
        heardHiddenAt.current = audio.paused ? null : audio.currentTime;
        flush();
      }
    };
    const onPageshow = () => {
      if (document.visibilityState !== "visible") return;
      reconcileHidden();
      flush();
      sync();
    };

    if (document.visibilityState === "hidden" && !audio.paused) {
      heardHiddenAt.current = audio.currentTime;
    } else {
      start();
    }
    audio.addEventListener("play", sync);
    audio.addEventListener("pause", sync);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pageshow", onPageshow);
    window.addEventListener("pagehide", flush);
    return () => {
      stop();
      // Reconcile before teardown — nulling first drops pocket seconds on
      // track change / effect re-run while still backgrounded.
      reconcileHidden();
      audio.removeEventListener("play", sync);
      audio.removeEventListener("pause", sync);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pageshow", onPageshow);
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [current?.bounce_ulid]);

  // On a phone the app is usually in your pocket. Without this the lock screen
  // shows nothing and the only way to skip a track is to unlock and find the
  // tab; with it the track, its art and the transport are on the lock screen
  // and the headphone buttons work.
  useEffect(() => {
    if (typeof navigator === "undefined" || !("mediaSession" in navigator)) return;
    const session = navigator.mediaSession;
    if (!current) {
      session.metadata = null;
      session.playbackState = "none";
      return;
    }
    session.metadata = new MediaMetadata({
      title: current.title,
      artist: current.key_canon
        ? `${current.key_canon}${current.bpm ? ` · ${Math.round(current.bpm)} bpm` : ""}`
        : "cr8",
      album: current.era ?? "Working : Scratch",
      artwork: [
        {src: `/art/${current.bounce_ulid}`, sizes: "512x512", type: "image/jpeg"},
      ],
    });
    session.playbackState = playing ? "playing" : "paused";
    const handlers: [MediaSessionAction, () => void][] = [
      ["play", () => audioRef.current?.play().catch(() => undefined)],
      ["pause", () => audioRef.current?.pause()],
      ["nexttrack", next],
      ["previoustrack", prev],
    ];
    for (const [action, handler] of handlers) {
      try {
        session.setActionHandler(action, handler);
      } catch {
        // Safari refuses actions it does not implement; that is not an error.
      }
    }
    return () => {
      for (const [action] of handlers) {
        try {
          session.setActionHandler(action, null);
        } catch {
          /* nothing to undo */
        }
      }
    };
  }, [current, next, playing, prev]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!current) {
      // The queue emptied under the player (last track removed): stop cleanly.
      audio.pause();
      audio.removeAttribute("src");
      setPosition(0);
      setDuration(0);
      return;
    }
    if (snapshot.cued) {
      // A cue can render the dock and queue, but owns no audio resource yet.
      // The first explicit transport intent below changes reducer state and
      // lets this effect assign the real source on its next pass.
      audio.pause();
      audio.removeAttribute("src");
      setPosition(0);
      setDuration(0);
      return;
    }
    const url = `/m/${current.bounce_ulid}`;
    if (audio.src.endsWith(url)) {
      // Same track with a NEW replay value: repeat-all with a single song
      // wraps onto itself and restarts here. Comparing against the last seen
      // value keeps unrelated queue edits from restarting playback.
      if (snapshot.replay !== replaySeen.current) {
        replaySeen.current = snapshot.replay;
        audio.currentTime = 0;
        setPosition(0);
        const again = audio.play();
        if (again) again.catch(() => undefined);
      }
      return;
    }
    replaySeen.current = snapshot.replay;
    audio.src = url;
    const done = audio.play();
    if (done) done.catch(() => undefined);

    // Warm the next track's first chunk so switching is instant — but only
    // AFTER this track is audibly rolling. Prefetching at press-play made a
    // 5 Mbit connection take ~5s to start: half a megabyte of "later"
    // competed with the buffer for "now". The play-probe gate caught it.
    const upcoming = queue[index + 1];
    if (upcoming) {
      const warmAbort = new AbortController();
      const warm = window.setTimeout(() => {
        fetch(`/m/${upcoming.bounce_ulid}`, {
          headers: {Range: "bytes=0-524287"},
          credentials: "same-origin",
          priority: "low",
          signal: warmAbort.signal,
        }).catch(() => undefined);
      }, 4000);
      return () => {
        window.clearTimeout(warm);
        warmAbort.abort();
      };
    }
  }, [current, index, queue, snapshot.cued, snapshot.replay]);

  const play = useCallback((next: Track[], at: number) => {
    dispatchQueue({type: "set", queue: next, index: at});
  }, []);

  const cue = useCallback((next: Track[], at: number) => {
    dispatchQueue({type: "cue", queue: next, index: at});
  }, []);

  const enqueue = useCallback((tracks: Track[]) => {
    dispatchQueue({type: "enqueue", tracks});
  }, []);

  const playNext = useCallback((tracks: Track[]) => {
    dispatchQueue({type: "playNext", tracks});
  }, []);

  const removeAt = useCallback((at: number) => {
    dispatchQueue({type: "removeAt", at});
  }, []);

  const move = useCallback((from: number, to: number) => {
    dispatchQueue({type: "move", from, to});
  }, []);

  const jumpTo = useCallback((at: number) => {
    dispatchQueue({type: "jumpTo", at});
  }, []);

  const cycleRepeat = useCallback(() => {
    setRepeat((value) => (value === "off" ? "all" : "off"));
  }, []);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !current) return;
    if (snapshot.cued) {
      dispatchQueue({type: "requestPlay"});
      return;
    }
    if (audio.paused) {
      const done = audio.play();
      if (done) done.catch(() => undefined);
    } else {
      audio.pause();
    }
  }, [current, snapshot.cued]);

  const seek = useCallback((fraction: number) => {
    const audio = audioRef.current;
    if (audio && audio.duration) audio.currentTime = audio.duration * fraction;
  }, []);

  const value = useMemo<PlayerState>(
    () => ({
      queue, index, current, playing, cued: snapshot.cued, repeat, getAnalyser,
      play, cue, enqueue, playNext, removeAt, move, jumpTo, cycleRepeat,
      toggle, next, prev, seek,
    }),
    [
      queue, index, current, playing, snapshot.cued, repeat, getAnalyser,
      play, cue, enqueue, playNext, removeAt, move, jumpTo, cycleRepeat,
      toggle, next, prev, seek,
    ],
  );
  const timeline = useMemo<PlayerTimeline>(
    () => ({position, duration}),
    [position, duration],
  );

  return (
    <Ctx.Provider value={value}>
      <TimelineCtx.Provider value={timeline}>{children}</TimelineCtx.Provider>
    </Ctx.Provider>
  );
}

export function usePlayer() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePlayer outside PlayerProvider");
  return ctx;
}

export function usePlayerTimeline() {
  const timeline = useContext(TimelineCtx);
  if (!timeline) throw new Error("usePlayerTimeline outside PlayerProvider");
  return timeline;
}
