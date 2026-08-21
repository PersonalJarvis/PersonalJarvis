/**
 * Stop a 3D memory map's engine while nobody is watching it.
 *
 * The force engine behind the maps keeps ticking so the liveliness force has
 * a frame to write into, and every tick pushes node and link positions
 * through the scene graph: a matrix update and a draw call per node, sixty
 * times a second. That is the price of a map that lives, and it is worth
 * paying while someone is looking at it.
 *
 * Nobody looking is the other half, and `document.hidden` does not cover it.
 * A desktop window sitting behind another window is not hidden — it is simply
 * not being watched, and it kept the full frame rate. Measured on a 4-core
 * laptop (2026-08-21): with the deck covered by another app, the board's map
 * alone held one core at 93 % and its GPU process another two thirds of one,
 * about a quarter of the whole machine, indefinitely.
 *
 * So the gate is hidden OR unfocused. Resuming picks up where it left off:
 * the layout is state, not animation, so nothing jumps when you come back —
 * which is why this pauses the engine rather than unmounting the scene.
 *
 * Deliberately NOT here: throttling the rate while someone IS watching. The
 * liveliness reads as alive at sixty and as a stutter below it, so the frame
 * rate someone sees stays untouched. This only removes frames nobody sees.
 */
import { useEffect, type RefObject } from "react";

/** The slice of a force-graph instance this gate drives. */
export interface GraphEngineApi {
  pauseAnimation: () => unknown;
  resumeAnimation: () => unknown;
}

/**
 * The instance lands on the ref a frame or two after mount, so the first
 * read can miss it. Bounded, for the same reason `useWebglSurface` bounds its
 * attach: a map that never mounts must not leave a retry loop running.
 */
const MAX_ATTACH_FRAMES = 120;

/**
 * Why this polls instead of trusting the events.
 *
 * The first cut listened for `blur`/`focus`/`visibilitychange` alone and did
 * nothing at all in the case it was written for. A window the user has never
 * clicked into never HAD focus, so it never fires `blur` when they work
 * elsewhere — the gate simply never woke up to look. Measured in the real
 * WebView 2026-08-21: `document.hasFocus()` was already false and the page
 * was still painting a full 60 fps, because nothing had asked.
 *
 * Two seconds is well under the time it takes to notice a still map, and one
 * `hasFocus()` read on a timer is nothing next to the frames it saves.
 */
const CHECK_MS = 2_000;

/** Is anyone actually looking at this document right now? */
function isWatched(): boolean {
  if (typeof document === "undefined") return true;
  if (document.hidden) return false;
  // jsdom and older embedders can be missing `hasFocus`; absent, assume the
  // window is watched rather than pausing a map the user is looking at.
  if (typeof document.hasFocus !== "function") return true;
  return document.hasFocus();
}

export function useGraphAwake(
  graphRef: RefObject<GraphEngineApi | undefined>,
  enabled = true,
): void {
  useEffect(() => {
    if (!enabled) return;

    let frame = 0;
    let attempts = 0;
    // The engine starts running, so that is what this gate believes until it
    // sees otherwise. Tracking it avoids pausing an already paused engine on
    // every focus event the window happens to fire.
    let awake = true;

    const apply = () => {
      const graph = graphRef.current;
      if (!graph) {
        if (attempts++ < MAX_ATTACH_FRAMES) {
          frame = requestAnimationFrame(apply);
        }
        return;
      }
      const next = isWatched();
      if (next === awake) return;
      awake = next;
      if (next) graph.resumeAnimation();
      else graph.pauseAnimation();
    };

    apply();
    // The events are the fast path — they react the instant you click away.
    // The timer is the one that actually catches the common case; see above.
    const timer = window.setInterval(apply, CHECK_MS);
    document.addEventListener("visibilitychange", apply);
    window.addEventListener("focus", apply);
    window.addEventListener("blur", apply);

    return () => {
      cancelAnimationFrame(frame);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", apply);
      window.removeEventListener("focus", apply);
      window.removeEventListener("blur", apply);
      // Never hand the scene back paused. The gate going away — the map being
      // torn down, or the caller turning it off — must not leave a still map
      // behind for whoever mounts next.
      if (!awake) graphRef.current?.resumeAnimation();
    };
  }, [graphRef, enabled]);
}
