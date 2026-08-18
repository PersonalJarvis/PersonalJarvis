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
 * rotates around a fixed point, clockwise"), with a slow nod on top. It is
 * ambient, not animation: a full turn takes about a minute, and you can read
 * a label while it moves.
 *
 * And it is CONTINUOUS. Nothing in here ever sets the camera somewhere else
 * in one go once it is running: a re-frame (new data, a settled layout, the
 * Center button, a resize) only moves the target, and every frame the loop
 * eases distance and centre a little toward it. The first cut tweened the
 * camera on every re-frame and parked the drift around the tween — five
 * lurches after each data change, and a step in height on every resume.
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
  approach,
  approachPoint,
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

/**
 * How quickly the camera glides to a new framing. Re-framing used to be a
 * 700 ms tween with the drift parked around it, and it happened five times
 * after every data change; each one was a visible lurch (maintainer,
 * 2026-08-18: "it teleports, it snaps"). Now a re-frame only sets a TARGET
 * distance and centre, and the drift loop eases toward them every frame with
 * these time constants — the turn never stops, the height never jumps.
 */
const DISTANCE_TAU_MS = 1_600;
const CENTRE_TAU_MS = 900;

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
  /** Where a re-frame wants the camera; the loop glides there. */
  const targetRef = useRef<{ centre: Vec3; distance: number } | null>(null);
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

  /**
   * Decide where the camera should stand: the middle of the network and how
   * far back. Only the FIRST framing places the camera; every later one just
   * moves the target, and the drift loop glides there.
   */
  const frame = useCallback((): void => {
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
    targetRef.current = { centre: framing.centre, distance };

    if (orbitRef.current.distance === 0) {
      // Nothing on screen yet: stand there at once, and start the turn.
      centreRef.current = framing.centre;
      orbitRef.current = { distance, azimuth: 0, elevation: BASE_ELEVATION };
      graph.cameraPosition(orbitPoint(framing.centre, orbitRef.current), framing.centre, 0);
    }
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

      const swing = Math.sin(swingPhaseRef.current) * ELEVATION_SWING;
      if (resyncRef.current) {
        // Carry on from wherever the user left the camera — their angle,
        // their height, how far out they zoomed — instead of yanking it back
        // to our own. The pivot stays ours: the turn continues around the
        // same point, just from where they parked the view. Their zoom
        // becomes the new target too, so the glide does not creep back.
        const camera = graph.camera();
        if (camera?.position) {
          const seen = orbitFrom(centreRef.current, camera.position);
          if (seen.distance > 0) {
            orbitRef.current = seen;
            // Take the swing back out so the resting height is theirs and the
            // swing continues from where it is — no step on resume.
            baseElevationRef.current = clampElevation(seen.elevation - swing);
            if (targetRef.current) targetRef.current = { ...targetRef.current, distance: seen.distance };
          }
        }
        resyncRef.current = false;
      }

      // Where the turn is centred: the pivot page, read live because the
      // layout keeps moving it (settling, new data), else the framed middle.
      // Eased, never set — a re-frame or a settling pivot glides the view.
      const pivotNode = pivotRef.current;
      const wanted: Vec3 =
        pivotNode && Number.isFinite(pivotNode.x)
          ? { x: pivotNode.x ?? 0, y: pivotNode.y ?? 0, z: pivotNode.z ?? 0 }
          : (targetRef.current?.centre ?? centreRef.current);
      centreRef.current = approachPoint(centreRef.current, wanted, elapsed, CENTRE_TAU_MS);

      const orbit = orbitRef.current;
      if (targetRef.current) {
        orbit.distance = approach(orbit.distance, targetRef.current.distance, elapsed, DISTANCE_TAU_MS);
      }
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
