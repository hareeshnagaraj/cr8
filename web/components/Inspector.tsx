"use client";

import {useCallback, useEffect, useState} from "react";
import Link from "next/link";
import {usePathname} from "next/navigation";

import {camelotForKey, dimensionActive, keyNameColor} from "@/lib/colors";
import {DownloadMenu} from "./DownloadMenu";
import {IconDownload, IconShare, IconStems} from "./Icons";
import {Menu} from "./Menu";
import {SendToDialog} from "./SendToDialog";
import {ShareDialog} from "./ShareDialog";
import {TagInput} from "./TagInput";
import {colorsByName, getEras} from "@/lib/eras";

import type {Track} from "./PlayerProvider";

type Value = {value: string; active: boolean; source?: string};
type Dimension = {name: string; values: Value[]; allow_new?: boolean};
type Panel = {song: Record<string, unknown>; dimensions: Dimension[]};

const LABEL: Record<string, string> = {
  status: "Status",
  keeper: "Keeper",
  key: "Key",
  vibe: "Vibe",
  instr: "Instruments",
  collab: "Collaborators",
  use: "Use",
  problem: "Problems",
};

export function Inspector({
  track,
  onClose,
  showActions = true,
}: {
  track: Track | null;
  onClose?: () => void;
  showActions?: boolean;
}) {
  const pathname = usePathname();
  const actionsVisible = showActions && !pathname.startsWith("/songs/");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const [stems, setStems] = useState<{stem_kind?: string; bounce_ulid?: string}[]>([]);
  const [ripping, setRipping] = useState(false);
  const [eras, setEras] = useState<Record<string, string>>({});

  const load = useCallback(async (songUlid: string) => {
    const response = await fetch(`/api/songs/${songUlid}`, {
      credentials: "same-origin",
    });
    if (response.ok) setPanel(await response.json());
  }, []);

  useEffect(() => {
    if (!track) {
      setPanel(null);
      return;
    }
    setPanel(null);
    load(track.song_ulid);
  }, [track?.song_ulid, load]);

  useEffect(() => {
    getEras()
      .then((eras) => setEras(colorsByName(eras)))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!track) return;
    setStems([]);
    fetch(`/api/stems/${track.bounce_ulid}`, {credentials: "same-origin"})
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setStems)
      .catch(() => setStems([]));
  }, [track?.bounce_ulid]);

  // Separating stems used to mean navigating to the song page and finding a
  // button. It is one click from wherever you are listening.
  async function rip() {
    if (!track) return;
    setRipping(true);
    // Check the result. This POST 404'd for the entire port because the path
    // was never proxied, and swallowing the response meant the button just
    // span for five minutes and gave up without ever saying why.
    const queued = await fetch(`/stems/${track.bounce_ulid}`, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CR8-Request": "1",
      },
      body: new URLSearchParams({recipe: "default-v1"}),
    }).catch(() => undefined);
    if (!queued || !queued.ok) {
      setRipping(false);
      return;
    }
    const ulid = track.bounce_ulid;
    const poll = window.setInterval(async () => {
      const response = await fetch(`/api/stems/${ulid}`, {
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const fresh = await response.json();
      if (fresh.length) {
        setStems(fresh);
        setRipping(false);
        window.clearInterval(poll);
      }
    }, 5000);
    window.setTimeout(() => {
      window.clearInterval(poll);
      setRipping(false);
    }, 300000);
  }

  async function toggle(dim: string, value: string) {
    if (!track) return;
    setPending(`${dim}:${value}`);

    // Flip locally first so the chip answers the click immediately, then reload
    // from the server. A tag write that silently failed used to look identical
    // to one that worked.
    setPanel((prev) =>
      prev
        ? {
            ...prev,
            dimensions: prev.dimensions.map((group) =>
              group.name !== dim
                ? group
                : {
                    ...group,
                    values: group.values.map((item) =>
                      item.value === value
                        ? {...item, active: !item.active}
                        : dim === "status" || dim === "keeper" || dim === "key"
                          ? {...item, active: false}
                          : item,
                    ),
                  },
            ),
          }
        : prev,
    );

    try {
      const response = await fetch(`/api/songs/${track.song_ulid}/tags/toggle`, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CR8-Request": "1"},
        body: JSON.stringify({dim, value, bounce_ulid: track.bounce_ulid}),
      });
      return response.ok;
    } catch {
      return false;
    } finally {
      await load(track.song_ulid);
      setPending(null);
    }
  }

  if (!track) {
    return (
      <aside className="inspector">
        <p className="ins-empty">Play something to tag it.</p>
      </aside>
    );
  }

  return (
    <aside className="inspector">
      {onClose ? (
        <button className="ins-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      ) : null}
      {/* One identity block, laid out like the song page: art, era, title,
          facts. It used to stack a full-width square cover above the title,
          then put the era on its own line, then a row of four buttons of
          which two were the same download glyph told apart by a 10px badge -
          which is not a distinction anyone makes at a glance. The actions are
          worded now, and sized to be hittable. */}
      <header className="ins-head">
        <Link className="ins-art-link" href={`/songs/${track.song_ulid}`}>
          <img className="ins-art" src={`/art/${track.bounce_ulid}`} alt="" />
        </Link>
        <div className="ins-id">
          {track.era ? (
            <p className="ins-era">
              <i className="era-dot" style={{background: eras[track.era] ?? "var(--t3)"}} />
              {track.era}
            </p>
          ) : null}
          <h2 className="ins-title">
            <Link href={`/songs/${track.song_ulid}`}>{track.title}</Link>
          </h2>
          <p className="ins-sub num">
            {[
              track.key_canon,
              track.key_camelot,
              track.bpm ? `${Math.round(track.bpm)} bpm` : null,
              track.duration_label,
            ]
              .filter(Boolean)
              .join("  ·  ")}
          </p>
        </div>
        {actionsVisible ? (
          <div className="ins-actions">
            <Link className="ins-act ins-act-open" href={`/songs/${track.song_ulid}`}>
              Open
            </Link>
            <button className="ins-act" onClick={() => setSendOpen(true)}>
              Send to
            </button>
            <DownloadMenu track={track} stems={stems} className="ins-download" />
            <button className="ins-act" onClick={() => setSharing(true)}>
              <IconShare />
              Share
            </button>
          </div>
        ) : null}
      </header>

      {sharing ? (
        <ShareDialog track={track} onClose={() => setSharing(false)} />
      ) : null}

      {sendOpen ? (
        <SendToDialog
          bounceUlid={track.bounce_ulid}
          title={track.title}
          onClose={() => setSendOpen(false)}
        />
      ) : null}

      <section className="ins-tag-add">
        <TagInput
          sourceSongUlid={track.song_ulid}
          onApply={toggle}
          placeholder="Add a tag…"
          ariaLabel="Add a tag to this song"
        />
      </section>

      {panel ? panel.dimensions.map((group) => {
        const active = group.values.filter((item) => item.active);

        if (group.name === "key") {
          const current = active[0];
          const value = current?.value
            ?? (typeof panel.song.key_canon === "string" ? panel.song.key_canon : "");
          const camelot = camelotForKey(value)
            ?? (value === track.key_canon ? track.key_camelot : null);
          const color = keyNameColor(value);
          return (
            <section className="ins-group" key={group.name}>
              <h3 className="ins-label">Key</h3>
              <div className="ins-fact-row">
                <span
                  className="ins-fact ins-key-fact num"
                  style={color ? {background: color, color: "#101010"} : undefined}
                >
                  {value || "Not set"}{camelot ? ` · ${camelot}` : ""}
                </span>
                <div className="ins-fact-edit">
                  <Menu
                    label="edit"
                    options={group.values.map((item) => ({
                      value: item.value,
                      swatch: keyNameColor(item.value) ?? undefined,
                    }))}
                    value={value}
                    searchable
                    onChange={(next) => void toggle(group.name, next)}
                  />
                </div>
                {value && current?.source !== "human" ? (
                  <span className="ins-fact-source">set by catalog</span>
                ) : null}
              </div>
            </section>
          );
        }

        if (group.name === "status") {
          const value = active[0]?.value
            ?? (typeof panel.song.status === "string" ? panel.song.status : "");
          return (
            <section className="ins-group" key={group.name}>
              <h3 className="ins-label">Status</h3>
              <div className="ins-fact-row">
                <span className="ins-fact ins-status-fact">{value || "Not set"}</span>
                <div className="ins-fact-edit">
                  <Menu
                    label="edit"
                    options={group.values.map((item) => ({value: item.value}))}
                    value={value}
                    onChange={(next) => void toggle(group.name, next)}
                  />
                </div>
              </div>
            </section>
          );
        }

        if (group.name === "keeper") {
          return (
            <section className="ins-group" key={group.name}>
              <h3 className="ins-label">Keeper</h3>
              <div className="keeper-cells">
                {group.values.map((item) => (
                  <button
                    key={item.value}
                    className={`keeper-cell${item.active ? " is-on" : ""}`}
                    aria-pressed={item.active}
                    disabled={pending === `${group.name}:${item.value}`}
                    onClick={() => void toggle(group.name, item.value)}
                  >
                    {item.value}
                  </button>
                ))}
              </div>
            </section>
          );
        }

        if (!active.length) return null;
        return (
          <section className="ins-group" key={group.name}>
            <h3 className="ins-label">{LABEL[group.name] ?? group.name}</h3>
            <div className="chips">
              {active.map((item) => item.source === "human" ? (
                <button
                  key={item.value}
                  className="chip is-on"
                  style={{background: dimensionActive(group.name) ?? undefined}}
                  aria-pressed="true"
                  disabled={pending === `${group.name}:${item.value}`}
                  onClick={() => void toggle(group.name, item.value)}
                >
                  {item.value}
                </button>
              ) : (
                <span className="ins-derived" key={item.value}>
                  {item.value}
                  <span>{" — "}{item.source || "derived"}</span>
                </span>
              ))}
            </div>
          </section>
        );
      }) : <p className="ins-empty">Loading tags…</p>}

      <section className="ins-group">
        <h3 className="ins-label">Stems</h3>
        {stems.length ? (
          <div className="stem-list">
            {stems.map((stem) => (
              <a
                className="stem"
                href={`/download/stem/${stem.bounce_ulid}`}
                key={stem.bounce_ulid}
              >
                <IconStems size={13} />
                {stem.stem_kind ?? "stem"}
                <IconDownload size={13} />
              </a>
            ))}
          </div>
        ) : (
          <button className="chip chip-icon" onClick={rip} disabled={ripping}>
            <IconStems />
            {ripping ? "Separating…" : "Rip stems"}
          </button>
        )}
      </section>
    </aside>
  );
}
