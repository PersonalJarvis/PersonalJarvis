import { useEffect, useState } from "react";
import type { Rect } from "../tour/tourSteps";

/** How long a step waits for its element — the Settings page loads lazily. */
const FIND_TIMEOUT_MS = 5000;
const FIND_POLL_MS = 120;
/** Re-measure while shown: cheap, and it catches layout shifts no observer reports. */
const TRACK_MS = 400;

function visibleAnchor(anchor: string): HTMLElement | null {
  for (const el of document.querySelectorAll<HTMLElement>(`[data-tour="${anchor}"]`)) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}

/**
 * The on-screen box of the element `anchor` names, clipped to the window,
 * followed while it moves. `null` while it is not (yet) there, or when the
 * step has no anchor at all. `scrollTo` brings the element to the top of its
 * scrolling page once it appears — a Settings group further down the page.
 */
export function useAnchorRect(anchor: string | undefined, scrollTo: boolean, key: string): Rect | null {
  const [rect, setRect] = useState<Rect | null>(null);

  useEffect(() => {
    setRect(null);
    if (!anchor) return;
    let cancelled = false;
    let el: HTMLElement | null = null;
    let findTimer = 0;
    let trackTimer = 0;
    let frame = 0;
    let observer: ResizeObserver | null = null;
    const started = performance.now();

    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (cancelled) return;
        if (!el || !el.isConnected) el = visibleAnchor(anchor);
        if (!el) return;
        const r = el.getBoundingClientRect();
        // Clip to the window: a tall Settings group must not push the hole
        // (and the card placed beside it) off screen.
        const top = Math.max(r.top, 0);
        const bottom = Math.min(r.bottom, window.innerHeight);
        const next = { x: r.left, y: top, w: r.width, h: Math.max(0, bottom - top) };
        setRect((prev) =>
          prev &&
          Math.abs(prev.x - next.x) < 0.5 &&
          Math.abs(prev.y - next.y) < 0.5 &&
          Math.abs(prev.w - next.w) < 0.5 &&
          Math.abs(prev.h - next.h) < 0.5
            ? prev
            : next,
        );
      });
    };

    const find = () => {
      if (cancelled) return;
      el = visibleAnchor(anchor);
      if (!el) {
        if (performance.now() - started < FIND_TIMEOUT_MS) findTimer = window.setTimeout(find, FIND_POLL_MS);
        return;
      }
      if (scrollTo) el.scrollIntoView({ block: "start" });
      if (typeof ResizeObserver !== "undefined") {
        observer = new ResizeObserver(measure);
        observer.observe(el);
      }
      window.addEventListener("resize", measure);
      window.addEventListener("scroll", measure, true);
      trackTimer = window.setInterval(measure, TRACK_MS);
      measure();
    };
    findTimer = window.setTimeout(find, 0);

    return () => {
      cancelled = true;
      window.clearTimeout(findTimer);
      window.clearInterval(trackTimer);
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [anchor, scrollTo, key]);

  return rect;
}
