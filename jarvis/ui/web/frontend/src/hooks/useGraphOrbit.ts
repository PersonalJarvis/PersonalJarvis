/**
 * The camera work for the 3D memory maps: fill the frame, then keep moving.
 *
 * Two jobs, one owner, because they are the same state. Framing decides where
 * the middle of the network is and how far back to stand; the drift walks the
 * camera around that same point. Split across two places they fight — the
 * drift keeps restoring an angle the fit just changed.
 *
 * The motion is deliberately slow: a full turn takes well over a minute, with
 * a slight rise and fall on top so the network reads as a solid object in
 * space rather than a flat picture that happens to spin. It is ambient, not
 * animation — you should be able to read a label while it moves.
 *
 * Three things stop it, and all three matter:
 *  - The user touching the map. Nothing is more irritating than a view that
 *    creeps away from where you just put it, so a drag or a scroll parks the
 *    drift, and when it resumes it carries on from the angle the USER left the
 *    camera at, read back out of the camera itself.
 *  - A hidden tab. Frames nobody sees still cost a GPU.
 *  - `prefers-reduced-motion`. Continuous movement is a genuine trigger for
 *    some people, and the OS switch for it is the one place they say so once
 *    instead of per app.
 */
import { useCallback, useEffect, useRef, type RefObject } from "react";

import {
  framingFor,
  orbitDistance,
  orbitFrom,
  orbitPoint,
  type Orbit,
  type Vec3,
} from "@/lib/graphCamera";

/** The slice of the renderer's imperative handle the camera work needs. */
export interface GraphCameraApi {
  cameraPosition: (
    position: Partial<Vec3>,
    lookAt?: Vec3,
    transitionMs?: number,
  ) => void;
  camera: () => { position: Vec3; fov?: number; aspect?: number };
}

/** One full revolution. Long enough to read while it happens. */
const REVOLUTION_MS = 96_000;

/** The rise and fall on top of the rotation. */
const SWAY_PERIOD_MS = 19_000;
const SWAY_RADIANS = 0.11;

/** Resting height above the network's own plane — a slight look down. */
const BASE_ELEVATION = 0.24;

/** How long after the user's last touch the drift picks back up. */
const RESUME_AFTER_MS = 3_500;

/** Share of the frame the network fills once framed. */
const FILL = 0.94;

export interface GraphOrbitOptions {
  graphRef: RefObject<GraphCameraApi | undefined>;
  /** Element the user actually drags — where the pause listeners go. */
  hostRef: RefObject<HTMLElement | null>;
  /** Live node array; the simulation writes x/y/z onto these objects. */
  nodes: readonly Partial<Vec3>[];
  /** Bump to re-frame: new data, a settled layout, the Center button. */
  frameSignal: number;
}

export function useGraphOrbit({
  graphRef,
  hostRef,
  nodes,
  frameSignal,
}: GraphOrbitOptions): void {
  const centreRef = useRef<Vec3>({ x: 0, y: 0, z: 0 });
  const orbitRef = useRef<Orbit>({
    distance: 0,
    azimuth: 0,
    elevation: BASE_ELEVATION,
  });
  // Phase of the sway, kept separately so pausing does not make the camera
  // jump when it resumes at a different point in the cycle.
  const swayRef = useRef(0);
  const pausedUntilRef = useRef(0);
  const resyncRef = useRef(false);
  // Read once per mount rather than per frame; the array identity changes on
  // every data generation but the objects inside are the live ones.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;

  /** Point the camera at the middle of the network, far enough back to see it. */
  const frame = useCallback((transitionMs = 700): void => {
    const graph = graphRef.current;
    if (!graph) return;
    const framing = framingFor(nodesRef.current, 0.95);
    if (!framing) return;

    const camera = graph.camera();
    const distance = orbitDistance(
      framing.radius,
      camera?.fov ?? 50,
      camera?.aspect ?? 1,
      FILL,
    );
    centreRef.current = framing.centre;
    // Keep whatever angle the camera is already at — re-framing is about how
    // far back you stand, not about turning the map back to the front.
    const current = camera?.position
      ? orbitFrom(framing.centre, camera.position)
      : null;
    orbitRef.current = {
      distance,
      azimuth: current?.azimuth ?? 0,
      elevation: BASE_ELEVATION,
    };
    graph.cameraPosition(
      orbitPoint(framing.centre, orbitRef.current),
      framing.centre,
      transitionMs,
    );
    // Our own move must not be mistaken for the user's, but the transition
    // does need to finish before the drift starts nudging the camera again.
    pausedUntilRef.current = Math.max(
      pausedUntilRef.current,
      performance.now() + transitionMs + 60,
    );
  }, [graphRef]);

  useEffect(() => {
    // One frame's delay: on the first render after a data swap the renderer
    // has a handle but the simulation has not written positions yet, and
    // framing an empty cloud puts the camera nowhere useful.
    const timer = window.setTimeout(() => frame(), 60);
    return () => window.clearTimeout(timer);
  }, [frame, frameSignal]);

  // Pause while the user is steering, and remember to pick their angle up.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const hold = () => {
      pausedUntilRef.current = performance.now() + RESUME_AFTER_MS;
      resyncRef.current = true;
    };
    // Only a held button counts as steering; a cursor crossing the map on its
    // way somewhere else is not, and treating it as such would park the drift
    // for good on a busy screen.
    const onMove = (event: PointerEvent) => {
      if (event.buttons !== 0) hold();
    };
    host.addEventListener("pointerdown", hold);
    host.addEventListener("pointermove", onMove);
    host.addEventListener("wheel", hold, { passive: true });
    host.addEventListener("touchstart", hold, { passive: true });
    return () => {
      host.removeEventListener("pointerdown", hold);
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("wheel", hold);
      host.removeEventListener("touchstart", hold);
    };
  }, [hostRef]);

  useEffect(() => {
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    let raf = 0;
    let last = performance.now();

    const step = (now: number) => {
      raf = window.requestAnimationFrame(step);
      const elapsed = Math.min(now - last, 100);
      last = now;

      if (document.hidden) return;
      if (now < pausedUntilRef.current) return;

      const graph = graphRef.current;
      if (!graph || orbitRef.current.distance === 0) return;

      if (resyncRef.current) {
        // Carry on from wherever the user left the camera — including how far
        // out they zoomed — instead of yanking it back to our own angle. The
        // sway is subtracted back out so the stored elevation stays the
        // RESTING one; keeping the swayed value would let each pause ratchet
        // the camera a little further up or down.
        const camera = graph.camera();
        if (camera?.position) {
          const seen = orbitFrom(centreRef.current, camera.position);
          if (seen.distance > 0) {
            orbitRef.current = {
              ...seen,
              elevation: seen.elevation - Math.sin(swayRef.current) * SWAY_RADIANS,
            };
          }
        }
        resyncRef.current = false;
      }

      const orbit = orbitRef.current;
      orbit.azimuth -= (elapsed / REVOLUTION_MS) * Math.PI * 2;
      swayRef.current += (elapsed / SWAY_PERIOD_MS) * Math.PI * 2;
      const elevation = orbit.elevation + Math.sin(swayRef.current) * SWAY_RADIANS;

      graph.cameraPosition(
        orbitPoint(centreRef.current, { ...orbit, elevation }),
        centreRef.current,
        0,
      );
    };

    raf = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(raf);
  }, [graphRef]);
}
