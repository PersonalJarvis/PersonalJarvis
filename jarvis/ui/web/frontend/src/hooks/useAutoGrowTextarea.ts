import { useLayoutEffect, useRef, type RefObject } from "react";

/**
 * A textarea that grows with what is typed into it.
 *
 * A textarea keeps the height its `rows` give it and scrolls inside that box,
 * so a message four lines long is read three lines at a time, with its own
 * opening out of sight (maintainer, 2026-08-27: "I want to see the whole
 * text, not scroll up"). This sets the element's height to its content after
 * every change, from the `rows` minimum up to whatever `max-height` the
 * element's own styling names — past that cap the box scrolls again, so a
 * pasted file cannot push the composer off the screen.
 *
 * Layout-phase, so the box is already the right size when the frame paints.
 * Where the platform reports no content height (a test DOM), the box keeps
 * its `rows` height and nothing else happens.
 */
export function useAutoGrowTextarea(value: string): RefObject<HTMLTextAreaElement> {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Let it shrink first: a height set from the last, longer value would
    // keep `scrollHeight` from reporting the shorter content.
    el.style.height = "auto";
    const content = el.scrollHeight;
    if (content > 0) el.style.height = `${content}px`;
  }, [value]);
  return ref;
}
