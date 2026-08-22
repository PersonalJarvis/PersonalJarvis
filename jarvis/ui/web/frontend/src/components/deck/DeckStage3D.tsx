import { useEffect, useRef, type RefObject } from "react";
import { useReducedMotion } from "framer-motion";
import { useDesktopWallpaper } from "@/hooks/useDesktopWallpaper";
import {
  PERSPECTIVE_ORIGIN,
  driftCompensation,
  parallaxShift,
  pointerOffset,
} from "@/lib/deckDepth";

/**
 * The display case's moving parts: the pointer parallax, the slots' drift
 * compensation, and the floor.
 *
 * `useDeckParallax` does two things on the board element it is given:
 *
 *  1. It follows the pointer over the board and writes the shift of the
 *     nearest plane into two custom properties (`--deck-px`, `--deck-py`);
 *     every slot's transform rule in index.css multiplies them by its own
 *     parallax factor (`lib/deckDepth.ts`). One animation frame per move at
 *     most, no React state on the way — the pointer must never go through a
 *     render to reach a transform. Leaving the board eases the planes back
 *     to rest (the transition is CSS). With reduced motion the vars stay at
 *     zero: the depth is still there, the sway is not.
 *  2. It measures where each `.deck-slot-3d` child sits relative to the
 *     board's vanishing point (its layout position, before any transform)
 *     and writes the pre-translation that keeps the plane's centre on its
 *     slot under the shared perspective (`driftCompensation`) — once, and
 *     again whenever the board or a slot changes size. Without it the near
 *     planes at the board's edges drift outward and are clipped.
 *
 * `DeckFloor` is the ground the case stands on: a plane laid flat under the
 * board, receding from the viewer to the wallpaper, carrying a blurred,
 * upside-down echo of the picture — the mascot and the planes stand on a
 * floor that reflects the wall behind them, which is what turns a picture
 * with things in front of it into a room. It takes the theme's ground colour
 * (so it is dark glass under the wave and pale stone under the terrace) and
 * the current wallpaper URL, nothing else.
 */
export function useDeckParallax<T extends HTMLElement>(
  ref: RefObject<T | null>,
  /**
   * Whether the board element is mounted. The view owns the ref from its
   * first render while the board only mounts after the start sequence, so
   * the effects must re-run when it does — a ref alone never re-triggers.
   */
  active = true,
): void {
  const reduced = useReducedMotion() ?? false;
  const frame = useRef<number | null>(null);

  // 2. drift compensation — layout geometry, re-measured on resize
  useEffect(() => {
    const el = ref.current;
    if (!el || !active) return;
    const measure = () => {
      const ox = el.clientWidth * PERSPECTIVE_ORIGIN.x;
      const oy = el.clientHeight * PERSPECTIVE_ORIGIN.y;
      el.querySelectorAll<HTMLElement>(".deck-slot-3d").forEach((slot) => {
        const z = parseFloat(slot.style.getPropertyValue("--slot-z")) || 0;
        const cx = slot.offsetLeft + slot.offsetWidth / 2 - ox;
        const cy = slot.offsetTop + slot.offsetHeight / 2 - oy;
        const { dx, dy } = driftCompensation(cx, cy, z);
        slot.style.setProperty("--slot-dx", `${dx}px`);
        slot.style.setProperty("--slot-dy", `${dy}px`);
      });
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    el.querySelectorAll<HTMLElement>(".deck-slot-3d").forEach((slot) => ro.observe(slot));
    return () => ro.disconnect();
  }, [ref, active]);

  // 1. pointer parallax — transform only, one frame per move
  useEffect(() => {
    const el = ref.current;
    if (!el || !active || reduced) return;
    let pending: { x: number; y: number } | null = null;
    const flush = () => {
      frame.current = null;
      if (!pending) return;
      const shift = parallaxShift(pending);
      el.style.setProperty("--deck-px", `${shift.x}px`);
      el.style.setProperty("--deck-py", `${shift.y}px`);
    };
    const onMove = (e: PointerEvent) => {
      pending = pointerOffset(e.clientX, e.clientY, el.getBoundingClientRect());
      if (frame.current === null) frame.current = window.requestAnimationFrame(flush);
    };
    const onLeave = () => {
      pending = { x: 0, y: 0 };
      if (frame.current === null) frame.current = window.requestAnimationFrame(flush);
    };
    el.addEventListener("pointermove", onMove, { passive: true });
    el.addEventListener("pointerleave", onLeave, { passive: true });
    return () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
      if (frame.current !== null) window.cancelAnimationFrame(frame.current);
      el.style.removeProperty("--deck-px");
      el.style.removeProperty("--deck-py");
    };
  }, [ref, active, reduced]);
}

export function DeckFloor() {
  const wallpaperUrl = useDesktopWallpaper();
  return (
    <div aria-hidden data-testid="deck-floor" className="deck-floor">
      <div className="deck-floor-echo" style={{ backgroundImage: `url(${wallpaperUrl})` }} />
      <div className="deck-floor-sheen" />
    </div>
  );
}
