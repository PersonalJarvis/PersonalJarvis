import { useCallback, useEffect, useRef, useState } from "react";

/**
 * "Follow the newest, unless the reader is reading" — the scrolling rule every
 * conversation surface owes its reader.
 *
 * The wrong version is one line and looks right: scroll to the end whenever
 * anything arrives. It reads fine while you watch an answer come in and is
 * unusable the moment you want to look back — a streaming answer fires an
 * update per token, so each attempt to scroll up is undone before the next
 * frame. The rule instead: new output pulls the view along ONLY while the view
 * is already at the end. Scrolled up, the reader keeps their place, and
 * `atEnd` turns false so the surface can offer a way back.
 *
 * Use it for anything that GROWS while being read — chat threads, transcripts,
 * live logs. A view that only ever lands at its end once (a stored thread
 * opened for reading) does not need this; a plain scroll on load says that
 * more honestly.
 *
 *   const { rootRef, contentRef, atEnd, jumpToEnd } = useStickToBottom();
 *   <ScrollArea ref={rootRef}><div ref={contentRef}>…</div></ScrollArea>
 *   {!atEnd && <button onClick={jumpToEnd}>…</button>}
 *
 * `rootRef` goes on the scrolling element or on a Radix ScrollArea root —
 * either is resolved. `contentRef` goes on the growing child, which is what
 * makes an answer that grows without a new item still pull the view along;
 * where the platform has no ResizeObserver, call `follow()` from an effect of
 * your own instead.
 */
export function useStickToBottom() {
  // A ref AND a state for the same node on purpose: `follow` must read the
  // current element synchronously inside a layout effect, while the listeners
  // below have to re-attach when the element appears or is replaced — which
  // only a state dependency can trigger.
  const rootElRef = useRef<HTMLElement | null>(null);
  const [rootEl, setRootEl] = useState<HTMLElement | null>(null);
  const contentElRef = useRef<HTMLElement | null>(null);
  const [contentEl, setContentEl] = useState<HTMLElement | null>(null);
  const stickRef = useRef(true);
  const [atEnd, setAtEnd] = useState(true);
  // Layout growth (a live thought, a tool row) is taller than NEAR_END_PX in
  // one frame. Overflow anchoring then fires `scroll` with the OLD top —
  // before any resize observer can pin — and `isNearEnd` would lie that the
  // reader moved away. Remembering the last size lets that event re-pin
  // instead of unsticking; a real scroll-up still unsticks.
  const lastHeightRef = useRef(0);
  const lastTopRef = useRef(0);
  // Smooth "jump to end" emits many `scroll` events that are not near the
  // end yet. Those must not clear the stick, or the next reasoning token
  // leaves the reader stranded mid-animation.
  const jumpingRef = useRef(false);
  const jumpTimerRef = useRef(0);

  const rootRef = useCallback((node: HTMLElement | null) => {
    rootElRef.current = node;
    setRootEl(node);
  }, []);
  const contentRef = useCallback((node: HTMLElement | null) => {
    contentElRef.current = node;
    setContentEl(node);
  }, []);

  const pinToEnd = (viewport: HTMLElement) => {
    viewport.scrollTop = viewport.scrollHeight;
    lastHeightRef.current = viewport.scrollHeight;
    lastTopRef.current = viewport.scrollTop;
  };

  /** Pull the view to the end — but only if it was already there. */
  const follow = useCallback(() => {
    const viewport = scrollViewportOf(rootElRef.current);
    if (!viewport || !stickRef.current) return;
    pinToEnd(viewport);
  }, []);

  /** Take the reader back to the end, and follow again from there. */
  const jumpToEnd = useCallback(() => {
    const viewport = scrollViewportOf(rootElRef.current);
    if (!viewport) return;
    stickRef.current = true;
    setAtEnd(true);
    if (!prefersReducedMotion() && typeof viewport.scrollTo === "function") {
      jumpingRef.current = true;
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
      window.clearTimeout(jumpTimerRef.current);
      jumpTimerRef.current = window.setTimeout(() => {
        if (!jumpingRef.current) return;
        jumpingRef.current = false;
        pinToEnd(viewport);
        setAtEnd(true);
      }, 400);
    } else {
      pinToEnd(viewport);
    }
  }, []);

  // One listener answers both questions: does new output pull the view along,
  // and does the surface need to offer a way back.
  useEffect(() => {
    const viewport = scrollViewportOf(rootEl);
    if (!viewport) return;
    const previousAnchor = viewport.style.overflowAnchor;
    viewport.style.overflowAnchor = "none";
    const read = () => {
      if (jumpingRef.current) {
        stickRef.current = true;
        if (isNearEnd(viewport.scrollTop, viewport.scrollHeight, viewport.clientHeight)) {
          jumpingRef.current = false;
          pinToEnd(viewport);
          setAtEnd(true);
        }
        return;
      }
      const height = viewport.scrollHeight;
      const top = viewport.scrollTop;
      // A first observation (height still 0) is not growth — jsdom and a
      // just-mounted pane both start there. Treating it as growth would pin
      // a reader who opened the thread mid-way.
      const hadLayout = lastHeightRef.current > 0;
      const grew = hadLayout && height > lastHeightRef.current;
      const scrolledUp = hadLayout && top + 1 < lastTopRef.current;
      // Content grew while we were following, and the reader did not scroll
      // up: this `scroll` is layout, not a choice. Stay stuck and pin.
      if (stickRef.current && grew && !scrolledUp) {
        pinToEnd(viewport);
        setAtEnd(true);
        return;
      }
      lastHeightRef.current = height;
      lastTopRef.current = top;
      const near = isNearEnd(top, height, viewport.clientHeight);
      stickRef.current = near;
      setAtEnd(near);
    };
    read();
    viewport.addEventListener("scroll", read, { passive: true });
    const onScrollEnd = () => {
      if (!jumpingRef.current) return;
      jumpingRef.current = false;
      if (stickRef.current) pinToEnd(viewport);
      setAtEnd(true);
    };
    viewport.addEventListener("scrollend", onScrollEnd);
    return () => {
      viewport.removeEventListener("scroll", read);
      viewport.removeEventListener("scrollend", onScrollEnd);
      viewport.style.overflowAnchor = previousAnchor;
      window.clearTimeout(jumpTimerRef.current);
    };
  }, [rootEl]);

  // An answer grows WITHOUT a new item arriving, so no render-driven effect
  // fires for it; the content's own size is the honest signal.
  useEffect(() => {
    const viewport = scrollViewportOf(rootEl);
    if (!viewport || !contentEl || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (!stickRef.current) return;
      pinToEnd(viewport);
      setAtEnd(true);
    });
    ro.observe(contentEl);
    return () => ro.disconnect();
  }, [rootEl, contentEl]);

  return { rootRef, contentRef, atEnd, jumpToEnd, follow };
}

/**
 * Within this many pixels of the bottom counts as "at the end", so sub-pixel
 * rounding — or the half line a growing answer adds between two frames —
 * never reads as "they scrolled away".
 */
export const NEAR_END_PX = 72;

export function isNearEnd(scrollTop: number, scrollHeight: number, clientHeight: number): boolean {
  return scrollHeight - scrollTop - clientHeight <= NEAR_END_PX;
}

/** Radix renders the scrolling element as a viewport inside the ScrollArea root. */
export function scrollViewportOf(root: HTMLElement | null): HTMLElement | null {
  if (!root) return null;
  return (root.querySelector("[data-radix-scroll-area-viewport]") as HTMLElement | null) ?? root;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}
