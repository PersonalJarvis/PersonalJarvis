import { describe, expect, it } from "vitest";
import { cameraHome, CAMERA_LIMITS, depthNear, wheelZoomSpeed } from "./officeCamera";

/** OrbitControls' dolly factor for one wheel event at this zoom speed. */
const step = (speed: number) => 1 / Math.pow(0.95, speed);
/** Wheel events of this size needed to zoom from `from` to `to` metres. */
const eventsFor = (from: number, to: number, deltaY: number, deltaMode = 0) =>
  Math.ceil(Math.log(from / to) / Math.log(step(wheelZoomSpeed(deltaY, deltaMode))));

describe("office wheel zoom", () => {
  it("brings the overview down to the character in a handful of mouse notches", () => {
    const overview = cameraHome({ minX: -14, maxX: 14, minZ: -22, maxZ: 22 }, 16 / 9);
    const distance = Math.hypot(...overview.position.map((v, i) => v - overview.target[i]) as [number, number, number]);
    expect(eventsFor(distance, 15, 100)).toBeLessThanOrEqual(12);
  });

  it("zooms by how far the wheel turned, so a trackpad's small steps stay small", () => {
    expect(wheelZoomSpeed(4)).toBeLessThan(wheelZoomSpeed(100) / 10);
    expect(wheelZoomSpeed(-100)).toBe(wheelZoomSpeed(100));
    // A whole trackpad swipe of 100 px moves about as far as one notch.
    const swipe = Math.pow(step(wheelZoomSpeed(4)), 25);
    expect(swipe).toBeCloseTo(step(wheelZoomSpeed(100)), 1);
  });

  it("reads line and page wheel modes and caps a single huge event", () => {
    expect(wheelZoomSpeed(3, 1)).toBeGreaterThan(wheelZoomSpeed(100));
    expect(wheelZoomSpeed(1, 2)).toBe(wheelZoomSpeed(100_000));
    expect(step(wheelZoomSpeed(100_000))).toBeLessThan(1.4);
    expect(wheelZoomSpeed(0)).toBeGreaterThan(0);
  });

  it("lets the camera come close enough to read a desk, and never inside the character", () => {
    expect(CAMERA_LIMITS.minDistance).toBeLessThanOrEqual(3);
    expect(CAMERA_LIMITS.minDistance).toBeGreaterThanOrEqual(2);
  });
});

/** Smallest depth step a 24-bit buffer resolves at `depth` metres with this near plane. */
const depthStep = (depth: number, near: number) => (depth * depth) / (near * 2 ** 24);

describe("office depth precision", () => {
  it("keeps the oak floor 1 mm above the slab resolvable at every zoom", () => {
    for (const distance of [CAMERA_LIMITS.minDistance, 15, 40, 90, CAMERA_LIMITS.maxDistance]) {
      // The far edge of the floor lies up to ~30 m behind the orbit target.
      expect(depthStep(distance + 30, depthNear(distance))).toBeLessThan(0.001);
    }
  });

  it("never clips the floor or the walls in front of the camera", () => {
    for (const distance of [CAMERA_LIMITS.minDistance, 15, 40, 90, CAMERA_LIMITS.maxDistance]) {
      // At the flattest allowed tilt the camera still stands this high above the floor.
      const height = distance * Math.cos(CAMERA_LIMITS.maxPolar);
      expect(depthNear(distance)).toBeLessThan(Math.max(0.5, height - 3));
    }
    expect(depthNear(CAMERA_LIMITS.minDistance)).toBe(0.2);
  });
});
