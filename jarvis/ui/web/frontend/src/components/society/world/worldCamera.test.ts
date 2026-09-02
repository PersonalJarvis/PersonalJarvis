import { describe, expect, it } from "vitest";

import { focusFromSearch } from "./cameraStore";
import { ISLAND_HALF_M } from "./islandLayout";
import {
  CAMERA_PITCH_DEG,
  CAMERA_YAW_DEG,
  DEFAULT_ZOOM,
  ZOOM_WIDTHS_M,
  cameraOffset,
  clampTarget,
  dragToPan,
  groundBasis,
  orthoHalfExtents,
  stepZoom,
  visibleGroundCorners,
} from "./worldCamera";

describe("worldCamera", () => {
  it("looks down steeply from the south-east", () => {
    expect(CAMERA_PITCH_DEG).toBe(50);
    expect(CAMERA_YAW_DEG).toBe(45);
    const [x, y, z] = cameraOffset();
    expect(x).toBeGreaterThan(0);
    expect(z).toBeGreaterThan(0);
    expect(x).toBeCloseTo(z, 3);
    // Steeper than 45°: more height than ground distance.
    expect(y).toBeGreaterThan(Math.hypot(x, z));
  });

  it("zooms in three fixed steps and clamps at both ends", () => {
    expect(ZOOM_WIDTHS_M).toEqual([32, 64, 128]);
    expect(stepZoom(DEFAULT_ZOOM, 1)).toBe(2);
    expect(stepZoom(2, 1)).toBe(2);
    expect(stepZoom(0, -1)).toBe(0);
    expect(stepZoom(1, -1)).toBe(0);
  });

  it("sizes the frustum from the visible width and the aspect", () => {
    const { halfW, halfH } = orthoHalfExtents(64, 16 / 9);
    expect(halfW).toBe(32);
    expect(halfH).toBeCloseTo(18, 3);
  });

  it("maps screen-right to north-east and screen-up to north-west at 45° yaw", () => {
    const { right, forward } = groundBasis();
    expect(right[0]).toBeCloseTo(Math.SQRT1_2, 5);
    expect(right[1]).toBeCloseTo(-Math.SQRT1_2, 5);
    expect(forward[0]).toBeCloseTo(-Math.SQRT1_2, 5);
    expect(forward[1]).toBeCloseTo(-Math.SQRT1_2, 5);
    // Perpendicular unit vectors.
    expect(right[0] * forward[0] + right[1] * forward[1]).toBeCloseTo(0, 6);
  });

  it("pans the target against the drag so the world follows the pointer", () => {
    const widthM = 64;
    const stagePx = 1280; // 0.05 m per px
    const { right, forward } = groundBasis();
    // Drag right by 200 px → target moves 10 m to screen-left.
    const [dx, dz] = dragToPan(200, 0, widthM, stagePx);
    expect(dx).toBeCloseTo(-10 * right[0], 4);
    expect(dz).toBeCloseTo(-10 * right[1], 4);
    // Drag down by 100 px → target moves forward (up-screen), stretched by 1/sin(50°).
    const [fx, fz] = dragToPan(0, 100, widthM, stagePx);
    const ground = 5 / Math.sin((50 * Math.PI) / 180);
    expect(fx).toBeCloseTo(ground * forward[0], 4);
    expect(fz).toBeCloseTo(ground * forward[1], 4);
    expect(dragToPan(10, 10, widthM, 0)).toEqual([0, 0]);
  });

  it("keeps the target on the island", () => {
    expect(clampTarget(10_000, -10_000)).toEqual([ISLAND_HALF_M, -ISLAND_HALF_M]);
    expect(clampTarget(3, -4)).toEqual([3, -4]);
  });

  it("draws the viewport as a rectangle centred on the target, deeper than wide on the ground", () => {
    const corners = visibleGroundCorners([0, 0], 64, 16 / 9);
    expect(corners).toHaveLength(4);
    const cx = corners.reduce((s, c) => s + c[0], 0) / 4;
    const cz = corners.reduce((s, c) => s + c[1], 0) / 4;
    expect(cx).toBeCloseTo(0, 4);
    expect(cz).toBeCloseTo(0, 4);
    const width = Math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]);
    const depth = Math.hypot(corners[3][0] - corners[0][0], corners[3][1] - corners[0][1]);
    expect(width).toBeCloseTo(64, 3);
    expect(depth).toBeCloseTo(36 / Math.sin((50 * Math.PI) / 180), 3);
  });

  it("reads a ?world=x,z,zoom deep link and ignores garbage", () => {
    expect(focusFromSearch("?view=agents&world=12,-30,0")).toEqual({ target: [12, -30], zoom: 0 });
    expect(focusFromSearch("?world=5,5")).toEqual({ target: [5, 5], zoom: 1 });
    expect(focusFromSearch("?world=9999,0,7")?.target[0]).toBe(ISLAND_HALF_M);
    expect(focusFromSearch("?world=abc")).toBeNull();
    expect(focusFromSearch("?view=agents")).toBeNull();
  });
});
