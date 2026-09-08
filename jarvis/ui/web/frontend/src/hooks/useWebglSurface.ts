/**
 * Keeps a WebGL scene alive across the two things that kill one: running out
 * of contexts, and being told the context is gone.
 *
 * A browser allows a small, fixed number of live WebGL contexts per page —
 * Chromium's limit is 16 — and it enforces it by silently taking the OLDEST
 * one away when a new one is asked for. `WebGLRenderer.dispose()` does NOT
 * hand a context back; only `WEBGL_lose_context` does. So a page that mounts
 * and unmounts 3D scenes leaks one context per mount, and after sixteen the
 * browser kills the longest-running scene on screen — measured 2026-08-21:
 * fourteen trips into the Wiki section and back left fifteen live contexts
 * behind ONE canvas, and the deck's map (the oldest context in the page) was
 * the one that died.
 *
 * A dead canvas is not blank, either. With no `webglcontextlost` handler the
 * browser never offers the context back, and Chromium paints its own
 * broken-canvas placeholder into the element: a white rectangle with a small
 * sad face in the corner. That is the failure the maintainer screenshotted.
 *
 * So this hook does both halves:
 *  - on unmount (and between rebuilds) it RELEASES the context, so scenes that
 *    come and go do not spend the page's budget;
 *  - on a lost context it calls `preventDefault()` — without it the browser
 *    never fires `webglcontextrestored` — and rebuilds the scene by bumping a
 *    remount key, twice, before it gives up and degrades every graph on screen
 *    to the flat map with a stated reason.
 */
import { useEffect, useRef, useState, type RefObject } from "react";

import { reportWebglLost } from "@/lib/graphDimension";

/** How often one surface rebuilds itself before the flat map takes over. */
export const MAX_CONTEXT_RECOVERIES = 2;

/**
 * A beat between the loss and the rebuild, so a browser that means to hand the
 * context back gets the chance — and a GPU reset that takes several scenes
 * down at once is not answered by all of them in the same frame.
 */
const REBUILD_DELAY_MS = 250;

/** A scene that has run this long without trouble has earned its retries back. */
const STABLE_MS = 60_000;

/** How long to keep looking for the renderer's canvas after a mount. */
const MAX_ATTACH_FRAMES = 60;

export interface WebglSurface {
  /**
   * Remount key for the renderer. It only ever changes when a context was
   * lost, so a resize or a data change still reaches the live scene instead of
   * restarting the simulation.
   */
  generation: number;
}

/** Hand a canvas's WebGL context back to the browser. Safe to call twice. */
export function releaseWebglContext(canvas: HTMLCanvasElement): void {
  try {
    const gl = (canvas.getContext("webgl2") ??
      canvas.getContext("webgl")) as WebGLRenderingContext | null;
    // Absent on a context that is already lost, and on the software renderers
    // some locked-down WebViews ship — neither is worth an exception.
    const lose = gl?.getExtension("WEBGL_lose_context") as {
      loseContext?: () => void;
    } | null;
    lose?.loseContext?.();
  } catch {
    /* A context we cannot reach is one we cannot leak either. */
  }
}

/**
 * @param hostRef the element the renderer paints its canvas into.
 */
export function useWebglSurface(
  hostRef: RefObject<HTMLElement | null>,
): WebglSurface {
  const [generation, setGeneration] = useState(0);
  const recoveriesRef = useRef(0);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let canvas: HTMLCanvasElement | null = null;
    let released = false;
    let rebuilt = false;
    let frame = 0;
    let attempts = 0;
    let rebuildTimer = 0;

    const rebuild = () => {
      if (released || rebuilt) return;
      rebuilt = true;
      setGeneration((n) => n + 1);
    };

    const onLost = (event: Event) => {
      // THE line that matters: without it the browser writes the context off
      // for good and never fires `webglcontextrestored`.
      event.preventDefault();
      if (released) return;
      if (recoveriesRef.current >= MAX_CONTEXT_RECOVERIES) {
        // Twice was not a fluke. Say so once, out loud, and let every graph
        // fall back to the flat map rather than paint a broken canvas.
        reportWebglLost(true);
        return;
      }
      recoveriesRef.current += 1;
      rebuildTimer = window.setTimeout(rebuild, REBUILD_DELAY_MS);
    };

    const onRestored = () => {
      // The context is back but empty — the renderer's buffers, textures and
      // programs all went with it, so the scene has to be built again.
      window.clearTimeout(rebuildTimer);
      rebuild();
    };

    const attach = () => {
      canvas = host.querySelector("canvas");
      if (!canvas) {
        // The renderer mounts in its own layout effect; on a slow first paint
        // it can be a frame or two behind this one.
        if (attempts++ < MAX_ATTACH_FRAMES) frame = requestAnimationFrame(attach);
        return;
      }
      canvas.addEventListener("webglcontextlost", onLost);
      canvas.addEventListener("webglcontextrestored", onRestored);
    };
    attach();

    const stable = window.setTimeout(() => {
      recoveriesRef.current = 0;
    }, STABLE_MS);

    return () => {
      released = true;
      cancelAnimationFrame(frame);
      window.clearTimeout(rebuildTimer);
      window.clearTimeout(stable);
      if (!canvas) return;
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      // The renderer's own destructor stops its animation loop; touching the
      // graph ref here would reach the NEXT scene, because React commits a
      // rebuilt child BEFORE it runs this cleanup — that mistake left the
      // rebuilt map paused and black (2026-08-21).
      releaseWebglContext(canvas);
    };
  }, [generation, hostRef]);

  return { generation };
}

export default useWebglSurface;
