"use client";

import {memo, type CSSProperties} from "react";
import Link from "next/link";
import {ArtStrip, HoverArt} from "@/components/ArtStrip";
import {camelotColor} from "@/lib/colors";
import type {LibraryTrack} from "@/hooks/useLibraryQuery";

type LibraryRowProps = {
  track: LibraryTrack;
  index: number;
  isPlaying: boolean;
  isPicked: boolean;
  isHearted: boolean;
  compact: boolean;
  eraColor?: string;
  transportPlaying: boolean;
  selectMode: boolean;
  dataIndex: number;
  measureRef: (element: HTMLDivElement | null) => void;
  style: CSSProperties;
  onPlay: (index: number) => void;
  onTogglePick: (bounceUlid: string) => void;
  onHeart: (bounceUlid: string) => void;
  onOpenSheet: (track: LibraryTrack) => void;
};

function LibraryRowView({
  track,
  index,
  isPlaying,
  isPicked,
  isHearted,
  compact,
  eraColor,
  transportPlaying,
  selectMode,
  dataIndex,
  measureRef,
  style,
  onPlay,
  onTogglePick,
  onHeart,
  onOpenSheet,
}: LibraryRowProps) {
  return (
    <div
      data-index={dataIndex}
      ref={measureRef}
      className={`row${isPlaying ? " is-playing" : ""}${
        isPicked ? " is-picked" : ""
      }`}
      // Tapping a record plays it. On the desktop table that job
      // belongs to the gutter triangle; on a phone the gutter is gone,
      // and without this there is no way to play anything from the list.
      onClick={compact ? () => {
        if (selectMode) onTogglePick(track.bounce_ulid);
        else onPlay(index);
      } : undefined}
      style={style}
    >
      <input
        className="pick"
        type="checkbox"
        checked={isPicked}
        aria-label={`Select ${track.title}`}
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => {
          event.stopPropagation();
          onTogglePick(track.bounce_ulid);
        }}
      />
      <button
        className="row-play"
        onClick={() => onPlay(index)}
        aria-label={`Play ${track.title}`}
      >
      {/* The gutter number becomes the play affordance on hover, so a row
          has exactly one control instead of a stack of buttons competing
          with the title. */}
      <span className="c-num num">
        <span className="idx">
          {/* The playing row shows moving bars instead of its number:
              the band alone said "selected", not "making sound". They
              freeze on pause rather than resetting. */}
          {isPlaying ? (
            <span
              className={`eq${transportPlaying ? "" : " is-paused"}`}
              aria-hidden="true"
            >
              <i />
              <i />
              <i />
            </span>
          ) : (
            <>
              {track.unheard ? (
                <i
                  className="dot"
                  style={eraColor ? {background: eraColor} : undefined}
                />
              ) : null}
              {index + 1}
            </>
          )}
        </span>
        <span className="tri">{isPlaying ? "❙❙" : "▶"}</span>
      </span>
      </button>
      {/* Art and the combined meta line exist only for narrow screens,
          where a row has to carry its own identity instead of relying
          on column headings that are not there. */}
      {isPlaying ? (
        <ArtStrip bounceUlid={track.bounce_ulid} />
      ) : (
        <HoverArt bounceUlid={track.bounce_ulid} />
      )}
      <span className={`name${track.vibe_tags?.length ? " has-vibes" : ""}`}>
        <Link
          className="name-link"
          href={`/songs/${track.song_ulid}`}
          title={track.title}
          onClick={(event) => event.stopPropagation()}
        >
          {track.title}
        </Link>
        {track.vibe_tags?.length ? (
          <span className="row-vibes">
            {track.vibe_tags.slice(0, 3).join(" · ")}
          </span>
        ) : null}
      </span>
      <span className="row-meta num">
        {[
          track.key_canon,
          track.bpm ? `${Math.round(track.bpm)} bpm` : null,
          track.duration_label,
          track.date_label,
          Number(track.ears) >= 1 ? `${track.ears} ears` : null,
          Number(track.keeper) >= 1 ? `k${track.keeper}` : null,
        ]
          // A server-side placeholder dash is not a value; joining it
          // renders "125 bpm · — · May" (the empty-state law).
          .filter((part) => Boolean(part) && part !== "—")
          .join("  ·  ")}
      </span>
      <button
        className={`heart${isHearted ? " is-on" : ""}`}
        aria-pressed={isHearted}
        aria-label={`Heart ${track.title}`}
        onClick={(event) => {
          event.stopPropagation();
          onHeart(track.bounce_ulid);
        }}
      >
        {isHearted ? "♥" : "♡"}
      </button>
      <span
        className="c-r num dim key-cell"
        style={
          camelotColor(track.key_camelot)
            ? {color: camelotColor(track.key_camelot) as string}
            : undefined
        }
        title={track.key_camelot ?? undefined}
      >
        {track.key_canon || "—"}
      </span>
      <span className="c-r num dim">{track.bpm ? Math.round(track.bpm) : "—"}</span>
      <span className="c-r num dim">{track.duration_label || "—"}</span>
      <span className="c-r num dim">{track.date_label || "—"}</span>
      <span className="c-r num dim">
        {Number(track.ears) >= 1 ? `${track.ears} ears` : ""}
      </span>
      {/* Keeper shows as a badge beside versions, not a column: the
          twelfth fixed column crushed the name to 14px at 1280 with
          the inspector open, and the name is the row's identity. */}
      <span className="c-r num dim">
        {track.version_count && track.version_count > 1
          ? `v${track.version_count}`
          : "—"}
        {Number(track.keeper) >= 1 ? (
          <span className="keeper-mark"> k{track.keeper}</span>
        ) : null}
      </span>
      {/* Version count reads as a badge beside the title when there are
          no column headings to explain a bare number. */}
      <span className="row-badges num">
        {track.version_count && track.version_count > 1 ? (
          <span className="vbadge">v{track.version_count}</span>
        ) : null}
        {Number(track.keeper) >= 1 ? (
          <span className="keeper-mark">k{track.keeper}</span>
        ) : null}
      </span>
      <button
        className="row-more"
        aria-label={`More for ${track.title}`}
        onClick={(event) => {
          event.stopPropagation();
          onOpenSheet(track);
        }}
      >
        ⋯
      </button>
    </div>
  );
}

export const LibraryRow = memo(LibraryRowView, (prev, next) => (
  prev.track === next.track &&
  prev.index === next.index &&
  prev.isPlaying === next.isPlaying &&
  prev.isPicked === next.isPicked &&
  prev.isHearted === next.isHearted &&
  prev.compact === next.compact &&
  prev.eraColor === next.eraColor &&
  prev.transportPlaying === next.transportPlaying &&
  prev.selectMode === next.selectMode &&
  prev.dataIndex === next.dataIndex &&
  prev.style.transform === next.style.transform &&
  prev.onPlay === next.onPlay &&
  prev.onTogglePick === next.onTogglePick &&
  prev.onHeart === next.onHeart &&
  prev.onOpenSheet === next.onOpenSheet
));
