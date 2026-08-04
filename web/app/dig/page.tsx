"use client";

import {useEffect, useMemo, useState} from "react";

import {IconShuffle} from "@/components/Icons";
import {PassCrateDialog} from "@/components/PassCrateDialog";
import {usePlayer, type Track} from "@/components/PlayerProvider";
import {camelotColor} from "@/lib/colors";
import {spreadShuffle} from "@/lib/shuffle";

type DigReason = "untagged" | "never_played" | "dormant";

type DigSummary = {
  total: number;
  neverPlayed: number;
  showing: number;
  dugToday: number;
};

type DigQueueItem = {
  id: string;
  trackUrl: string;
  audioUrl: string;
  title: string;
  digReason: DigReason;
  digReasonLabel: string;
};

type DigTrack = Track & {
  digReason: DigReason;
  digReasonLabel: string;
};

type DigFilters = {
  unheard: boolean;
  hearted: boolean;
  includeSketches: boolean;
};

const INITIAL_FILTERS: DigFilters = {
  unheard: false,
  hearted: false,
  includeSketches: false,
};

const STRATA: readonly {
  reason: DigReason;
  label: string;
  narration: string;
}[] = [
  {reason: "untagged", label: "NO VIBE YET", narration: "without a vibe yet"},
  {reason: "never_played", label: "NEVER PLAYED", narration: "nobody's played"},
  {reason: "dormant", label: "DORMANT", narration: "dormant"},
];

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Unexpected response");
  }
  return value as Record<string, unknown>;
}

function string(value: unknown): string {
  if (typeof value !== "string") throw new Error("Unexpected response");
  return value;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new Error("Unexpected response");
  }
  return value;
}

function digReason(value: unknown): DigReason {
  if (value === "untagged" || value === "never_played" || value === "dormant") {
    return value;
  }
  throw new Error("Unexpected response");
}

function queueItems(value: unknown): DigQueueItem[] {
  const payload = record(value);
  if (payload.mode !== "dig" || !Array.isArray(payload.tracks)) {
    throw new Error("Unexpected dig response");
  }
  return payload.tracks.map((raw) => {
    const item = record(raw);
    const parsed = {
      id: string(item.id),
      trackUrl: string(item.trackUrl),
      audioUrl: string(item.audioUrl),
      title: string(item.title),
      digReason: digReason(item.dig_reason),
      digReasonLabel: string(item.dig_reason_label),
    };
    if (
      parsed.trackUrl !== `/api/tracks/${parsed.id}` ||
      parsed.audioUrl !== `/m/${parsed.id}`
    ) {
      throw new Error("Unexpected dig track URL");
    }
    return parsed;
  });
}

function summary(value: unknown): DigSummary {
  const item = record(value);
  return {
    total: count(item.total),
    neverPlayed: count(item.never_played),
    showing: count(item.showing),
    dugToday: count(item.dug_today),
  };
}

function trackDetails(value: unknown): Track {
  const item = record(value);
  string(item.bounce_ulid);
  string(item.song_ulid);
  string(item.title);
  return item as Track;
}

