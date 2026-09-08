/**
 * Drives the Canvas's orthographic camera from the camera store: follows the
 * target with an exponential ease, applies the zoom step to the frustum,
 * carries the orbit angles (yaw / pitch) the viewer turns the island by, and
 * turns held keys into a steady pan. Inside the Canvas; the DOM controls live
 * in `useWorldControls`.
 *
 * Everything screen-relative is derived from the SMOOTHED angles, not the
 * store's: a keyboard pan during a turn then follows the picture the viewer
 * actually sees rather than the angle they are easing toward.
 */
import { useEffect, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import type { OrthographicCamera } from "three";

import { keyPanVector, useCameraStore } from "./cameraStore";
import { setViewAngles } from "./viewAngles";
import {
  CAMERA_FAR_M,
  KEY_PAN_PER_S,
  ZOOM_WIDTHS_M,
  cameraOffset,
  followAlpha,
  groundBasis,
  orthoHalfExtents,
} from "./worldCamera";

/** How quickly the camera settles on a new target / zoom, per second. */
const FOLLOW_RATE = 7;
/** A stepped quarter turn should read as a swing, so the angles ease slower. */

export function WorldCameraRig() {
  const camera = useThree((s) => s.camera) as OrthographicCamera;
  const size = useThree((s) => s.size);
  const invalidate = useThree((s) => s.invalidate);
  const setAspect = useCameraStore((s) => s.setAspect);
  const current = useRef<{ x: number; z: number; width: number; yaw: number; pitch: number }>({
    x: useCameraStore.getState().target[0],
    z: useCameraStore.getState().target[1],
    width: ZOOM_WIDTHS_M[useCameraStore.getState().zoom],
    yaw: useCameraStore.getState().yaw,
    pitch: useCameraStore.getState().pitch,
  });

  useEffect(() => {
    setAspect(size.width / Math.max(1, size.height));
  }, [size.width, size.height, setAspect]);

  // A deep-linked or already-turned view must reach the minimap and the sun
  // before the first frame, not one frame late.
  useEffect(() => {
    setViewAngles(current.current.yaw, current.current.pitch);
  }, []);

  // A store change must wake a demand-driven loop (reduced motion).
  useEffect(() => useCameraStore.subscribe(() => invalidate()), [invalidate]);

  useFrame((_, dt) => {
    const state = useCameraStore.getState();
    const step = Math.min(dt, 0.1);
    const cur = current.current;

    // Keyboard pan: constant speed as a fraction of the visible width, along
    // the ground axes of the picture on screen right now.
    const [kr, ku] = keyPanVector(state.heldKeys);
    if (kr !== 0 || ku !== 0) {
      const basis = groundBasis(cur.yaw);
      const speed = cur.width * KEY_PAN_PER_S * step;
      const len = Math.hypot(kr, ku);
      const dx = ((kr * basis.right[0] + ku * basis.forward[0]) / len) * speed;
      const dz = ((kr * basis.right[1] + ku * basis.forward[1]) / len) * speed;
      state.panBy(dx, dz);
      invalidate();
    }

    const [tx, tz] = useCameraStore.getState().target;
    const targetWidth = ZOOM_WIDTHS_M[state.zoom];
    const a = followAlpha(step, FOLLOW_RATE);
    // While dragging the world must stick to the pointer: no easing.
    if (state.dragging) {
      cur.x = tx;
      cur.z = tz;
    } else {
      cur.x += (tx - cur.x) * a;
      cur.z += (tz - cur.z) * a;
    }
    // Fixed zoom and view directions never animate through fractional pixel scales.
    cur.width = targetWidth;
    cur.yaw = state.yaw;
    cur.pitch = state.pitch;

    setViewAngles(cur.yaw, cur.pitch);

    const aspect = size.width / Math.max(1, size.height);
    const { halfW, halfH } = orthoHalfExtents(cur.width, aspect);
    const offset = cameraOffset(cur.pitch, cur.yaw);
    camera.left = -halfW;
    camera.right = halfW;
    camera.top = halfH;
    camera.bottom = -halfH;
    camera.near = 1;
    camera.far = CAMERA_FAR_M;
    // Snap the camera target in the two projected ground axes. Simulation
    // coordinates remain continuous; only presentation uses the pixel grid.
    const basis = groundBasis(cur.yaw);
    const unit = cur.width / Math.max(1, Math.floor(size.width / 2));
    const right = cur.x * basis.right[0] + cur.z * basis.right[1];
    const forward = cur.x * basis.forward[0] + cur.z * basis.forward[1];
    const r = Math.round(right / unit) * unit;
    const fUnit = unit / Math.sin(cur.pitch * Math.PI / 180);
    const f = Math.round(forward / fUnit) * fUnit;
    const sx = r * basis.right[0] + f * basis.forward[0];
    const sz = r * basis.right[1] + f * basis.forward[1];
    camera.position.set(sx + offset[0], offset[1], sz + offset[2]);
    camera.lookAt(sx, 0, sz);
    camera.updateProjectionMatrix();

    // Keep the loop alive until every ease has settled.
    if (
      Math.abs(tx - cur.x) > 0.005 ||
      Math.abs(tz - cur.z) > 0.005 ||
      cur.width !== targetWidth ||
      cur.yaw !== state.yaw ||
      cur.pitch !== state.pitch
    ) {
      invalidate();
    }
  }, 0);

  return null;
}
