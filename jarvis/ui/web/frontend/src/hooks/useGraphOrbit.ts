/**
 * The camera work for the 3D memory maps: fill the frame, then keep turning.
 *
 * Two jobs, one owner, because they are the same state. Framing decides where
 * the middle of the network is and how far back to stand; the drift walks the
 * camera around that same point. Split across two places they fight — the
 * drift keeps restoring an angle the fit just changed.
 *
 * The motion is ONE thing: a steady clockwise turn around a fixed pivot, seen
 * from a raised camera so the turn reads as a rotation about a point rather
 * than nodes sliding sideways (maintainer decision 2026-08-18: "always
 * rotates around a fixed point, clockwise"). No rise and fall on top — a
 * bobbing camera made the pivot itself look like it was drifting. It is
 * still ambient, not animation: a full turn takes well over a minute, and
 * you can read a label while it moves.
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
  framingAround,
  framingFor,
  orbitDistance,
  orbitFrom,
  orbitPoint,
  stepAzimuth,
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

/**
 * One full revolution. Half again as fast as the first cut (96 s → 64 s,
 * maintainer 2026-08-18) — still slow enough to read a label while it moves.
 */
const REVOLUTION_MS = 64_000;

/**
 * Resting height above the network's own plane, radians.
 *
 * Lower than it used to be (was 1.0 ≈ 57°, a near top-down view that made the
 * map read as a flat disc turning). At ≈ 30° the camera looks THROUGH the
 * network: pages on the near side sweep past large, pages on the far side
 * pass behind the pivot small, and the whole thing reads as a volume in space
 * — what the maintainer asked for on 2026-08-18. Still high enough that the
 * turn is unmistakably a rotation about the pivot rather than a tilt.
 */
const BASE_ELEVATION = 0.52;

/**
 * The camera also rises and falls as it goes round — a slow nod on top of the
 * turn, so the same page is not always the one in front and the parallax
 * changes from pass to pass. Its period is deliberately NOT a divisor of the
 * revolution: the view never repeats exactly.
 */
const ELEVATION_SWING = 0.28;
const SWING_MS = 61_000;

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
  /**
   * The node to turn around, when the map has one — the user's own page. The
   * simulation keeps moving it, so the object is read fresh every frame and
   * the camera follows; absent, the orbit turns around the network's trimmed
   * mean as before.
   */
  pivot?: Partial<Vec3> | null;
  /** Bump to re-frame: new data, a settled layout, the Center button. */
  frameSignal: number;
}

export function useGraphOrbit({
  graphRef,
  hostRef,
  nodes,
  pivot = null,
  frameSignal,
}: GraphOrbitOptions): void {
  const centreRef = useRef<Vec3>({ x: 0, y: 0, z: 0 });
  const orbitRef = useRef<Orbit>({
    distance: 0,
    azimuth: 0,
    elevation: BASE_ELEVATION,
  });
  const pausedUntilRef = useRef(0);
  const resyncRef = useRef(false);
  // Read once per mount rather than per frame; the array identity changes on
  // every data generation but the objects inside are the live ones.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const pivotRef = useRef(pivot);
  pivotRef.current = pivot;
  /** Where the elevation swing is in its cycle; advanced by the drift. */
  const swingPhaseRef = useRef(0);
  /** The height the swing oscillates around — ours, or the user's after a drag. */
  const baseElevationRef = useRef(BASE_ELEVATION);

  /** Point the camera at the middle of the network, far enough back to see it. */
  const frame = useCallback((transitionMs = 700): void => {
    const graph = graphRef.current;
    if (!graph) return;
    const framing = pivotRef.current
      ? framingAround(pivotRef.current, nodesRef.current, 0.95)
      : framingFor(nodesRef.current, 0.95);
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
    baseElevationRef.current = BASE_ELEVATION;
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
        // Carry on from wherever the user left the camera — their angle,
        // their height, how far out they zoomed — instead of yanking it back
        // to our own. The pivot stays ours: the turn continues around the
        // same point, just from where they parked the view.
        const camera = graph.camera();
        if (camera?.position) {
          const seen = orbitFrom(centreRef.current, camera.position);
          if (seen.distance > 0) {
            orbitRef.current = seen;
            baseElevationRef.current = seen.elevation;
          }
        }
        resyncRef.current = false;
      }

      // The pivot node keeps moving while the layout settles (and again after
      // new data); following it each frame is what makes the turn stay
      // centred on the page rather than on where it was when we framed.
      const pivotNode = pivotRef.current;
      if (pivotNode && Number.isFinite(pivotNode.x)) {
        centreRef.current = {
          x: pivotNode.x ?? 0,
          y: pivotNode.y ?? 0,
          z: pivotNode.z ?? 0,
        };
      }

      const orbit = orbitRef.current;
      orbit.azimuth = stepAzimuth(orbit.azimuth, elapsed, REVOLUTION_MS);
      swingPhaseRef.current += (elapsed / SWING_MS) * Math.PI * 2;
      orbit.elevation = clampElevation(
        baseElevationRef.current + Math.sin(swingPhaseRef.current) * ELEVATION_SWING,
      );

      graph.cameraPosition(orbitPoint(centreRef.current, orbit), centreRef.current, 0);
    };

    raf = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(raf);
  }, [graphRef]);
}

/** Keep the nod inside a range where "up" stays up and the pivot stays framed. */
function clampElevation(value: number): number {
  return Math.min(1.25, Math.max(0.12, value));
}
