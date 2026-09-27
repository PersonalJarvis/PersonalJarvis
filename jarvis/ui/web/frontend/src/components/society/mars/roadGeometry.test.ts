import { describe, expect, it } from "vitest";
import { Vector3 } from "three";
import { roadOrientation } from "./roadGeometry";
import { ROADS } from "./world";
import { BUILDING_COLLIDERS } from "./world";
import { segmentBox } from "./camera";

describe("graded road rendering", () => {
  it("keeps the advertised pedestrian routes clear of building walls", () => {
    for (const road of ROADS) {
      const start: [number, number, number] = [road.start[0], road.start[1] + 1, road.start[2]];
      const end: [number, number, number] = [road.end[0], road.end[1] + 1, road.end[2]];
      for (const collider of BUILDING_COLLIDERS) {
        expect(segmentBox(start, end, collider, 0.4), `${road.id} intersects ${collider.id}`).toBeNull();
      }
    }
  });
  it("keeps every route horizontal across its width and every railing upright", () => {
    for (const road of ROADS) {
      const orientation = roadOrientation(road.start, road.end);
      const width = new Vector3(road.width / 2, 0, 0).applyQuaternion(orientation);
      const up = new Vector3(0, 1, 0).applyQuaternion(orientation);
      expect(width.y, road.id).toBeCloseTo(0, 8);
      expect(up.y, road.id).toBeGreaterThan(0.9);
      const forward = new Vector3(0, 0, 1).applyQuaternion(orientation);
      const delta = new Vector3(...road.end).sub(new Vector3(...road.start)).normalize();
      expect(forward.distanceTo(delta), road.id).toBeLessThan(1e-8);
    }
  });
});
