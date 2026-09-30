import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";

import { resolvePetAnimation, type PetState } from "@/lib/petStates";
import type { Pet } from "@/lib/petsApi";
import { cn } from "@/lib/utils";

/**
 * One pet, one state, drawn straight from its sprite sheet.
 *
 * The frame is a `frame_size` square of the sheet shown as a CSS background,
 * then enlarged by an INTEGER factor with nearest-neighbour sampling, so the
 * pixels stay crisp squares the way the desktop draws them. The sheet's own
 * size is never needed: the cell is cut at source scale and the whole box is
 * scaled with a transform.
 *
 * Frames advance on a small timer that writes the background position
 * directly (no React render per frame), at the manifest's fps. A looping row
 * wraps; a one-shot row holds its last frame. The timer stops while the sprite
 * is off-screen, and under `prefers-reduced-motion` the sprite shows frame 0
 * and never moves.
 */
export function PetSprite({
  pet,
  state,
  scale,
  label,
  className,
}: {
  pet: Pick<Pet, "sheet_url" | "frame_size" | "animations">;
  state: PetState;
  /** Display factor; rounded to a whole number so pixels stay square. */
  scale: number;
  /** Accessible name; the sprite is decorative without one. */
  label?: string;
  className?: string;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const cellRef = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion() ?? false;
  const onScreen = useOnScreen(boxRef);

  const size = pet.frame_size;
  const factor = Math.max(1, Math.round(scale));
  const animation = resolvePetAnimation(pet.animations, state);
  const row = animation?.row ?? 0;
  const frames = animation?.frames ?? 1;
  const fps = animation?.fps ?? 1;
  const loop = animation?.loop ?? true;
  const drawable = animation !== null;

  useEffect(() => {
    const cell = cellRef.current;
    if (!cell || !drawable) return;
    const place = (frame: number) => {
      cell.style.backgroundPosition = `${-frame * size}px ${-row * size}px`;
    };
    place(0);
    if (frames <= 1 || reduced || !onScreen) return;
    let frame = 0;
    const timer = window.setInterval(() => {
      frame += 1;
      if (frame >= frames) {
        if (!loop) {
          window.clearInterval(timer);
          return;
        }
        frame = 0;
      }
      place(frame);
    }, 1000 / Math.max(1, fps));
    return () => window.clearInterval(timer);
    // Primitives only: the animation object is rebuilt on every render, and
    // depending on it would restart the row each time a parent repaints.
  }, [drawable, row, frames, fps, loop, size, reduced, onScreen, pet.sheet_url]);

  return (
    <div
      ref={boxRef}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      data-testid="pet-sprite"
      data-state={state}
      className={cn("relative shrink-0 overflow-hidden", className)}
      style={{ width: size * factor, height: size * factor }}
    >
      {drawable && (
        <div
          ref={cellRef}
          data-testid="pet-sprite-cell"
          className="absolute left-0 top-0 bg-no-repeat [image-rendering:pixelated]"
          style={{
            width: size,
            height: size,
            backgroundImage: `url("${pet.sheet_url.replace(/"/g, "%22")}")`,
            backgroundPosition: `0px ${-row * size}px`,
            transform: `scale(${factor})`,
            transformOrigin: "0 0",
          }}
        />
      )}
    </div>
  );
}

/** The largest whole-number factor that keeps a frame within `maxPx`. */
export function integerScaleFor(frameSize: number, maxPx: number): number {
  if (frameSize <= 0) return 1;
  return Math.max(1, Math.floor(maxPx / frameSize));
}

/**
 * Whether the element is in the viewport. Without IntersectionObserver
 * (jsdom, very old engines) it counts as visible, so the sprite still plays.
 */
function useOnScreen(ref: React.RefObject<HTMLElement>): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) setVisible(entry.isIntersecting);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return visible;
}
