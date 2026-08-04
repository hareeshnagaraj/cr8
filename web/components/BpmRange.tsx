"use client";

import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";

type BpmRangeProps = {
  low: number;
  high: number;
  min: number;
  max: number;
  histogram: number[];
  onChange: (low: number, high: number) => void;
};

type WindowDrag = {
  startX: number;
  width: number;
  low: number;
  high: number;
  moved: boolean;
};

// The face is isolated from the popover and the library page. Search typing can
// rerender both without redrawing these bars; only a new histogram does that.
const TempoSparkline = memo(function TempoSparkline({
  histogram,
}: {
  histogram: number[];
}) {
  const peak = Math.max(1, ...histogram);
  return (
    <span className="tempo-sparkline" aria-hidden="true">
      {histogram.map((count, index) => (
        <i
          key={index}
          style={{height: `${Math.max(10, (count / peak) * 100)}%`}}
        />
      ))}
    </span>
  );
});

export function BpmPopover(props: BpmRangeProps) {
  const {low, high, min, max, histogram} = props;
  const [open, setOpen] = useState(false);
  const [flipped, setFlipped] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const touched = low > min || high < max;

  function close(restoreFocus = false) {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => triggerRef.current?.focus());
  }

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !panelRef.current) return;
    setFlipped(panelRef.current.getBoundingClientRect().bottom > window.innerHeight);
  }, [open]);

  return (
    <div
      className="menu tempo-menu"
      ref={wrapperRef}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !open) return;
        event.preventDefault();
        event.stopPropagation();
        close(true);
      }}
    >
      <button
        ref={triggerRef}
        className={`menu-trigger tempo-trigger${touched ? " is-active" : ""}`}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          if (open) close();
          else {
            setFlipped(false);
            setOpen(true);
          }
        }}
      >
        <TempoSparkline histogram={histogram} />
        <span className="tempo-trigger-value num">
          {touched ? `${low}–${high}` : "any"}
        </span>
      </button>
      {open ? (
        <div
          ref={panelRef}
          className={`menu-panel tempo-panel${flipped ? " is-flipped" : ""}`}
          role="dialog"
          aria-label="Tempo range"
        >
          <BpmRange {...props} />
        </div>
      ) : null}
    </div>
  );
}

// Two native range inputs preserve keyboard and touch semantics. Their tracks
// do not receive pointer events; only the enlarged thumbs do. The histogram and
// selected window supply the direct-manipulation behavior around them.
export function BpmRange({
  low,
  high,
  min,
  max,
  histogram,
  onChange,
}: BpmRangeProps) {
  const peak = useMemo(() => Math.max(1, ...histogram), [histogram]);
  const span = Math.max(1, max - min);
  const leftPercent = ((low - min) / span) * 100;
  const rightPercent = ((high - min) / span) * 100;
  const touched = low > min || high < max;
  const plotRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<WindowDrag | null>(null);
  const [activeThumb, setActiveThumb] = useState<"low" | "high" | null>(null);
  const [windowDragging, setWindowDragging] = useState(false);

  function jumpNearest(clientX: number, width?: number, left?: number) {
    const rect = plotRef.current?.getBoundingClientRect();
    const plotWidth = width ?? rect?.width ?? 0;
    const plotLeft = left ?? rect?.left ?? 0;
    if (!plotWidth) return;
    const ratio = Math.min(1, Math.max(0, (clientX - plotLeft) / plotWidth));
    const value = Math.round(min + ratio * span);
    if (Math.abs(value - low) <= Math.abs(value - high)) {
      onChange(Math.min(value, high), high);
    } else {
      onChange(low, Math.max(value, low));
    }
  }

  function startWindowDrag(event: ReactPointerEvent<HTMLSpanElement>) {
    if (event.button !== 0) return;
    event.stopPropagation();
    const rect = plotRef.current?.getBoundingClientRect();
    if (!rect?.width) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      startX: event.clientX,
      width: rect.width,
      low,
      high,
      moved: false,
    };
    setWindowDragging(true);
  }

  function moveWindow(event: ReactPointerEvent<HTMLSpanElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const deltaPixels = event.clientX - drag.startX;
    if (Math.abs(deltaPixels) > 2) drag.moved = true;
    const delta = Math.round((deltaPixels / drag.width) * span);
    const rangeSpan = drag.high - drag.low;
    const nextLow = Math.max(min, Math.min(max - rangeSpan, drag.low + delta));
    onChange(nextLow, nextLow + rangeSpan);
  }

  function finishWindowDrag(event: ReactPointerEvent<HTMLSpanElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    if (!drag.moved) {
      const rect = plotRef.current?.getBoundingClientRect();
      jumpNearest(event.clientX, drag.width, rect?.left);
    }
    dragRef.current = null;
    setWindowDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  const coincidentTop =
    low === high
      ? low <= min
        ? "high"
        : high >= max
          ? "low"
          : activeThumb ?? "high"
      : null;

  return (
    <div className="bpm">
      <div className="bpm-head">
        <span className="field-label">Tempo</span>
        <span className="bpm-value num">
          {touched ? `${low}–${high}` : "any"}
          {touched ? (
            <button
              className="bpm-clear"
              type="button"
              onClick={() => onChange(min, max)}
              aria-label="Clear the tempo range"
            >
              ×
            </button>
          ) : null}
        </span>
      </div>

      <div
        ref={plotRef}
        className="bpm-plot"
        aria-label="Tempo histogram. Click to move the nearest range handle."
        onPointerDown={(event) => {
          if (event.button === 0) jumpNearest(event.clientX);
        }}
      >
        {histogram.map((count, index) => {
          const at = min + (index / histogram.length) * span;
          const inside = at >= low && at <= high;
          return (
            <i
              key={index}
              className={`bpm-bar${inside ? " is-in" : ""}`}
              style={{height: `${Math.max(6, (count / peak) * 100)}%`}}
            />
          );
        })}
        <span
          className={`bpm-shade${windowDragging ? " is-dragging" : ""}`}
          style={{left: `${leftPercent}%`, right: `${100 - rightPercent}%`}}
          onPointerDown={startWindowDrag}
          onPointerMove={moveWindow}
          onPointerUp={finishWindowDrag}
          onPointerCancel={() => {
            dragRef.current = null;
            setWindowDragging(false);
          }}
        />
      </div>

      <div className="bpm-rails">
        <input
          className={`bpm-rail${activeThumb === "low" ? " is-active" : ""}`}
          style={{zIndex: coincidentTop === "low" ? 3 : 1}}
          type="range"
          min={min}
          max={max}
          step={1}
          value={low}
          aria-label="Slowest tempo"
          onPointerDown={() => setActiveThumb("low")}
          onPointerUp={() => setActiveThumb(null)}
          onPointerCancel={() => setActiveThumb(null)}
          onBlur={() => setActiveThumb(null)}
          onChange={(event) =>
            onChange(Math.min(Number(event.target.value), high), high)
          }
        />
        <input
          className={`bpm-rail${activeThumb === "high" ? " is-active" : ""}`}
          style={{zIndex: coincidentTop === "high" ? 3 : 2}}
          type="range"
          min={min}
          max={max}
          step={1}
          value={high}
          aria-label="Fastest tempo"
          onPointerDown={() => setActiveThumb("high")}
          onPointerUp={() => setActiveThumb(null)}
          onPointerCancel={() => setActiveThumb(null)}
          onBlur={() => setActiveThumb(null)}
          onChange={(event) =>
            onChange(low, Math.max(Number(event.target.value), low))
          }
        />
      </div>
    </div>
  );
}
