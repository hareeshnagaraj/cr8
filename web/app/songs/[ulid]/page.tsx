"use client";

import {use, useCallback, useEffect, useRef, useState} from "react";
import Link from "next/link";
import {usePlayer, usePlayerTimeline, type Track} from "@/components/PlayerProvider";
import {Waveform} from "@/components/Waveform";
import {LiveSpectrum} from "@/components/LiveSpectrum";
import {Inspector} from "@/components/Inspector";
import {DownloadMenu} from "@/components/DownloadMenu";
import {SendToDialog} from "@/components/SendToDialog";
import {ShareDialog} from "@/components/ShareDialog";
import {Menu} from "@/components/Menu";
import {
  IconClock,
  IconLayers,
  IconPause,
  IconPlay,
  IconShare,
  IconTag,
} from "@/components/Icons";

// What this page is for.
//
// It used to be a header, a version list and a stems button, with every piece
// of actual information about the track hidden in a sheet of sixty tag chips.
// That is a data-entry form, not a page about a song - it tells you nothing
// you would want to know while listening.
//
// So the waveform is the page now, and notes hang off it by timecode. The
// catalogue has stored a timecode on every note since the schema was written
// and no screen has ever shown one, which meant "the drums drop out at 2:14"
// had nowhere to live. Tagging still exists, in full, behind Edit tags - it
// is a thing you do occasionally, not the reason you opened the track.

type Stem = {
  bounce_ulid?: string;
  stem_kind?: string;
  title?: string;
  duration_label?: string;
};

