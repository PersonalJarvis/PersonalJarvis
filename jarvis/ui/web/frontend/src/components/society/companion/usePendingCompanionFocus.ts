import { useEffect, useRef, type MutableRefObject } from "react";
import type { Point } from "./kinematics";

/** Consume a request only when the host has applied it to a published pose. */
export function usePendingCompanionFocus(request: number, position: MutableRefObject<Point | null>, readyVersion: number, apply: (point: Point) => boolean): void {
  const applied = useRef(0);
  useEffect(() => {
    if (request <= applied.current || !position.current) return;
    if (apply(position.current)) applied.current = request;
  }, [request, position, readyVersion, apply]);
}
