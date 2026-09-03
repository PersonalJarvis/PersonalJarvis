import { describe, expect, it } from "vitest";

import { focusFromSearch } from "./cameraStore";
import { CAMERA_FROM, ISLAND_HALF_M, facesCamera } from "./islandLayout";
import {
  CAMERA_DISTANCE_M,
  CAMERA_FAR_M,
  CAMERA_PITCH_DEG,
  CAMERA_YAW_DEG,
  DEFAULT_ZOOM,
  MAX_PITCH_DEG,
  MAX_ZOOM,
  MIN_PITCH_DEG,
  ORBIT_YAW_PER_WIDTH_DEG,
  YAW_STEP_DEG,
  ZOOM_WIDTHS_M,
  cameraOffset,
  clampPitch,
  clampTarget,
  dragToOrbit,
  dragToPan,
  groundBasis,
  isDesignedView,
  normalizeYaw,
  northScreenAngle,
  orthoHalfExtents,
  shortestYawDelta,
  stepYaw,
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

  it("agrees with the island layout about where it stands", () => {
    // The layout turns houses so no door faces away from THIS camera: the
    // ground vector toward the camera is the opposite of screen-up on the ground.
    const { forward } = groundBasis();
    expect(-forward[0]).toBeCloseTo(CAMERA_FROM[0], 5);
    expect(-forward[1]).toBeCloseTo(CAMERA_FROM[1], 5);
    // A front pointing at the camera is seen; one pointing away is a back.
    expect(facesCamera(Math.atan2(CAMERA_FROM[0], CAMERA_FROM[1]))).toBe(true);
    expect(facesCamera(Math.atan2(-CAMERA_FROM[0], -CAMERA_FROM[1]))).toBe(false);
  });

  it("zooms in five fixed steps, up to the whole island, and clamps at both ends", () => {
    expect(ZOOM_WIDTHS_M).toEqual([32, 64, 128, 256, 512]);
    expect(ZOOM_WIDTHS_M[MAX_ZOOM]).toBe(ISLAND_HALF_M * 2);
    expect(stepZoom(DEFAULT_ZOOM, 1)).toBe(2);
    expect(stepZoom(MAX_ZOOM, 1)).toBe(MAX_ZOOM);
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
    const designed = { yaw: CAMERA_YAW_DEG, pitch: CAMERA_PITCH_DEG };
    expect(focusFromSearch("?view=agents&world=12,-30,0")).toEqual({
      target: [12, -30],
      zoom: 0,
      ...designed,
    });
    expect(focusFromSearch("?world=5,5")).toEqual({ target: [5, 5], zoom: 1, ...designed });
    expect(focusFromSearch("?world=9999,0,7")).toEqual({
      target: [ISLAND_HALF_M, 0],
      zoom: 1,
      ...designed,
    });
    expect(focusFromSearch("?world=0,0,4")?.zoom).toBe(4);
    expect(focusFromSearch("?world=abc")).toBeNull();
    expect(focusFromSearch("?view=agents")).toBeNull();
  });

  it("deep-links the orbit too, clamped and wrapped", () => {
    expect(focusFromSearch("?world=0,0,2,225")?.yaw).toBe(225);
    expect(focusFromSearch("?world=0,0,2,-45")?.yaw).toBe(315);
    expect(focusFromSearch("?world=0,0,2,225,70")?.pitch).toBe(70);
    // Past the limits the view is clamped, not refused.
    expect(focusFromSearch("?world=0,0,2,0,5")?.pitch).toBe(MIN_PITCH_DEG);
    expect(focusFromSearch("?world=0,0,2,0,120")?.pitch).toBe(MAX_PITCH_DEG);
    expect(focusFromSearch("?world=0,0,2,abc")?.yaw).toBe(CAMERA_YAW_DEG);
  });
});