type Note = {
  id: number;
  actor: string;
  note: string;
  timecode_s: number;
  created_at: string;
};

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export default function SongDetail({params}: {params: Promise<{ulid: string}>}) {
  const {ulid} = use(params);
  const [track, setTrack] = useState<Track | null>(null);
  const [versions, setVersions] = useState<Track[]>([]);
  const [showVersions, setShowVersions] = useState(false);
  const [stems, setStems] = useState<Stem[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [draft, setDraft] = useState("");
  const [pinned, setPinned] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [noteError, setNoteError] = useState(false);
  const [noteHint, setNoteHint] = useState(false);
  const [tagsOpen, setTagsOpen] = useState(false);
  const [sendTo, setSendTo] = useState(false);
  const [sharing, setSharing] = useState(false);
  const composer = useRef<HTMLTextAreaElement | null>(null);
  const welcomeAttempted = useRef(false);
  const player = usePlayer();
  const timeline = usePlayerTimeline();

  useEffect(() => {
    fetch(`/api/library?limit=1000`, {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data: {tracks: Track[]}) => {
        const song = data.tracks.find((t) => t.song_ulid === ulid) ?? null;
        setTrack(song);
        const list = song?.versions ?? [];
        setVersions(
          list.length
            ? list.map((v) => ({...song, ...v}) as Track)
            : song
              ? [song]
              : [],
        );
      })
      .catch(() => setTrack(null));
  }, [ulid]);

  const loadNotes = useCallback((bounceUlid: string) => {
    fetch(`/api/notes/${bounceUlid}`, {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setNotes)
      .catch(() => setNotes([]));
  }, []);

  useEffect(() => {
    if (!track) return;
    fetch(`/api/stems/${track.bounce_ulid}`, {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setStems)
      .catch(() => setStems([]));
    loadNotes(track.bounce_ulid);
  }, [track?.bounce_ulid, loadNotes]);

  useEffect(() => {
    if (welcomeAttempted.current || !track || track.song_ulid !== ulid) return;
    const location = new URL(window.location.href);
    if (location.searchParams.get("welcome") !== "1") return;
    welcomeAttempted.current = true;
    const queue = versions.length ? versions : [track];
    const selected = Math.max(
      0,
      queue.findIndex((version) => version.bounce_ulid === track.bounce_ulid),
    );
    player.play(queue, selected);
    location.searchParams.delete("welcome");
    window.history.replaceState({}, "", location.pathname + location.search + location.hash);
  }, [player, track, ulid, versions]);

  // The track this page is about is not necessarily the one playing, and a
  // note has to be pinned to a time in THIS track or it means nothing.
  const isPlayingThis = player.current?.bounce_ulid === track?.bounce_ulid;
  const position = isPlayingThis ? timeline.position : 0;
  const duration = isPlayingThis && timeline.duration
    ? timeline.duration
    : (track?.duration_s ?? 0);
  const at = pinned ?? position;

  useEffect(() => {
    setNoteHint(false);
  }, [ulid]);

  useEffect(() => {
    if (
      track?.song_ulid !== ulid || !isPlayingThis || !player.playing ||
      position < 30 || noteHint
    ) return;
    try {
      if (
        window.localStorage.getItem("cr8:noted") === "true" ||
        window.localStorage.getItem("cr8:note-hinted") === "true"
      ) return;
      window.localStorage.setItem("cr8:note-hinted", "true");
      setNoteHint(true);
    } catch {
      // Without storage, the at-most-once promise cannot be kept.
    }
  }, [isPlayingThis, noteHint, player.playing, position, track?.song_ulid, ulid]);

  async function submitNote() {
    if (!track || !draft.trim() || sending) return;
    setSending(true);
    setNoteError(false);
    const response = await fetch(`/api/notes/${track.bounce_ulid}`, {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CR8-Request": "1"},
      body: JSON.stringify({note: draft.trim(), timecode_s: at}),
    }).catch(() => undefined);
    setSending(false);
    if (!response || !response.ok) {
      setNoteError(true);
      return;
    }
    try {
      window.localStorage.setItem("cr8:noted", "true");
    } catch {
      // The note still saved; storage only controls this browser hint.
    }
    setNoteHint(false);
    setDraft("");
    setPinned(null);
    loadNotes(track.bounce_ulid);
  }

  if (!track) {
    return (
      <div className="lib page-scroll">
        <p className="empty">Loading…</p>
      </div>
    );
  }

  const facts = [
    track.status,
    track.keeper && track.keeper > 0 ? `keeper ${track.keeper}` : null,
    track.key_canon,
    track.key_camelot,
    track.bpm ? `${Math.round(track.bpm)} bpm` : null,
    track.duration_label,
    track.date_label,
  ].filter(Boolean);

  return (
    <div className="song page-scroll">
      <div className="song-id">
        <img className="song-art" src={`/art/${track.bounce_ulid}`} alt="" />
        <div className="song-id-text">
          {track.era ? <p className="song-era">{track.era}</p> : null}
          <h1 className="song-title">{track.title}</h1>
          <p className="song-facts num">{facts.join("  ·  ")}</p>
        </div>
        <div className="song-id-actions">
          {versions.length > 1 ? (
            <button
              className={`song-pill${showVersions ? " is-on" : ""}`}
              onClick={() => setShowVersions((v) => !v)}
              aria-expanded={showVersions}
            >
              <IconLayers />
              <span className="num">v{versions.length}</span>
            </button>
          ) : null}
        </div>
      </div>

      {showVersions ? (
        <ol className="song-versions">
          {versions.map((version, i) => (
            <li key={version.bounce_ulid}>
              <button
                className={`song-version${
                  version.bounce_ulid === player.current?.bounce_ulid ? " is-playing" : ""
                }`}
                onClick={() => player.play(versions, i)}
              >
                <span className="song-version-dot" aria-hidden="true" />
                <span className="song-version-name">
                  {version.version_label ?? version.title}
                </span>
                <span className="num dim">{version.date_label || "—"}</span>
                <span className="num dim">{version.duration_label || "—"}</span>
              </button>
            </li>
          ))}
        </ol>
      ) : null}

      <div className="song-wave">
        <Waveform
          bounceUlid={track.bounce_ulid}
          progress={duration ? position / duration : 0}
          onSeek={(fraction) => {
            if (!isPlayingThis) player.play(versions.length ? versions : [track], 0);
            player.seek(fraction);
          }}
        />
        <div className="song-wave-marks" aria-hidden="true">
          {duration
            ? notes.map((note) => (
                <span
                  key={note.id}
                  className="song-mark"
                  style={{left: `${Math.min(100, (note.timecode_s / duration) * 100)}%`}}
                />
              ))
            : null}
        </div>
      </div>
      {isPlayingThis && player.playing ? <LiveSpectrum /> : null}

      <div className="song-transport">
        <button
          className="primary"
          aria-label={isPlayingThis && player.playing ? "Pause" : "Play"}
          title={isPlayingThis && player.playing ? "Pause" : "Play"}
          onClick={() =>
            isPlayingThis ? player.toggle() : player.play(versions.length ? versions : [track], 0)
          }
        >
          {isPlayingThis && player.playing ? <IconPause /> : <IconPlay />}
        </button>
        <div className="song-transport-rest">
          <button
            className="triage-verb verb-icon"
            aria-label="Edit tags"
            title="Edit tags"
            onClick={() => setTagsOpen(true)}
          >
            <IconTag />
          </button>
          <DownloadMenu
            track={track}
            stems={stems}
            className="song-download"
            iconOnly
          />
          {/* One share affordance. A paper plane and a share arrow side by
              side both read as "share", and the arrow only copied the
              internal URL — a link that stops at the login wall. The verbs
              are worded here because "send to an inbox" vs "mint a public
              link" is exactly the distinction an icon can't make. */}
          <div className="download-menu song-download song-share" title="Share">
            <Menu<string>
              label={
                <span className="download-icon-label">
                  <IconShare />
                  <span className="sr-only">Share</span>
                </span>
              }
              options={[
                {label: "Send to a friend…", value: "send"},
                {label: "Make a link…", value: "link"},
              ]}
              value=""
              onChange={(action) => {
                if (action === "send") setSendTo(true);
                if (action === "link") setSharing(true);
              }}
            />
          </div>
        </div>
        <span className="song-time num dim">
          {clock(position)} / {clock(duration)}
        </span>
      </div>

      {noteHint ? (
        <button
          type="button"
          className="song-note-hint"
          onClick={() => setNoteHint(false)}
        >
          Heard something? Pin a note to this exact moment.
        </button>
      ) : null}

      <div className="song-composer">
        <textarea
          ref={composer}
          className="song-note-input"
          placeholder="Leave a note…"
          value={draft}
          rows={2}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              submitNote();
            }
          }}
        />
        <div className="song-composer-foot">
          <button
            className={`song-stamp${pinned !== null ? " is-on" : ""}`}
            onClick={() => setPinned(pinned === null ? position : null)}
            title={
              pinned === null
                ? "Pin this note to where the track is now"
                : "Unpin; the note will follow playback again"
            }
          >
            <IconClock />
            <span className="num">{clock(at)}</span>
          </button>
          <button
            className="primary song-send"
            onClick={submitNote}
            disabled={!draft.trim() || sending}
          >
            {sending ? "Saving…" : "Note"}
          </button>
        </div>
        {noteError ? (
          <p className="song-warn" role="alert">
            That note did not save. Nothing has been recorded.
          </p>
        ) : null}
      </div>

      {notes.length ? (
        <ol className="song-notes">
          {notes.map((note) => (
            <li key={note.id} className="song-note">
              <button
                className="song-note-at num"
                onClick={() => {
                  if (!isPlayingThis) player.play(versions.length ? versions : [track], 0);
                  if (duration) player.seek(note.timecode_s / duration);
                }}
              >
                {clock(note.timecode_s)}
              </button>
              <div>
                <p className="song-note-body">{note.note}</p>
                <p className="song-note-who dim">{note.actor}</p>
              </div>
            </li>
          ))}
        </ol>
      ) : null}

      {/* Playable stem rows when they exist; the empty state renders nothing —
          the inspector column owns the Rip affordance, and two identical
          "Rip stems" chips on one screen read as an accident. */}
      {stems.length ? (
        <section className="song-section">
          <h2 className="ins-label">Stems</h2>
          <div className="rows">
            {stems.map((stem, i) => (
              <button
                key={stem.bounce_ulid ?? i}
                className="row row-lite"
                onClick={() => player.play(stems as Track[], i)}
              >
                <span className="c-num num">
                  <span className="idx">{i + 1}</span>
                  <span className="tri">▶</span>
                </span>
                <span className="name">{stem.stem_kind ?? stem.title}</span>
                <span className="c-r num dim" />
                <span className="c-r num dim">{stem.duration_label || "—"}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {/* The ins-shell wrapper is what slides the sheet up on a phone —
          the bare <Inspector> rendered here used to mount translated
          off-screen below 1180px, so Edit tags looked wired to nothing. */}
      {tagsOpen ? (
        <div className="ins-shell is-open">
          <Inspector
            track={track}
            onClose={() => setTagsOpen(false)}
            showActions={false}
          />
        </div>
      ) : null}
      {sendTo ? <SendToDialog
          bounceUlid={track.bounce_ulid}
          title={track.title}
          onClose={() => setSendTo(false)}
        /> : null}
      {sharing ? <ShareDialog track={track} onClose={() => setSharing(false)} /> : null}
    </div>
  );
}
