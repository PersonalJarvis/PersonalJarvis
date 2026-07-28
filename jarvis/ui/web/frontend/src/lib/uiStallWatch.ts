/**
 * Notice when the browser's main thread stops answering — and say so out loud.
 *
 * The desktop app is a WebView with no address bar, no dev tools and no
 * console, so a blocked main thread is completely invisible from the inside.
 * What the user sees is the window title turning into "Not responding", clicks
 * landing late, and characters appearing in a terminal pane seconds after they
 * were typed — all three being the same event: one JavaScript task ran long
 * enough that nothing else, input included, could be serviced.
 *
 * The backend already reports its own stalls from a thread the loop cannot
 * block (`jarvis/core/loop_watchdog.py`). This is the same instrument for the
 * half that draws the window, and it exists because that half is the one that
 * survived every measurement on 2026-07-28: backend loop lag, CPU contention
 * from other processes, GIL pressure inside the app, terminal frame rate,
 * xterm write cost and reflow cost were each measured and each came back
 * clean, while the window kept freezing during real use.
 *
 * Cost when nothing is wrong: one registered observer that is never called.
 * `longtask` entries are only emitted for tasks already over ~50 ms, so this
 * cannot itself become the thing that slows the page down.
 */

/**
 * Report tasks longer than this, in milliseconds.
 *
 * Well above the ~50 ms the browser already considers "long": a garbage
 * collection pause, a big terminal write and a pane reflow all land in the low
 * hundreds and are survivable. What is being hunted is the multi-second block
 * that makes Windows draw the ghost window, so the bar sits high enough that a
 * quiet session reports nothing at all.
 */
const REPORT_OVER_MS = 1000;

/**
 * Never send more than one report per this window.
 *
 * A thread that is blocking repeatedly would otherwise report on every block,
 * and each report is itself a request the same thread has to make. One line a
 * minute is enough to establish what is happening.
 */
const MIN_GAP_MS = 60_000;

/** Fixed labels only — no page content, no user text, ever leaves here. */
function describe(entry: PerformanceEntry): string {
  const attribution = (entry as PerformanceEntry & {
    attribution?: Array<{ name?: string; containerType?: string; containerName?: string }>;
  }).attribution;
  if (!attribution?.length) return "";
  const first = attribution[0];
  return [first.name, first.containerType, first.containerName]
    .filter(Boolean)
    .join("/")
    .slice(0, 120);
}

/** How many terminal panes are mounted right now, as the DOM knows it. */
function paneCount(): number {
  try {
    return document.querySelectorAll("[data-testid^='agentic-terminal-host-']").length;
  } catch {
    return 0;
  }
}

/**
 * Start watching. Returns a stop function; safe to call in any environment —
 * an engine without `PerformanceObserver` or without the `longtask` entry type
 * simply gets a no-op rather than a crash.
 */
export function watchUiStalls(
  report: (payload: {
    blocked_ms: number;
    at: string;
    panes: number;
    detail: string;
  }) => void,
): () => void {
  if (typeof PerformanceObserver === "undefined") return () => {};

  let lastReportAt = 0;
  let observer: PerformanceObserver;
  try {
    observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.duration < REPORT_OVER_MS) continue;
        const now = Date.now();
        if (now - lastReportAt < MIN_GAP_MS) continue;
        lastReportAt = now;
        report({
          blocked_ms: Math.round(entry.duration),
          // The route the app is on, not its contents — enough to tell a stall
          // in the Agentic IDE apart from one anywhere else.
          at: (location.hash || location.pathname || "").slice(0, 60),
          panes: paneCount(),
          detail: describe(entry),
        });
      }
    });
    observer.observe({ type: "longtask", buffered: true });
  } catch {
    // Entry type unsupported in this engine — nothing to watch, nothing broken.
    return () => {};
  }

  return () => {
    try {
      observer.disconnect();
    } catch {
      /* already gone */
    }
  };
}
