"use client";

import {
  Fragment, useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import {useVirtualizer} from "@tanstack/react-virtual";
import {
  LibraryFilterBar, NO_FILTERS, nextSort,
  type Filters, type SortOption,
} from "@/components/FilterRail";

import {usePlayer, type Track} from "@/components/PlayerProvider";
import {spreadShuffle} from "@/lib/shuffle";
import {Inspector} from "@/components/Inspector";
import {Letter, useLetterDismissed} from "@/components/Letter";
import {bpmBounds, bpmHistogram} from "@/lib/dig";
import {colorsByName, getEras} from "@/lib/eras";
import {IconCheck} from "@/components/Icons";
import {BulkBar} from "@/components/BulkBar";
import {LibraryRow} from "@/components/LibraryRow";
import {useLibraryQuery} from "@/hooks/useLibraryQuery";
import {useSelection} from "@/hooks/useSelection";
import {useHearts, type BulkFeedback} from "@/hooks/useHearts";
import {useCompact} from "@/hooks/useCompact";
import {useRowScrollMemory} from "@/hooks/useRowScrollMemory";
import {useKeyboardTransport} from "@/hooks/useKeyboardTransport";
import {useCuedLanding} from "@/hooks/useCuedLanding";

// Sorting is executed by SQLite, not in the browser: the catalog already knows
// how to order 472 rows and the client should not hold a second opinion.
// Keeper sorts from the Order menu rather than a column header: a twelfth
// fixed column crushed the name cell at 1280px with the inspector open.
const SORT_OPTIONS: readonly SortOption[] = [
  {key: "title", label: "Name", asc: "title", desc: "title-desc", column: {cls: "th-name"}},
  {key: "key_canon", label: "Key", asc: "key", desc: "key-desc", column: {cls: ""}},
  {key: "bpm", label: "BPM", asc: "bpm", desc: "bpm-desc", column: {cls: ""}},
  {key: "duration", label: "Length", asc: "longest", desc: "shortest", column: {cls: ""}},
  {key: "added", label: "Created", asc: "newest", desc: "oldest", column: {cls: ""}},
  {key: "ears", label: "Ears", asc: "ears", desc: "ears-desc", column: {cls: ""}},
  {key: "versions", label: "Versions", asc: "versions", desc: "versions-desc", column: {cls: ""}},
  {
    key: "keeper", label: "Keeper", asc: "keeper", desc: "keeper-desc",
    initial: "desc",
  },
  {
    key: "random", label: "Random", asc: "random", desc: "random",
    fixed: true,
  },
];

type SinceYouWereHere = {new_songs: number; people: number};

export default function Library() {
  const [q, setQ] = useState("");
  const [bpm, setBpm] = useState<[number | null, number | null]>([null, null]);
  // The row overflow opens the inspector for that track rather than a second
  // menu, so tagging, send-to, share, download and stems all come along.
  const [sheetTrack, setSheetTrack] = useState<Track | null>(null);
  const [sort, setSort] = useState("newest");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [bulkFeedback, setBulkFeedback] = useState<BulkFeedback>(null);
  const [eras, setEras] = useState<Record<string, string>>({});
  const [sinceYouWereHere, setSinceYouWereHere] = useState<SinceYouWereHere | null>(null);
  const player = usePlayer();
  const letterDismissed = useLetterDismissed();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const {
    tracks, setTracks, allTracks, libraryTracks, total, loading,
    shuffled, setShuffled, filtersActive, requestRandom,
  } = useLibraryQuery({q, sort, filters, bpm, scrollRef});

  const {picked, selectMode, setSelectMode, togglePicked, dropPicked, clearPicked} = useSelection();
  const {hearts, heart} = useHearts(setBulkFeedback);
  const compact = useCompact();
  useRowScrollMemory(scrollRef, tracks.length);
  useKeyboardTransport();
  useCuedLanding(allTracks, loading);

  useEffect(() => {
    // The door is garnish, and garnish never competes with the track you
    // just started for the pipe (DESIGN.md): fetching this at mount cost
    // ~500ms of press-play latency through the 5 Mbit probe and went over
    // budget. It waits out the landing rush instead.
    const timer = window.setTimeout(() => {
      fetch("/api/since-you-were-here", {credentials: "same-origin"})
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((data: {quiet?: boolean; new_songs?: number; people?: number}) => {
          if (data.quiet) return;
          const newSongs = Math.max(0, Math.trunc(Number(data.new_songs) || 0));
          const people = Math.max(0, Math.trunc(Number(data.people) || 0));
          if (newSongs || people) {
            setSinceYouWereHere({new_songs: newSongs, people});
          }
        })
        .catch(() => undefined);
    }, 5000);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    getEras()
      .then((eras) => setEras(colorsByName(eras)))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!compact) setSelectMode(false);
  }, [compact, setSelectMode]);

  const unheardTracks = useMemo(
    () => libraryTracks.filter((track) => Number(track.ears) === 0),
    [libraryTracks],
  );
  const keys = useMemo(
    () => [...new Set(
      libraryTracks
        .map((track) => track.key_canon)
        .filter((value): value is string => Boolean(value)),
    )].sort((left, right) => left.localeCompare(right)),
    [libraryTracks],
  );

  // The tempo range a person can actually pick reflects the crate they have.
  const [bpmFloor, bpmCeiling] = useMemo(() => bpmBounds(allTracks), [allTracks]);
  const histogram = useMemo(
    () => bpmHistogram(allTracks, bpmFloor, bpmCeiling),
    [allTracks, bpmFloor, bpmCeiling],
  );

  const changeBpm = useCallback(
    (low: number, high: number) =>
      setBpm([
        low <= bpmFloor ? null : low,
        high >= bpmCeiling ? null : high,
      ]),
    [bpmCeiling, bpmFloor],
  );

  const changeSort = useCallback((next: string) => {
    setSort(next);
    if (next === "random") requestRandom();
  }, [requestRandom]);

  const shuffle = useCallback(() => {
    // Spread rather than uniform - see web/lib/shuffle.ts for why a correct
    // Fisher-Yates was the thing making shuffle sound broken.
    const order = spreadShuffle(tracks);
    if (!order.length) return;
    setTracks(order);
    setShuffled(true);
    player.play(order, 0);
    scrollRef.current?.scrollTo({top: 0});
  }, [tracks, setTracks, setShuffled, player]);

  const playAt = useCallback(
    (index: number) => player.play(tracks, index),
    [player, tracks],
  );
  const openSheet = useCallback((track: Track) => setSheetTrack(track), []);

  const chosen = useMemo(
    () => tracks.filter((track) => picked.has(track.bounce_ulid)),
    [tracks, picked],
  );

  const playingUlid = player.current?.bounce_ulid;

  // Rows are absolutely positioned into slots, so a slot that is shorter than
  // its row makes rows overlap the ones after them. A fixed estimate cannot be
  // right: the same markup is 48px in the desktop table, taller on a phone, and
  // taller again with a larger iOS text size or a different font fallback. It
  // was guessed at 68 and overlapped on a real handset.
  //
  // measureElement reports what each row actually rendered as, so the estimate
  // only has to be close enough to avoid a jump on first paint.
  const rowVirtualizer = useVirtualizer({
    count: tracks.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => (compact ? 68 : 48),
    measureElement: (element) => element.getBoundingClientRect().height,
    overscan: 12,
  });

  useEffect(() => {
    rowVirtualizer.measure();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compact]);

  return (
    <div className="lib">
      <Letter dismissed={letterDismissed} />
      <header className="lib-head">
        <div>
          <h1 className="lib-title">Library</h1>
          {/* The door speaks inside the count line: appending to a line that
              already exists swaps text in place, where a separate late-
              mounting line above the header displaced everything below it —
              the exact layout-shift class the gate refuses. */}
          <p className="lib-count num">
            {loading ? "Loading" : `${total} songs`}
            {q && !loading ? ` matching “${q}”` : ""}
            {shuffled && !loading ? " · shuffled" : ""}
            {sinceYouWereHere?.new_songs && !loading ? (
              <>
                {" · "}
                <button
                  className="unheard-shift-action"
                  type="button"
                  onClick={() => setSort("newest")}
                >
                  {sinceYouWereHere.new_songs} new
                </button>{" "}
                since you were here
              </>
            ) : null}
            {sinceYouWereHere?.people && !loading ? (
              <>
                {" · "}
                {sinceYouWereHere.people}{" "}
                {sinceYouWereHere.people === 1 ? "person" : "people"} came through
              </>
            ) : null}
            {!filtersActive && unheardTracks.length && !loading ? (
              <>
                {" · "}
                {unheardTracks.length} never heard by anyone —{" "}
                <button
                  className="unheard-shift-action"
                  type="button"
                  onClick={() => player.play(spreadShuffle(unheardTracks), 0)}
                >
                  Take a shift?
                </button>
              </>
            ) : null}
          </p>
        </div>
        <div className="lib-actions">
          <input
            className="search desktop-only"
            placeholder="Search  /"
            value={q}
            onChange={(event) => setQ(event.target.value)}
          />
          <button className="primary desktop-only" onClick={shuffle}>Shuffle</button>
          <button
            className={`mobile-icon-button mobile-only mobile-select-button${
              selectMode ? " is-on" : ""
            }`}
            type="button"
            aria-label="Select songs"
            title="Select songs"
            aria-pressed={selectMode}
            onClick={() => {
              if (selectMode) clearPicked();
              else setSelectMode(true);
            }}
          >
            <IconCheck size={19} />
          </button>
        </div>
      </header>

      <LibraryFilterBar
        filters={filters}
        onChange={setFilters}
        bpm={bpm}
        bpmFloor={bpmFloor}
        bpmCeiling={bpmCeiling}
        histogram={histogram}
        onBpmChange={changeBpm}
        query={q}
        onQueryChange={setQ}
        onShuffle={shuffle}
        sort={sort}
        sortOptions={SORT_OPTIONS}
        onSortChange={changeSort}
        eras={eras}
        keys={keys}
      />

      {chosen.length ? (
        <BulkBar
          chosen={chosen}
          onDone={clearPicked}
          onConsumed={dropPicked}
          onPlay={(list) => player.play(list, 0)}
          onEnqueue={(list) => player.enqueue(list)}
          setFeedback={setBulkFeedback}
        />
      ) : null}

      {bulkFeedback ? (
        <p
          className={`bulk-feedback is-${bulkFeedback.kind}`}
          role={bulkFeedback.kind === "error" ? "alert" : "status"}
        >
          {bulkFeedback.message}
        </p>
      ) : null}

      <div className={`lib-body${picked.size ? " is-selecting" : ""}${
        selectMode ? " is-select-mode" : ""
      }`}>
      <div className="lib-table">
      <div className="thead">
        <span className="c-pick" />
        <span className="c-num">#</span>
        {/* Artwork and the overflow menu have no headings, so the grid needs
            placeholders or every label after them sits over the wrong column. */}
        <span className="c-art-head" />
        {SORT_OPTIONS.filter((option) => option.column).map((option, index) => {
          const column = option.column;
          if (!column) return null;
          const active = sort === option.asc || sort === option.desc;
          return (
            <Fragment key={option.key}>
              {/* The heart column has no label; without this spacer every
                  header after Name lines up over the wrong values. */}
              {index === 1 ? <span className="c-heart-head" /> : null}
            <button
              className={`th ${column.cls}${active ? " is-sorted" : ""}`}
              onClick={() => changeSort(nextSort(sort, option))}
              aria-label={`Sort by ${option.label}`}
            >
              {option.label}
              {active ? (
                <span className="caret">{sort === option.asc ? "▲" : "▼"}</span>
              ) : null}
            </button>
            </Fragment>
          );
        })}
        <span className="c-more-head" />
      </div>

      <div className="rows-viewport" ref={scrollRef}>
      <div className="rows" style={{height: rowVirtualizer.getTotalSize(), position: "relative"}}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const i = virtualRow.index;
          const track = tracks[i];
          return (
            <LibraryRow
              key={track.bounce_ulid}
              track={track}
              index={i}
              isPlaying={track.bounce_ulid === playingUlid}
              isPicked={picked.has(track.bounce_ulid)}
              isHearted={hearts.has(track.bounce_ulid)}
              compact={compact}
              eraColor={track.era ? eras[track.era] : undefined}
              transportPlaying={player.playing}
              selectMode={selectMode}
              dataIndex={virtualRow.index}
              measureRef={rowVirtualizer.measureElement}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                transform: `translateY(${virtualRow.start}px)`,
              }}
              onPlay={playAt}
              onTogglePick={togglePicked}
              onHeart={heart}
              onOpenSheet={openSheet}
            />
          );
        })}
      </div>
      {!loading && !tracks.length ? (
        <p className="empty">Nothing here. Try clearing a filter.</p>
      ) : null}
      </div>
      </div>
      </div>

      {/* Everything the inspector can do, for whichever row you tapped. */}
      {sheetTrack ? (
        <button
          className="sheet-scrim"
          aria-label="Close"
          onClick={() => setSheetTrack(null)}
        />
      ) : null}
      <div className={`ins-shell${sheetTrack ? " is-open" : ""}`}>
        {sheetTrack ? (
          <Inspector track={sheetTrack} onClose={() => setSheetTrack(null)} />
        ) : null}
      </div>
    </div>
  );
}