async function checkedJson(url: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(url, {credentials: "same-origin", signal});
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

function reasonParts(track: DigTrack): {keyword: string; detail: string} {
  if (track.digReason !== "dormant") {
    return {keyword: track.digReasonLabel, detail: ""};
  }
  const keyword = "LAST HEARD";
  return {
    keyword,
    detail: track.digReasonLabel.slice(keyword.length).trim(),
  };
}

function lookingAt(
  digSummary: DigSummary,
  activeReason: DigReason | null,
  visibleCount: number,
): string {
  if (!activeReason) {
    return `you're looking at the ${digSummary.showing} coldest`;
  }
  const stratum = STRATA.find((item) => item.reason === activeReason);
  return `you're looking at the ${visibleCount} ${stratum?.narration ?? "coldest"}`;
}

export default function Dig() {
  const [tracks, setTracks] = useState<DigTrack[]>([]);
  const [digSummary, setDigSummary] = useState<DigSummary | null>(null);
  const [filters, setFilters] = useState<DigFilters>(INITIAL_FILTERS);
  const [activeReason, setActiveReason] = useState<DigReason | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [playedThisVisit, setPlayedThisVisit] = useState<Set<string>>(
    () => new Set(),
  );
  const [passDialogOpen, setPassDialogOpen] = useState(false);
  const [passedTo, setPassedTo] = useState("");
  const player = usePlayer();

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (filters.unheard) params.set("unheard", "true");
    if (filters.hearted) params.set("hearted", "true");
    params.set("skip_sketches", filters.includeSketches ? "false" : "true");

    async function load() {
      setLoading(true);
      setError("");
      try {
        const rawPayload = await checkedJson(`/api/dig?${params}`, controller.signal);
        const payload = record(rawPayload);
        const queue = queueItems(payload);
        // Details ride in the same response — one request total. Fetching
        // /api/tracks per item burned the rate budget at ~650 requests.
        const details = payload.details;
        if (!Array.isArray(details) || details.length !== queue.length) {
          throw new Error("Unexpected dig response");
        }
        const detailed = queue.map((item, index) => {
          const track = trackDetails(details[index]);
          if (track.bounce_ulid !== item.id) {
            throw new Error("Dig track did not match its queue item");
          }
          return {
            ...track,
            title: item.title,
            digReason: item.digReason,
            digReasonLabel: item.digReasonLabel,
          };
        });
        const nextSummary = summary(payload.dig_summary);
        if (!controller.signal.aborted) {
          setTracks(detailed);
          setDigSummary(nextSummary);
          setActiveReason((current) =>
            current && detailed.some((track) => track.digReason === current)
              ? current
              : null,
          );
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        setTracks([]);
        setDigSummary(null);
        setError(cause instanceof Error ? cause.message : "Could not load Dig");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    load();
    return () => controller.abort();
  }, [filters]);

  function toggle(filter: keyof DigFilters) {
    setFilters((current) => ({...current, [filter]: !current[filter]}));
  }

  const strata = useMemo(
    () =>
      STRATA.map((stratum) => ({
        ...stratum,
        tracks: tracks.filter((track) => track.digReason === stratum.reason),
      })).filter((stratum) => stratum.tracks.length > 0),
    [tracks],
  );
  const visibleStrata = activeReason
    ? strata.filter((stratum) => stratum.reason === activeReason)
    : strata;
  const visibleTracks = useMemo(
    () => visibleStrata.flatMap((stratum) => stratum.tracks),
    [visibleStrata],
  );
  const visibleIndex = useMemo(
    () => new Map(visibleTracks.map((track, index) => [track.bounce_ulid, index])),
    [visibleTracks],
  );
  const coldTracks = useMemo(
    () => visibleTracks.filter((track) => !playedThisVisit.has(track.bounce_ulid)),
    [playedThisVisit, visibleTracks],
  );
  const activeStratum = STRATA.find((stratum) => stratum.reason === activeReason);

  function shuffleStratum(stratumTracks: DigTrack[]) {
    const shuffled = spreadShuffle(stratumTracks);
    if (shuffled.length) player.play(shuffled, 0);
  }

  function playFromRow(track: DigTrack) {
    setPlayedThisVisit((current) => {
      if (current.has(track.bounce_ulid)) return current;
      const next = new Set(current);
      next.add(track.bounce_ulid);
      return next;
    });
    player.play(
      visibleTracks,
      visibleIndex.get(track.bounce_ulid) ?? 0,
    );
  }

  return (
    <div className="dig page-scroll">
      <div className="dig-inner">
        <header className="dig-head">
          <div>
            <h1 className="lib-title">Dig</h1>
            <p className="lib-count dig-summary num">
              {loading ? "Digging" : digSummary ? (
                <>
                  {digSummary.total} in the crate · {digSummary.neverPlayed}
                  {" nobody's opened · "}
                  {lookingAt(digSummary, activeReason, visibleTracks.length)}
                  {digSummary.dugToday > 0 ? (
                    <> · you dug <span className="dig-dent">{digSummary.dugToday}</span> out today</>
                  ) : null}
                </>
              ) : null}
            </p>
            {passedTo ? (
              <p className="dig-handoff">passed to {passedTo}</p>
            ) : playedThisVisit.size >= 3 && coldTracks.length >= 10 ? (
              <p className="dig-handoff">
                you left <span className="num">{coldTracks.length}</span> cold ones —{" "}
                <button
                  className="dig-handoff-action"
                  type="button"
                  onClick={() => setPassDialogOpen(true)}
                >
                  pass the next 10 to someone?
                </button>
              </p>
            ) : null}
          </div>
          <button
            className="btn btn-main"
            disabled={loading || visibleTracks.length === 0}
            onClick={() => player.play(visibleTracks, 0)}
          >
            Play all
          </button>
        </header>

        <div className="dig-filters" aria-label="Dig filters">
          <button
            className={`chip${filters.unheard ? " is-on" : ""}`}
            aria-pressed={filters.unheard}
            onClick={() => toggle("unheard")}
          >
            unheard
          </button>
          <button
            className={`chip${filters.hearted ? " is-on" : ""}`}
            aria-pressed={filters.hearted}
            onClick={() => toggle("hearted")}
          >
            hearted
          </button>
          <button
            className={`chip${filters.includeSketches ? " is-on" : ""}`}
            aria-pressed={filters.includeSketches}
            onClick={() => toggle("includeSketches")}
          >
            include sketches
          </button>
        </div>

        {activeReason && activeStratum ? (
          <div className="active-filter-pills dig-reason-filter" aria-label="Active filters">
            <button
              className="active-filter-pill"
              type="button"
              aria-label={`Remove ${activeStratum.label} filter`}
              onClick={() => setActiveReason(null)}
            >
              {activeStratum.label} <span aria-hidden="true">×</span>
            </button>
          </div>
        ) : null}

        {error ? (
          <p className="dig-error" role="alert">
            Could not load Dig. {error}
          </p>
        ) : null}

        {!loading && !error && tracks.length === 0 ? (
          <p className="empty teaching-empty">
            Dig surfaces what the crate forgot — untagged first, then never played. Nothing qualifies right now.
          </p>
        ) : null}

        <div className="dig-strata" aria-busy={loading}>
          {visibleStrata.map((stratum) => (
            <section
              className="dig-stratum"
              key={stratum.reason}
              aria-labelledby={`dig-stratum-${stratum.reason}`}
            >
              <div className="dig-stratum-head">
                <button
                  id={`dig-stratum-${stratum.reason}`}
                  className="ins-label dig-stratum-label"
                  type="button"
                  onClick={() => setActiveReason(stratum.reason)}
                >
                  <span>{stratum.label}</span>
                  <span className="dig-stratum-count num">{stratum.tracks.length}</span>
                </button>
                <button
                  className="dig-stratum-shuffle"
                  type="button"
                  aria-label="Shuffle this stratum"
                  title="Shuffle this stratum"
                  onClick={() => shuffleStratum(stratum.tracks)}
                >
                  <IconShuffle size={16} />
                </button>
              </div>

              <ul className="dig-list">
                {stratum.tracks.map((track) => {
                  const isPlaying = player.current?.bounce_ulid === track.bounce_ulid;
                  const keyColor = camelotColor(track.key_camelot);
                  const reason = reasonParts(track);
                  return (
                    <li key={track.bounce_ulid}>
                      <div className={`dig-row${isPlaying ? " is-playing" : ""}`}>
                        <button
                          className="dig-row-hit"
                          type="button"
                          onClick={() => playFromRow(track)}
                          aria-label={`Play ${track.title} and queue the visible Dig tracks`}
                        />
                        <img
                          className="row-art"
                          src={`/art/${track.bounce_ulid}`}
                          alt=""
                          loading="lazy"
                        />
                        <span className="dig-copy">
                          <button
                            className="dig-reason"
                            type="button"
                            onClick={() => setActiveReason(track.digReason)}
                          >
                            <span className="dig-reason-key">{reason.keyword}</span>
                            {reason.detail ? (
                              <> <span>{reason.detail}</span></>
                            ) : null}
                          </button>
                          <span className="dig-title">{track.title}</span>
                        </span>
                        <span className="dig-meta num">
                          <span style={keyColor ? {color: keyColor} : undefined}>
                            {track.key_canon || "—"}
                          </span>
                          {"  ·  "}
                          {track.bpm ? `${Math.round(track.bpm)} bpm` : "—"}
                          {"  ·  "}
                          {track.duration_label || "—"}
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      </div>
      {passDialogOpen ? (
        <PassCrateDialog
          bounceUlids={coldTracks.slice(0, 10).map((track) => track.bounce_ulid)}
          onClose={() => setPassDialogOpen(false)}
          onPassed={(member) => {
            setPassedTo(member.display || member.username);
            setPassDialogOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}
