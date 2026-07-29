/**
 * Is the document on screen at all — or is the window minimized, behind another
 * one, or sitting in a background tab?
 *
 * Existed inline in every polling effect that cared, as a `document.hidden`
 * check at the top of the tick plus a `visibilitychange` listener to catch up
 * afterwards. Both halves are needed and it is the second one that gets
 * forgotten: skipping ticks while hidden is only correct if something reads
 * again on the way back, or the screen keeps showing whatever it last managed
 * to fetch for as long as the window stays open.
 *
 * As a piece of STATE rather than a check, a poller expresses the same thing by
 * binding its effect to the answer: hidden tears the interval down, visible
 * builds it again — and building it again is the catch-up read, for free.
 *
 * Answers `true` where there is no `document` (SSR, a bare test environment),
 * because "cannot tell" must not silently switch polling off everywhere.
 */
import { useEffect, useState } from "react";

function currentlyVisible(): boolean {
  if (typeof document === "undefined") return true;
  return document.hidden !== true;
}

export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(currentlyVisible);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const onChange = () => setVisible(currentlyVisible());
    // Read once on mount too: the document may have been hidden between the
    // initial state above and this effect running.
    onChange();
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  return visible;
}
