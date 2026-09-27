import { OrthographicCamera, Vector3 } from "three";
import { boundsCorners } from "./camera";
import { outpostBounds, OUTPOST, type Bounds, type Vec3 } from "./world";

export const SUN_POSITION: Vec3 = [OUTPOST.center[0] - 140, 230, 180];
export const SUN_TARGET: Vec3 = [320, 58, 50];
export const SHADOW_MAP_SIZE = 2048;

/** Fit the complete authored reference in light space, including its bridge.
 * Depth bias is expressed in metres before conversion to normalized depth;
 * changing the shadow frustum must not change its physical separation.
 */
export function fitOutpostShadow(bounds: Bounds) {
  const camera = new OrthographicCamera();
  camera.position.fromArray(SUN_POSITION);
  camera.lookAt(...SUN_TARGET);
  camera.updateMatrixWorld();
  const points = boundsCorners(bounds).map((point) => new Vector3(...point).applyMatrix4(camera.matrixWorldInverse));
  const padding = 8;
  const left = Math.min(...points.map((p) => p.x)) - padding;
  const right = Math.max(...points.map((p) => p.x)) + padding;
  const bottom = Math.min(...points.map((p) => p.y)) - padding;
  const top = Math.max(...points.map((p) => p.y)) + padding;
  const near = Math.max(0.5, -Math.max(...points.map((p) => p.z)) - padding);
  const far = -Math.min(...points.map((p) => p.z)) + padding;
  return { left, right, bottom, top, near, far, bias: -0.18 / (far - near), normalBias: 0.06 };
}

export const OUTPOST_SHADOW = fitOutpostShadow(outpostBounds());
