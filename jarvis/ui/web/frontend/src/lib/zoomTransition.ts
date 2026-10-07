/**
 * A short, smooth scale from the old zoom level to the new one, so a zoom step
 * glides instead of jumping — centred on the mouse pointer.
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
 *
 * ## Zooming towards the mouse
 *
 * The pointer decides where the zoom goes in or out, the way a map or a photo
 * viewer zooms. Two things make that happen:
 *
 * * Before the step, the element under the pointer and the spot on it are
 *   remembered. After the engine reflowed the page, the scroll containers
 *   around that element are scrolled so the same spot sits under the pointer
 *   again. Fixed chrome (sidebar, title strip) has nowhere to scroll and simply
 *   reflows; scrolled content — a chat, a document, a list — stays put.
 * * The glide grows out of the pointer (`transform-origin`) instead of the
 *   top-left corner, so the content under it does not wander during the step.
 *
 * With the pointer outside the window (or never moved over it) the step keeps
 * the old behaviour: anchored at the top-left corner, where the engine anchors
 * the reflow.
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

/** Nested scroll containers walked outwards from the element under the pointer. */
const MAX_SCROLLERS = 4;

/** The spot under the pointer, taken in the layout before the step. */
export interface ZoomAnchor {
  /** Pointer position in the old CSS pixels. */
  x: number;
  y: number;
  /** The element under the pointer, if there is one. */
  target: Element | null;
  /** Where on that element the pointer was, as a fraction of its box. */
  fx: number;
  fy: number;
}

let armedUntil = 0;
let anchor: ZoomAnchor | null = null;
let pointer: { x: number; y: number } | null = null;
let running: Animation | null = null;

/** Where the pointer is over this window, or null while it is outside. */
export function setZoomPointer(position: { x: number; y: number } | null): void {
  pointer = position;
}

function captureAnchor(doc: Document): ZoomAnchor | null {
  if (!pointer) return null;
  const { x, y } = pointer;
  const target = typeof doc.elementFromPoint === "function" ? doc.elementFromPoint(x, y) : null;
  let fx = 0;
  let fy = 0;
  if (target) {
    const box = target.getBoundingClientRect();
    fx = box.width > 0 ? (x - box.left) / box.width : 0;
    fy = box.height > 0 ? (y - box.top) / box.height : 0;
  }
  return { x, y, target, fx, fy };
}

/**
 * Mark the next engine resize as the answer to a zoom this window asked for,
 * and remember what the pointer is over in the layout as it is now.
 */
export function armZoomTransition(now: number = performance.now(), doc: Document = document): void {
  // A queued step arms again before the first answer landed; the first
  // anchor still describes the layout on screen. One whose answer never
  // came (a failed request) is stale.
  if (!anchor || now > armedUntil) anchor = captureAnchor(doc);
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

function isScroller(el: Element, win: Window): boolean {
  const canX = el.scrollWidth > el.clientWidth + 1;
  const canY = el.scrollHeight > el.clientHeight + 1;
  if (!canX && !canY) return false;
  if (el === win.document.scrollingElement) return true;
  const style = win.getComputedStyle(el);
  const scrolls = (v: string) => v === "auto" || v === "scroll" || v === "overlay";
  return (canX && scrolls(style.overflowX)) || (canY && scrolls(style.overflowY));
}

/**
 * Scroll the containers around the anchored element so the remembered spot
 * sits under the pointer again (`px`, `py` in the new CSS pixels). The
 * innermost container takes what it can; the rest goes to the next one out.
 */
export function scrollAnchorUnderPointer(anchorAt: ZoomAnchor, px: number, py: number, win: Window = window): void {
  const target = anchorAt.target;
  if (!target || !target.isConnected) return;
  let scrollers = 0;
  for (let el: Element | null = target.parentElement; el && scrollers < MAX_SCROLLERS; el = el.parentElement) {
    if (!isScroller(el, win)) continue;
    scrollers += 1;
    const box = target.getBoundingClientRect();
    const dx = box.left + anchorAt.fx * box.width - px;
    const dy = box.top + anchorAt.fy * box.height - py;
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
    // "instant" so a container with `scroll-behavior: smooth` does not slide.
    el.scrollTo({ left: el.scrollLeft + dx, top: el.scrollTop + dy, behavior: "instant" as ScrollBehavior });
  }
}

/**
 * Ease the page from `from` (a scale factor) back to its real size, growing
 * out of `origin` (CSS pixels, top-left corner when omitted).
 */
export function playZoomTransition(el: HTMLElement, from: number, origin?: { x: number; y: number }): void {
  if (typeof el.animate !== "function") return;
  const start = currentScale(el) * from;
  running?.cancel();
  if (Math.abs(start - 1) < 0.002) {
    running = null;
    return;
  }
  const transformOrigin = origin ? `${Math.round(origin.x)}px ${Math.round(origin.y)}px` : "0 0";
  // A page scaled above 1 overflows the window for a moment; without this
  // the viewport would flash scrollbars while it shrinks back.
  el.style.overflow = "hidden";
  const animation = el.animate(
    [
      { transform: `scale(${start})`, transformOrigin },
      { transform: "scale(1)", transformOrigin },
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

  const onPointerMove = (event: PointerEvent) => setZoomPointer({ x: event.clientX, y: event.clientY });
  const onPointerLeave = () => setZoomPointer(null);

  const onResize = () => {
    const dpr = win.devicePixelRatio || 1;
    const inner = win.innerWidth;
    const outer = win.outerWidth;
    const before = { dpr: lastDpr, inner: lastInner, outer: lastOuter };
    lastDpr = dpr;
    lastInner = inner;
    lastOuter = outer;

    const anchorAt = anchor;
    anchor = null;
    if (performance.now() > armedUntil) return;

    // The device pixel ratio carries the zoom on Chromium and WebKit. Where it
    // does not, the CSS viewport of an unchanged window shrank or grew by the
    // same factor, which says the same thing.
    let from = 1;
    if (Math.abs(dpr - before.dpr) > 0.001) from = before.dpr / dpr;
    else if (outer === before.outer && before.inner > 0 && inner !== before.inner) from = inner / before.inner;
    if (from === 1) return;

    const root = win.document.documentElement;
    // The pointer has not moved on the screen, so in CSS pixels it moved by
    // the zoom factor.
    const origin = anchorAt ? { x: anchorAt.x * from, y: anchorAt.y * from } : undefined;
    if (anchorAt && origin) {
      // Measure without the previous step's scale still applied.
      const carried = currentScale(root);
      running?.cancel();
      scrollAnchorUnderPointer(anchorAt, origin.x, origin.y, win);
      from *= carried;
    }
    if (win.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    playZoomTransition(root, from, origin);
  };

  win.addEventListener("resize", onResize);
  win.addEventListener("pointermove", onPointerMove, { passive: true });
  win.document.documentElement.addEventListener("pointerleave", onPointerLeave);
  return () => {
    win.removeEventListener("resize", onResize);
    win.removeEventListener("pointermove", onPointerMove);
    win.document.documentElement.removeEventListener("pointerleave", onPointerLeave);
    running?.cancel();
    anchor = null;
    pointer = null;
  };
}
