/**
 * A short, smooth scale from the old zoom level to the new one, so a zoom step
 * glides instead of jumping.
 *
 * ## How it stays in sync with the engine
 *
 * The zoom itself is the WebView engine's (see hooks/useAppZoom), and it lands
 * whenever the desktop shell gets round to it — a round trip later. Animating
 * on the keypress would run ahead of it and then jump a second time when the
 * engine catches up. So the animation starts from the engine's own signal
 * instead: an engine zoom resizes the CSS viewport, which fires `resize` in the
 * same rendering step that lays the page out at the new size, BEFORE that
 * frame is painted. Setting the starting scale there means the first painted
 * frame already looks like the old size, and the page then eases to the new
 * one. Nothing is ever drawn at the wrong size.
 *
 * The step is FLIP-style: the page is already laid out at the new level and is
 * scaled back to how big it looked before (old / new), then released to 1.
 * The origin is the top-left corner, which is also where the engine anchors
 * the reflow, so the sidebar and the title strip grow from where they already
 * are instead of sliding.
 *
 * A held-down key fires steps faster than the animation runs. Each new step
 * starts from the scale currently on screen, so a run of steps reads as one
 * continuous glide rather than a series of restarts.
 *
 * Only armed around a zoom this window asked for: a `resize` from dragging
 * the window edge, or from moving it to a screen with a different DPI, also
 * changes the numbers below and must not animate.
 */

/** Long enough to feel like motion, short enough that a step never lags the key. */
export const ZOOM_TRANSITION_MS = 200;
const EASING = "cubic-bezier(0.22, 1, 0.36, 1)";

/** How long after a request an engine resize still counts as its answer. */
const ARM_WINDOW_MS = 1500;

let armedUntil = 0;
let running: Animation | null = null;

/** Mark the next engine resize as the answer to a zoom this window asked for. */
export function armZoomTransition(now: number = performance.now()): void {
  armedUntil = now + ARM_WINDOW_MS;
}

/** The scale the root element is drawn at right now (1 when nothing runs). */
function currentScale(el: HTMLElement): number {
  if (!running) return 1;
  const transform = getComputedStyle(el).transform;
  const match = /^matrix\(([-\d.e]+)/.exec(transform ?? "");
  const scale = match ? Number(match[1]) : 1;
  return Number.isFinite(scale) && scale > 0 ? scale : 1;
}

/** Ease the page from `from` (a scale factor) back to its real size. */
export function playZoomTransition(el: HTMLElement, from: number): void {
  if (typeof el.animate !== "function") return;
  const start = currentScale(el) * from;
  running?.cancel();
  if (Math.abs(start - 1) < 0.002) {
    running = null;
    return;
  }
  // A page scaled above 1 overflows the window for a moment; without this
  // the viewport would flash scrollbars while it shrinks back.
  el.style.overflow = "hidden";
  const animation = el.animate(
    [
      { transform: `scale(${start})`, transformOrigin: "0 0" },
      { transform: "scale(1)", transformOrigin: "0 0" },
    ],
    { duration: ZOOM_TRANSITION_MS, easing: EASING },
  );
  running = animation;
  const settle = () => {
    if (running !== animation) return;
    running = null;
    el.style.overflow = "";
  };
  animation.onfinish = settle;
  animation.oncancel = settle;
}

/**
 * Watch `win` for engine zoom steps and animate each one, returning a cleanup
 * function.
 */
export function installZoomTransition(win: Window = window): () => void {
  let lastDpr = win.devicePixelRatio || 1;
  let lastInner = win.innerWidth;
  let lastOuter = win.outerWidth;

  const onResize = () => {
    const dpr = win.devicePixelRatio || 1;
    const inner = win.innerWidth;
    const outer = win.outerWidth;
    const before = { dpr: lastDpr, inner: lastInner, outer: lastOuter };
    lastDpr = dpr;
    lastInner = inner;
    lastOuter = outer;

    if (performance.now() > armedUntil) return;
    if (win.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

    // The device pixel ratio carries the zoom on Chromium and WebKit. Where it
    // does not, the CSS viewport of an unchanged window shrank or grew by the
    // same factor, which says the same thing.
    let from = 1;
    if (Math.abs(dpr - before.dpr) > 0.001) from = before.dpr / dpr;
    else if (outer === before.outer && before.inner > 0 && inner !== before.inner) from = inner / before.inner;
    if (from === 1) return;
    playZoomTransition(win.document.documentElement, from);
  };

  win.addEventListener("resize", onResize);
  return () => {
    win.removeEventListener("resize", onResize);
    running?.cancel();
  };
}
