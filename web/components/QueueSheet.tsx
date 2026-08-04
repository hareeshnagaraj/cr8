"use client";

import {
  memo, useEffect, useMemo, useRef, useState,
  type PointerEvent as ReactPointerEvent,
} from "react";

import type {Track} from "./PlayerProvider";

const INITIAL_LIMIT = 60;

type QueueItem = {
  queueIndex: number;
  track: Track;
};

type DragState = {
  pointerId: number;
  from: number;
  to: number;
};

type QueueSheetProps = {
  open: boolean;
  queue: Track[];
  index: number;
  current: Track | null;
  repeat: "off" | "all";
  eras: Record<string, string>;
  onClose: () => void;
  cycleRepeat: () => void;
  jumpTo: (index: number) => void;
  move: (from: number, to: number) => void;
  removeAt: (index: number) => void;
};

function trackMeta(track: Track) {
  return [
    track.key_canon,
    track.bpm ? `${Math.round(track.bpm)} bpm` : null,
  ].filter(Boolean).join(" · ") || "—";
}

function reorder(items: QueueItem[], from: number, to: number) {
  const fromOffset = items.findIndex((item) => item.queueIndex === from);
  if (fromOffset < 0) return items;
  const toOffset = Math.max(0, Math.min(to - items[0].queueIndex, items.length - 1));
  const reordered = items.slice();
  const [item] = reordered.splice(fromOffset, 1);
  reordered.splice(toOffset, 0, item);
  return reordered;
}

export const QueueSheet = memo(function QueueSheet({
  open,
  queue,
  index,
  current,
  repeat,
  eras,
  onClose,
  cycleRepeat,
  jumpTo,
  move,
  removeAt,
}: QueueSheetProps) {
  const [showAll, setShowAll] = useState(false);
  const [drag, setDrag] = useState<DragState | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      setShowAll(false);
      setDrag(null);
      dragRef.current = null;
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, open]);

  const upcoming = useMemo<QueueItem[]>(() => {
    if (!current || index < 0) return [];
    return queue.slice(index + 1).map((track, offset) => ({
      queueIndex: index + offset + 1,
      track,
    }));
  }, [current, index, queue]);

  const ordered = useMemo(
    () => (drag && upcoming.length ? reorder(upcoming, drag.from, drag.to) : upcoming),
    [drag, upcoming],
  );
  const visible = showAll ? ordered : ordered.slice(0, INITIAL_LIMIT);

  function startDrag(event: ReactPointerEvent<HTMLButtonElement>, queueIndex: number) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    document.getSelection()?.removeAllRanges();
    const next = {pointerId: event.pointerId, from: queueIndex, to: queueIndex};
    dragRef.current = next;
    setDrag(next);
  }

  function updateDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    event.preventDefault();
    const rows = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>("[data-queue-position]") ?? [],
    );
    const before = rows.find(
      (row) => Number(row.dataset.queuePosition) === active.to - 1,
    );
    const after = rows.find(
      (row) => Number(row.dataset.queuePosition) === active.to + 1,
    );
    let to = active.to;
    if (before) {
      const bounds = before.getBoundingClientRect();
      if (event.clientY < bounds.top + bounds.height / 2) to -= 1;
    }
    if (to === active.to && after) {
      const bounds = after.getBoundingClientRect();
      if (event.clientY > bounds.top + bounds.height / 2) to += 1;
    }
    if (to === active.to) return;
    const next = {...active, to};
    dragRef.current = next;
    setDrag(next);
  }

  function finishDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    setDrag(null);
    if (active.from !== active.to) move(active.from, active.to);
  }

  function cancelDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDrag(null);
  }

  return (
    <>
      {open ? (
        <button
          className="sheet-scrim queue-scrim"
          aria-label="Close queue"
          onClick={onClose}
        />
      ) : null}
      <aside
        className={`queue-sheet${open ? " is-open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="Queue"
        aria-hidden={!open}
        inert={!open ? true : undefined}
      >
        <div className="queue-grabber" aria-hidden="true" />
        <header className="queue-head">
          <div>
            <h2>Queue</h2>
            <span className="queue-count num">{upcoming.length} up next</span>
          </div>
          <div className="queue-head-actions">
            <button
              className={`queue-repeat${repeat === "all" ? " is-on" : ""}`}
              aria-pressed={repeat === "all"}
              onClick={cycleRepeat}
            >
              Repeat {repeat}
            </button>
            <button className="queue-close" onClick={onClose} aria-label="Close queue">
              ×
            </button>
          </div>
        </header>

        {current ? (
          <section className="queue-now" aria-label="Now playing">
            <i
              className="era-dot"
              style={{background: current.era ? eras[current.era] ?? "var(--t3)" : "var(--t3)"}}
            />
            <div>
              <span className="queue-kicker">Now playing</span>
              <strong>{current.title}</strong>
              <span className="queue-meta num">{trackMeta(current)}</span>
            </div>
          </section>
        ) : null}

        {current ? <h3 className="queue-section-title">Up next</h3> : null}
        <div className="queue-list" ref={listRef}>
          {visible.map((item, position) => (
            <div
              className={`queue-row${drag?.from === item.queueIndex ? " is-dragging" : ""}`}
              key={`${item.track.bounce_ulid}-${item.queueIndex}`}
              data-queue-position={index + position + 1}
            >
              <button
                className="queue-handle"
                aria-label={`Reorder ${item.track.title}`}
                onPointerDown={(event) => startDrag(event, item.queueIndex)}
                onPointerMove={updateDrag}
                onPointerUp={finishDrag}
                onPointerCancel={cancelDrag}
              >
                ≡
              </button>
              <button className="queue-track" onClick={() => jumpTo(item.queueIndex)}>
                <span>{item.track.title}</span>
                <span className="queue-meta num">{trackMeta(item.track)}</span>
              </button>
              <button
                className="queue-remove"
                aria-label={`Remove ${item.track.title} from queue`}
                onClick={() => removeAt(item.queueIndex)}
              >
                ×
              </button>
            </div>
          ))}
          {!upcoming.length ? (
            <p className="queue-empty">Nothing queued. Play something.</p>
          ) : null}
        </div>
        {!showAll && upcoming.length > INITIAL_LIMIT ? (
          <button className="queue-show-all" onClick={() => setShowAll(true)}>
            Show all {upcoming.length}
          </button>
        ) : null}
      </aside>
    </>
  );
});
