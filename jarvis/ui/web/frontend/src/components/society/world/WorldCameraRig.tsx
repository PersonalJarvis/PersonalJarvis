/**
 * Drives the Canvas's orthographic camera from the camera store: follows the
 * target with an exponential ease, applies the zoom step to the frustum, and
 * turns held keys into a steady pan. Inside the Canvas; the DOM controls live
 * in `useWorldControls`.
 */
import { useEffect, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { OrthographicCamera } from "three";

import { keyPanVector, useCameraStore } from "./cameraStore";
import {
  KEY_PAN_PER_S,
  ZOOM_WIDTHS_M,
  cameraOffset,
  followAlpha,
  groundBasis,
  orthoHalfExtents,
} from "./worldCamera";

/** How quickly the camera settles on a new target / zoom, per second. */
const FOLLOW_RATE = 7;

const OFFSET = cameraOffset();
const BASIS = groundBasis();

export function WorldCameraRig() {
  const camera = useThree((s) => s.camera) as OrthographicCamera;
  const size = useThree((s) => s.size);
  const invalidate = useThree((s) => s.invalidate);
  const setAspect = useCameraStore((s) => s.setAspect);
  const current = useRef<{ x: number; z: number; width: number }>({
    x: useCameraStore.getState().target[0],
    z: useCameraStore.getState().target[1],
    width: ZOOM_WIDTHS_M[useCameraStore.getState().zoom],
  });

  useEffect(() => {
    setAspect(size.width / Math.max(1, size.height));
  }, [size.width, size.height, setAspect]);

  // A store change must wake a demand-driven loop (reduced motion).
  useEffect(() => useCameraStore.subscribe(() => invalidate()), [invalidate]);

  useFrame((_, dt) => {
    const state = useCameraStore.getState();
    const step = Math.min(dt, 0.1);

    // Keyboard pan: constant speed as a fraction of the visible width.
    const [kr, ku] = keyPanVector(state.heldKeys);
    if (kr !== 0 || ku !== 0) {
      const speed = current.current.width * KEY_PAN_PER_S * step;
      const len = Math.hypot(kr, ku);
      const dx = ((kr * BASIS.right[0] + ku * BASIS.forward[0]) / len) * speed;
      const dz = ((kr * BASIS.right[1] + ku * BASIS.forward[1]) / len) * speed;
      state.panBy(dx, dz);
      invalidate();
    }

    const [tx, tz] = useCameraStore.getState().target;
    const targetWidth = ZOOM_WIDTHS_M[state.zoom];
    const a = followAlpha(step, FOLLOW_RATE);
    const cur = current.current;
    // While dragging the world must stick to the pointer: no easing.
    if (state.dragging) {
      cur.x = tx;
      cur.z = tz;
    } else {
      cur.x += (tx - cur.x) * a;
      cur.z += (tz - cur.z) * a;
    }
    cur.width += (targetWidth - cur.width) * a;
    if (Math.abs(targetWidth - cur.width) < 0.01) cur.width = targetWidth;

    const aspect = size.width / Math.max(1, size.height);
    const { halfW, halfH } = orthoHalfExtents(cur.width, aspect);
    camera.left = -halfW;
    camera.right = halfW;
    camera.top = halfH;
    camera.bottom = -halfH;
    camera.near = 1;
    camera.far = 1200;
    camera.position.set(cur.x + OFFSET[0], OFFSET[1], cur.z + OFFSET[2]);
    camera.lookAt(cur.x, 0, cur.z);
    camera.updateProjectionMatrix();

    // Keep the loop alive until the ease has settled.
    if (Math.abs(tx - cur.x) > 0.005 || Math.abs(tz - cur.z) > 0.005 || cur.width !== targetWidth) {
      invalidate();
    }
  }, 0);

  return null;
}
