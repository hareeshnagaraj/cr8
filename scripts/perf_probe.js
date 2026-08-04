// In-page instrumentation for the speed budget.
//
// Injected into the running app, it watches the three things that actually
// describe how the app feels and reports numbers a build can fail on.
//
// The measurement that matters is keystroke to repaint. Two traps make that
// number lie if you are not careful, and both are handled here:
//
//   Long Animation Frames only fire for frames that take over 50ms, and Event
//   Timing only reports interactions over its threshold. Both are jank
//   detectors: on an app that is already fast they report nothing at all, and a
//   probe that measures nothing passes everything. So typical latency is timed
//   directly - keydown timestamp to the paint that follows the update - and
//   LoAF is kept purely as the blocking-time alarm.
//
//   Synthetic events - anything dispatched from JavaScript - are marked
//   isTrusted: false and are given no interactionId, so they never appear in
//   Event Timing at all. The driver has to send real keystrokes.
//
// window.__cr8probe is the handle the driver talks to.

(() => {
  if (window.__cr8probe) {
    window.__cr8probe.reset();
    return "already installed";
  }

  const state = {
    loaf: [],      // long animation frames
    events: [],    // event timing entries
    shifts: 0,     // cumulative layout shift
    frames: null,  // frame sampling result
    typed: [],     // measured keystroke -> paint, in ms
  };

  const observe = (type, handler, extra = {}) => {
    try {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) handler(entry);
      });
      observer.observe({type, buffered: true, ...extra});
      return observer;
    } catch {
      // An unsupported entry type must not take the whole probe down; the
      // driver reports which observers actually attached.
      return null;
    }
  };

  const observers = {
    loaf: observe("long-animation-frame", (entry) => {
      state.loaf.push({
        start: entry.startTime,
        duration: entry.duration,
        blocking: entry.blockingDuration ?? 0,
        // The honest keystroke number: from the input that caused this frame
        // to the frame being presented.
        inputToRender:
          entry.firstUIEventTimestamp > 0
            ? entry.startTime + entry.duration - entry.firstUIEventTimestamp
            : null,
      });
    }),
    event: observe(
      "event",
      (entry) => {
        state.events.push({
          name: entry.name,
          duration: entry.duration,
          processing: entry.processingEnd - entry.processingStart,
          interactionId: entry.interactionId ?? 0,
        });
      },
      {durationThreshold: 16},
    ),
    shift: observe("layout-shift", (entry) => {
      if (!entry.hadRecentInput) state.shifts += entry.value;
    }),
  };

  // Keystroke to paint, measured directly.
  //
  // A double requestAnimationFrame lands after the frame carrying React's
  // update has been composited, so the delta from the event timestamp is what
  // a person actually waited to see their search narrow.
  function watchKeystrokes() {
    document.addEventListener(
      "keydown",
      (event) => {
        if (!event.isTrusted) return; // synthetic events are not measurable
        const at = event.timeStamp;
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            state.typed.push(Number((performance.now() - at).toFixed(1)));
          }),
        );
      },
      {capture: true},
    );
  }

  // Count frames actually presented over a window, which is how you see
  // dropped frames rather than inferring them.
  function sampleFrames(ms) {
    return new Promise((resolve) => {
      const start = performance.now();
      let count = 0;
      let worst = 0;
      let previous = start;
      const tick = (now) => {
        const gap = now - previous;
        if (gap > worst) worst = gap;
        previous = now;
        count += 1;
        if (now - start < ms) {
          requestAnimationFrame(tick);
        } else {
          const seconds = (now - start) / 1000;
          const expected = seconds * (state.refreshHz || 60);
          resolve({
            seconds: Number(seconds.toFixed(2)),
            frames: count,
            fps: Number((count / seconds).toFixed(1)),
            droppedPercent: Number(
              (Math.max(0, (expected - count) / expected) * 100).toFixed(1),
            ),
            worstGapMs: Number(worst.toFixed(1)),
          });
        }
      };
      requestAnimationFrame(tick);
    });
  }

  // What this display can actually do, so dropped frames mean something.
  function detectRefresh() {
    return new Promise((resolve) => {
      let count = 0;
      const start = performance.now();
      const tick = () => {
        count += 1;
        if (performance.now() - start < 500) requestAnimationFrame(tick);
        else {
          state.refreshHz = Math.round(count / ((performance.now() - start) / 1000));
          resolve(state.refreshHz);
        }
      };
      requestAnimationFrame(tick);
    });
  }

  window.__cr8probe = {
    reset() {
      state.loaf.length = 0;
      state.events.length = 0;
      state.shifts = 0;
      state.frames = null;
      state.typed.length = 0;
    },
    attached: Object.fromEntries(
      Object.entries(observers).map(([key, value]) => [key, Boolean(value)]),
    ),
    detectRefresh,
    async fling(ms = 2000) {
      if (!state.refreshHz) await detectRefresh();
      const viewport = document.querySelector(".rows-viewport");
      if (!viewport) return {error: "no .rows-viewport"};
      const sampling = sampleFrames(ms);
      // Drive the scroll from rAF so it competes with rendering the way a real
      // finger does, rather than jumping in one step.
      const started = performance.now();
      const step = (now) => {
        const elapsed = now - started;
        viewport.scrollTop = (elapsed / ms) * viewport.scrollHeight * 0.5;
        if (elapsed < ms) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
      state.frames = await sampling;
      return state.frames;
    },
    report() {
      const keystrokes = state.loaf.filter((f) => f.inputToRender !== null);
      return {
        attached: window.__cr8probe.attached,
        refreshHz: state.refreshHz || null,
        keystrokeSamples: state.typed.length,
        // Directly measured; LoAF stays below as the jank alarm only.
        inputToRender: state.typed.slice(),
        blocking: state.loaf.map((f) => Number(f.blocking.toFixed(1))),
        jankFrames: keystrokes.length,
        longestFrameMs: state.loaf.length
          ? Number(Math.max(...state.loaf.map((f) => f.duration)).toFixed(1))
          : 0,
        slowEvents: state.events
          .filter((e) => e.interactionId > 0)
          .map((e) => ({name: e.name, duration: e.duration})),
        cls: Number(state.shifts.toFixed(4)),
        frames: state.frames,
      };
    },
  };

  watchKeystrokes();
  detectRefresh();
  return "installed";
})();
