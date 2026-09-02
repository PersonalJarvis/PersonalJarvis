/**
 * Should a canvas be rendering frames right now?
 *
 * Answered with an IntersectionObserver on the canvas host — the ONLY signal
 * this shell can be trusted on. `document.hidden` is explicitly not consulted:
 * the WebView2 host reports an on-screen window as hidden for minutes at a
 * time (docs/BUGS.md, the visibility class), so gating on it parks scenes the
 * user is looking at. `display: none` and scrolled-out-of-view both read as
 * non-intersecting, which covers section switches and hidden sticky views.
 *
 * The society README mandates this pattern for every world/card canvas.
 */
import { useEffect, useState, type RefObject } from "react";

export function useCanvasAwake(hostRef: RefObject<HTMLElement | null>): boolean {
  const [awake, setAwake] = useState(true);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (typeof IntersectionObserver === "undefined") return; // old shells: stay awake
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) setAwake(entry.isIntersecting);
      },
      { threshold: 0.05 },
    );
    observer.observe(host);
    return () => observer.disconnect();
  }, [hostRef]);

  return awake;
}

export default useCanvasAwake;
