import { describe, expect, it } from "vitest";
import { PerspectiveCamera, Vector3 } from "three";
import { avoidCameraCollision, boundsCorners, CAMERA_FOV, fitWorldBounds, frameInspectionBounds, MAX_POLAR, MIN_POLAR } from "./camera";
import { box, outpostBounds, WORLD_BOUNDS } from "./world";
import { parseViewPreferences, VIEW_DIRECTIONS, VIEWPOINTS } from "./viewPreferences";

describe("Mars camera", () => {
  it.each(VIEWPOINTS)("frames the complete authored Outpost from %s", (view) => {
    const bounds = outpostBounds();
    expect(bounds.min[0]).toBeLessThan(125); // West bridge, not just the plateau.
    expect(bounds.min[1]).toBeLessThan(0); // Complete cliff/support base.
    const frame = frameInspectionBounds(bounds, 16 / 9, 1.15, VIEW_DIRECTIONS[view]);
    const camera = new PerspectiveCamera(CAMERA_FOV, 16 / 9, 0.12, 20000);
    camera.position.fromArray(frame.position); camera.lookAt(...frame.target); camera.updateMatrixWorld();
    for (const point of boundsCorners(bounds)) {
      const projected = new Vector3(...point).project(camera);
      expect(Math.abs(projected.x)).toBeLessThan(1 / 1.15);
      expect(Math.abs(projected.y)).toBeLessThan(1 / 1.15);
    }
  });
  it("backs a preset lens out of solids without cropping its complete subject", () => {
    const bounds = box("subject", 0, 0, 0, 10, 10, 10);
    const raw = fitWorldBounds(bounds, 1.6);
    const obstacle = box("lens-obstacle", ...raw.position, 10, 10, 10);
    const frame = frameInspectionBounds(bounds, 1.6, 1.15, [0.8, 0.9, 1], [obstacle], () => -10);
    expect(frame.distance).toBeGreaterThan(raw.distance);
    expect(frame.target).toEqual(raw.target);
    const camera = new PerspectiveCamera(CAMERA_FOV, 1.6, 0.12, 20000);
    camera.position.fromArray(frame.position); camera.lookAt(...frame.target); camera.updateMatrixWorld();
    for (const point of boundsCorners(bounds)) {
      const projected = new Vector3(...point).project(camera);
      expect(Math.abs(projected.x)).toBeLessThan(1 / 1.15);
      expect(Math.abs(projected.y)).toBeLessThan(1 / 1.15);
    }
  });
  it("restores only valid client/world view state and recovers from corrupt storage", () => {
    const pose = { position: [320, 100, 150], target: [320, 76, 50] };
    const saved = { world_id: "mars:ordinary", layout_version: 1, mode: "orbit", viewpoint: "rear", neutral: true, pose };
    expect(parseViewPreferences(JSON.stringify(saved))).toEqual({ mode: "orbit", viewpoint: "rear", neutral: true, shadows: true, pose });
    expect(parseViewPreferences(JSON.stringify({ ...saved, shadows: false })).shadows).toBe(false);
    for (const change of [{ world_id: "mars:swarm:other" }, { layout_version: 2 }, { mode: "bad" }]) {
      expect(parseViewPreferences(JSON.stringify({ ...saved, ...change })).mode).toBe("overview");
    }
    for (const position of [[0, 0], [null, 0, 0], [1e9, 0, 0], pose.target]) {
      expect(parseViewPreferences(JSON.stringify({ ...saved, pose: { ...pose, position } })).mode).toBe("overview");
    }
    expect(parseViewPreferences("invalid JSON").mode).toBe("overview");
  });
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
