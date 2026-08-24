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

  const rootRef = useCallback((node: HTMLElement | null) => {
    rootElRef.current = node;
    setRootEl(node);
  }, []);
  const contentRef = useCallback((node: HTMLElement | null) => {
    contentElRef.current = node;
    setContentEl(node);
  }, []);

  /** Pull the view to the end — but only if it was already there. */
  const follow = useCallback(() => {
    const viewport = scrollViewportOf(rootElRef.current);
    if (!viewport || !stickRef.current) return;
    viewport.scrollTop = viewport.scrollHeight;
  }, []);

  /** Take the reader back to the end, and follow again from there. */
  const jumpToEnd = useCallback(() => {
    const viewport = scrollViewportOf(rootElRef.current);
    if (!viewport) return;
    stickRef.current = true;
    setAtEnd(true);
    if (!prefersReducedMotion() && typeof viewport.scrollTo === "function") {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
    } else {
      viewport.scrollTop = viewport.scrollHeight;
    }
  }, []);

  // One listener answers both questions: does new output pull the view along,
  // and does the surface need to offer a way back.
  useEffect(() => {
    const viewport = scrollViewportOf(rootEl);
    if (!viewport) return;
    const read = () => {
      const near = isNearEnd(viewport.scrollTop, viewport.scrollHeight, viewport.clientHeight);
      stickRef.current = near;
      setAtEnd(near);
    };
    read();
    viewport.addEventListener("scroll", read, { passive: true });
    return () => viewport.removeEventListener("scroll", read);
  }, [rootEl]);

  // An answer grows WITHOUT a new item arriving, so no render-driven effect
  // fires for it; the content's own size is the honest signal.
  useEffect(() => {
    const viewport = scrollViewportOf(rootEl);
    if (!viewport || !contentEl || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (stickRef.current) viewport.scrollTop = viewport.scrollHeight;
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