describe("the orbit", () => {
  it("wraps the yaw and clamps the pitch", () => {
    expect(normalizeYaw(45)).toBe(45);
    expect(normalizeYaw(-90)).toBe(270);
    expect(normalizeYaw(725)).toBe(5);
    expect(normalizeYaw(Number.NaN)).toBe(CAMERA_YAW_DEG);
    expect(clampPitch(50)).toBe(50);
    expect(clampPitch(0)).toBe(MIN_PITCH_DEG);
    expect(clampPitch(90)).toBe(MAX_PITCH_DEG);
    expect(clampPitch(Number.NaN)).toBe(CAMERA_PITCH_DEG);
    // The designed view is inside the range, not on its edge.
    expect(CAMERA_PITCH_DEG).toBeGreaterThan(MIN_PITCH_DEG);
    expect(CAMERA_PITCH_DEG).toBeLessThan(MAX_PITCH_DEG);
  });

  it("stands clear of the island at every allowed tilt", () => {
    // The whole island has to sit between the near and the far plane from any
    // angle — at the flattest tilt the far side is nearly a full island length
    // further along the view axis, and the target may sit at either end of it.
    const span = 2 * ISLAND_HALF_M;
    for (const pitch of [MIN_PITCH_DEG, CAMERA_PITCH_DEG, MAX_PITCH_DEG]) {
      const reach = span * Math.cos((pitch * Math.PI) / 180);
      expect(CAMERA_DISTANCE_M - reach).toBeGreaterThan(1); // nothing behind the camera
      expect(CAMERA_DISTANCE_M + reach).toBeLessThan(CAMERA_FAR_M); // nothing past the far plane
    }
  });

  it("turns a full revolution across the stage width, and the island follows the pointer", () => {
    const [yawFull] = dragToOrbit(1000, 0, 1000, 600);
    expect(yawFull).toBe(ORBIT_YAW_PER_WIDTH_DEG);
    // Dragging right turns the island right, dragging down tips more of its
    // roofs into view — the same grab metaphor the pan uses.
    expect(dragToOrbit(50, 0, 1000, 600)[0]).toBeGreaterThan(0);
    expect(dragToOrbit(0, 50, 1000, 600)[1]).toBeGreaterThan(0);
    expect(dragToOrbit(10, 10, 0, 0)).toEqual([0, 0]);
  });

  it("steps the yaw onto the quarter-turn grid", () => {
    expect(stepYaw(CAMERA_YAW_DEG, 1)).toBe(90);
    expect(stepYaw(CAMERA_YAW_DEG, -1)).toBe(0);
    // Off the grid a step lands on the next stop, not 45° further on.
    expect(stepYaw(50, 1)).toBe(90);
    expect(stepYaw(50, -1)).toBe(45);
    // Four steps from the designed view come back to it.
    let yaw = CAMERA_YAW_DEG;
    for (let i = 0; i < 360 / YAW_STEP_DEG; i++) yaw = stepYaw(yaw, 1);
    expect(yaw).toBe(CAMERA_YAW_DEG);
    expect(stepYaw(0, -1)).toBe(360 - YAW_STEP_DEG);
  });

  it("eases a turn the short way round", () => {
    expect(shortestYawDelta(350, 10)).toBe(20);
    expect(shortestYawDelta(10, 350)).toBe(-20);
    expect(shortestYawDelta(0, 180)).toBe(180);
    expect(shortestYawDelta(45, 45)).toBe(0);
  });

  it("shows the same picture from a turned camera, only rotated", () => {
    // Half a turn from the designed view: the camera stands on the other side,
    // the ground basis flips, and a house front now shows its back.
    const [x, , z] = cameraOffset(CAMERA_PITCH_DEG, CAMERA_YAW_DEG + 180);
    const [x0, , z0] = cameraOffset();
    expect(x).toBeCloseTo(-x0, 3);
    expect(z).toBeCloseTo(-z0, 3);
    const back = groundBasis(CAMERA_YAW_DEG + 180);
    const front = groundBasis();
    expect(back.forward[0]).toBeCloseTo(-front.forward[0], 5);
    expect(back.forward[1]).toBeCloseTo(-front.forward[1], 5);
    expect(facesCamera(Math.atan2(CAMERA_FROM[0], CAMERA_FROM[1]))).toBe(true);
  });

  it("pans and frames along the turned axes", () => {
    const yaw = 135;
    const { right } = groundBasis(yaw);
    const [dx, dz] = dragToPan(200, 0, 64, 1280, CAMERA_PITCH_DEG, yaw);
    expect(dx).toBeCloseTo(-10 * right[0], 4);
    expect(dz).toBeCloseTo(-10 * right[1], 4);
    // A flatter view looks further into the island, so the frame runs deeper.
    const steep = visibleGroundCorners([0, 0], 64, 16 / 9, 70, yaw);
    const flat = visibleGroundCorners([0, 0], 64, 16 / 9, 25, yaw);
    const depth = (c: Array<[number, number]>) => Math.hypot(c[3][0] - c[0][0], c[3][1] - c[0][1]);
    expect(depth(flat)).toBeGreaterThan(depth(steep));
  });

  it("points the needle where north actually is on screen", () => {
    // North is (0, −1) on the ground; read through the basis it lands at
    // (sin yaw, cos yaw) on screen — straight up only when the camera is due south.
    for (const yaw of [0, 45, 135, 270]) {
      const { right, forward } = groundBasis(yaw);
      const screenX = -right[1];
      const screenUp = -forward[1];
      const rad = (northScreenAngle(yaw) * Math.PI) / 180;
      expect(screenX).toBeCloseTo(Math.sin(rad), 5);
      expect(screenUp).toBeCloseTo(Math.cos(rad), 5);
    }
    expect(isDesignedView(CAMERA_YAW_DEG, CAMERA_PITCH_DEG)).toBe(true);
    expect(isDesignedView(CAMERA_YAW_DEG + 360, CAMERA_PITCH_DEG)).toBe(true);
    expect(isDesignedView(CAMERA_YAW_DEG + 90, CAMERA_PITCH_DEG)).toBe(false);
    expect(isDesignedView(CAMERA_YAW_DEG, CAMERA_PITCH_DEG + 10)).toBe(false);
  });
});
