import { useContext, useEffect, useRef } from "react";
import { CanvasActivity } from "./useCanvasAwake";

/** Wall-clock updates sleep with a retained canvas and resume at their normal cadence. */
export function useCanvasInterval(callback: () => void, milliseconds: number, enabled = true): void {
  const active = useContext(CanvasActivity);
  const latest = useRef(callback);
  latest.current = callback;
  useEffect(() => {
    if (!active || !enabled) return;
    const timer = window.setInterval(() => latest.current(), milliseconds);
    return () => window.clearInterval(timer);
  }, [active, enabled, milliseconds]);
}
