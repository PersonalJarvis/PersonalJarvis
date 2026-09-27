import { Matrix4, Quaternion, Vector3 } from "three";
import type { Vec3 } from "./world";

/** Grade along the road; the width stays horizontal and the railings stay above it. */
export function roadOrientation(start: Vec3, end: Vec3): Quaternion {
  const forward = new Vector3(...end).sub(new Vector3(...start)).normalize();
  const right = new Vector3(forward.z, 0, -forward.x).normalize();
  const up = new Vector3().crossVectors(forward, right).normalize();
  return new Quaternion().setFromRotationMatrix(new Matrix4().makeBasis(right, up, forward));
}
