import { describe, expect, it } from "vitest";
import { PerspectiveCamera, Vector3 } from "three";
import { avoidCameraCollision, boundsCorners, CAMERA_FOV, fitWorldBounds, MAX_POLAR, MIN_POLAR } from "./camera";
import { box, WORLD_BOUNDS } from "./world";

describe("Mars camera", () => {
  it.each([0.32, 0.65, 1, 16 / 9, 3.4])("fits the full three-dimensional world at aspect %s", (aspect) => {
    const frame = fitWorldBounds(WORLD_BOUNDS, aspect);
    const camera = new PerspectiveCamera(CAMERA_FOV, aspect, 0.12, 20000);
    camera.position.fromArray(frame.position); camera.lookAt(...frame.target); camera.updateMatrixWorld();
    for (const corner of boundsCorners(WORLD_BOUNDS)) {
      const point = new Vector3(...corner).project(camera);
      expect(Math.abs(point.x)).toBeLessThan(1 / 1.15);
      expect(Math.abs(point.y)).toBeLessThan(1 / 1.15);
      expect(point.z).toBeGreaterThan(-1); expect(point.z).toBeLessThan(1);
    }
  });
  it("shortens a follow camera before its wall, and permits a clear sightline", () => {
    const wall = box("wall", 0, 0, 4, 8, 8, 1);
    const blocked = avoidCameraCollision([0, 2, 0], [0, 3, 10], [wall], () => 0);
    expect(blocked[2]).toBeLessThan(3.5);
    expect(avoidCameraCollision([0, 2, 0], [0, 3, 2], [wall], () => 0)).toEqual([0, 3, 2]);
  });
  it("stops before terrain occlusion and avoids both vertical poles", () => {
    const position = avoidCameraCollision([0, 2, 0], [0, 3, 20], [], (_x, z) => z > 6 ? 10 : 0);
    expect(position[2]).toBeLessThanOrEqual(6);
    expect(MIN_POLAR).toBeGreaterThan(0); expect(MAX_POLAR).toBeLessThan(Math.PI / 2);
  });
  it("keeps the lens outside a landmark even when its orbit pivot is inside", () => {
    const mast = box("mast", 0, 0, 0, 14, 62, 14);
    expect(avoidCameraCollision([0, 30, 0], [0, 31, 20], [mast], () => 0)).toEqual([0, 31, 20]);
    const near = avoidCameraCollision([0, 30, 0], [0, 30, 1], [mast], () => 0);
    expect(near[2]).toBeGreaterThan(7.35);
  });
});
