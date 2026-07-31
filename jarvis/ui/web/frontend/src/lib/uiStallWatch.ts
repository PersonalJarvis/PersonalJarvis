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

/** Upper bound on the attribution string, so one report stays one log line. */
const MAX_DETAIL = 200;

/**
 * One script that ran inside a long animation frame.
 *
 * Not in TypeScript's DOM types yet — the Long Animation Frames API is newer
 * than the bundled lib. WebView2 is Chromium, so it is present in the app even
 * though the type definitions are not.
 */
interface LoafScript {
  duration?: number;
  sourceURL?: string;
  sourceFunctionName?: string;
  invokerType?: string;
  forcedStyleAndLayoutDuration?: number;
}

interface LoafEntry extends PerformanceEntry {
  renderStart?: number;
  styleAndLayoutStart?: number;
  blockingDuration?: number;
  scripts?: LoafScript[];
}

/** ``…/assets/AgenticIdeView-xTOr0.js?x=1`` → ``AgenticIdeView-xTOr0.js``. */
function fileOf(url: string | undefined): string {
  if (!url) return "";
  const withoutQuery = url.split(/[?#]/)[0];
  const last = withoutQuery.split("/").pop() || "";
  return last.slice(0, 60);
}

/**
 * Name the code that blocked the thread, from a long *animation frame*.
 *
 * This is the field that decides the whole diagnosis, and the older `longtask`
 * entry cannot supply it: its `attribution` can only ever point at a container
 * (an iframe, or "window" for everything in the page itself), which is why
 * every stall this app has recorded reads `unknown/window` and names nothing.
 *
 * A long animation frame instead reports the individual scripts that ran, with
 * function name and source file, plus where the frame's time actually went —
 * script, style-and-layout, or the rest. That distinction matters here: with
 * several terminal panes mounted, "one handler ran for two seconds" and "layout
 * re-ran over a huge DOM" look identical to the user and need opposite fixes.
 *
 * Privacy is unchanged from the `longtask` path: code identity only — function
 * name, bundle file name, and the fixed `invokerType` enum. The `invoker`
 * string is deliberately NOT sent, because it can carry DOM ids (a terminal's
 * uuid, for instance), and no page content or user text belongs in a log line.
 */
function describeLoaf(entry: LoafEntry): string {
  const parts: string[] = [];
  const scripts = [...(entry.scripts ?? [])].sort(
    (a, b) => (b.duration ?? 0) - (a.duration ?? 0),
  );
  const worst = scripts[0];
  if (worst) {
    const where = [worst.sourceFunctionName, fileOf(worst.sourceURL)]
      .filter(Boolean)
      .join("@");
    parts.push(`script=${where || "anonymous"} ${Math.round(worst.duration ?? 0)}ms`);
    if (worst.invokerType) parts.push(`via=${worst.invokerType}`);
    const forced = Math.round(worst.forcedStyleAndLayoutDuration ?? 0);
    if (forced > 0) parts.push(`forced-layout=${forced}ms`);
    if (scripts.length > 1) parts.push(`+${scripts.length - 1}more`);
  }
  // Where the frame's time went. renderStart/styleAndLayoutStart are absolute
  // timestamps; 0 means that phase never began, so it is not a duration.
  const { startTime, duration, renderStart, styleAndLayoutStart } = entry;
  if (renderStart) {
    parts.push(`work=${Math.round(renderStart - startTime)}ms`);
    const renderEnd = startTime + duration;
    if (styleAndLayoutStart) {
      parts.push(`render=${Math.round(styleAndLayoutStart - renderStart)}ms`);
      parts.push(`layout=${Math.round(renderEnd - styleAndLayoutStart)}ms`);
    } else {
      parts.push(`render=${Math.round(renderEnd - renderStart)}ms`);
    }
  }
  if (entry.blockingDuration) {
    parts.push(`blocking=${Math.round(entry.blockingDuration)}ms`);
  }
  return parts.join(" ").slice(0, MAX_DETAIL);
}

/** Fixed labels only — no page content, no user text, ever leaves here. */
function describe(entry: PerformanceEntry): string {
  if (entry.entryType === "long-animation-frame") {
    return describeLoaf(entry as LoafEntry);
  }
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
      const blocking = list.getEntries().filter((e) => e.duration >= REPORT_OVER_MS);
      if (!blocking.length) return;
      const now = Date.now();
      if (now - lastReportAt < MIN_GAP_MS) return;
      // One block can surface as both a long task and a long animation frame.
      // Report once, choosing the entry that can actually name the culprit —
      // otherwise the attributed version loses a coin toss to the useless one.
      const best = blocking.sort((a, b) => {
        const named =
          Number(b.entryType === "long-animation-frame") -
          Number(a.entryType === "long-animation-frame");
        return named !== 0 ? named : b.duration - a.duration;
      })[0];
      lastReportAt = now;
      report({
        blocked_ms: Math.round(best.duration),
        // The route the app is on, not its contents — enough to tell a stall
        // in the Agentic IDE apart from one anywhere else.
        at: (location.hash || location.pathname || "").slice(0, 60),
        panes: paneCount(),
        detail: describe(best),
      });
    });
    // Long animation frames first: they carry the script attribution. `longtask`
    // stays because it is the older, more widely supported type and it also
    // catches a block that never reached a rendering update. An engine missing
    // either one throws only for that one.
    let watching = 0;
    for (const type of ["long-animation-frame", "longtask"]) {
      try {
        observer.observe({ type, buffered: true });
        watching += 1;
      } catch {
        // This engine lacks this entry type; the other may still work.
      }
    }
    if (watching === 0) throw new TypeError("no supported stall entry type");
  } catch {
    // Nothing observable in this engine — nothing to watch, nothing broken.
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
