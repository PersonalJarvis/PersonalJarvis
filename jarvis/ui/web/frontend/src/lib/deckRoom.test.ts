import { describe, expect, it } from "vitest";
import {
  CAMERA,
  FLOOR_Y,
  MASCOT,
  PANEL_SCALE,
  ROOM_PANELS,
  cameraTarget,
  coverFit,
  followAlpha,
  panelBottom,
  wallSize,
} from "@/lib/deckRoom";

describe("deckRoom", () => {
  it("keeps every panel on or above the floor", () => {
    for (const p of ROOM_PANELS) {
      expect(panelBottom(p), p.id).toBeGreaterThanOrEqual(FLOOR_Y - 0.05);
    }
  });

  it("puts what is acted on in front and what is looked at on the wall", () => {
    const byId = Object.fromEntries(ROOM_PANELS.map((p) => [p.id, p]));
    for (const near of ["log", "activity", "terminals"] as const) {
      expect(byId[near].standing).toBe(true);
      expect(byId[near].position[2]).toBeGreaterThan(1.5);
    }
    for (const wall of ["response", "wiki", "capture"] as const) {
      expect(byId[wall].standing).toBe(false);
      expect(byId[wall].position[2]).toBeLessThan(1);
    }
  });

  it("turns left panels toward the viewer's right and right panels the other way", () => {
    const byId = Object.fromEntries(ROOM_PANELS.map((p) => [p.id, p]));
    expect(byId.log.rotationY).toBeGreaterThan(0);
    expect(byId.activity.rotationY).toBeGreaterThan(0);
    expect(byId.terminals.rotationY).toBeLessThan(0);
    expect(byId.wiki.rotationY).toBeLessThan(0);
  });

  it("keeps the mascot on the floor in front of the wall, and the camera in front of everything", () => {
    expect(MASCOT.position[1]).toBe(FLOOR_Y);
    expect(MASCOT.position[2]).toBeGreaterThan(0);
    for (const p of ROOM_PANELS) expect(p.position[2]).toBeLessThan(CAMERA.position[2]);
    expect(MASCOT.position[2]).toBeLessThan(CAMERA.position[2]);
  });

  it("sizes the wall to fill the view at the wall's plane, with overscan", () => {
    const { width, height } = wallSize(16 / 9);
    const visible = 2 * CAMERA.position[2] * Math.tan((CAMERA.fov * Math.PI) / 360);
    expect(height).toBeGreaterThan(visible);
    expect(width / height).toBeCloseTo(16 / 9, 2);
  });

  it("cover-fits a texture like background-size: cover", () => {
    // a wide picture on a squarer plane: crop the sides, centred
    const wide = coverFit(16 / 9, 1);
    expect(wide.repeat[1]).toBe(1);
    expect(wide.repeat[0]).toBeCloseTo(9 / 16, 3);
    expect(wide.offset[0]).toBeCloseTo((1 - 9 / 16) / 2, 3);
    // a tall picture on a wide plane: crop top and bottom
    const tall = coverFit(1, 2);
    expect(tall.repeat[0]).toBe(1);
    expect(tall.repeat[1]).toBeCloseTo(0.5, 3);
    expect(tall.offset[1]).toBeCloseTo(0.25, 3);
    // same aspect: nothing cropped
    expect(coverFit(1.5, 1.5)).toEqual({ repeat: [1, 1], offset: [0, 0] });
  });

  it("sways the camera with the pointer, clamped, and never changes its distance", () => {
    const rest = cameraTarget(0, 0);
    expect(rest).toEqual([CAMERA.position[0], CAMERA.position[1], CAMERA.position[2]]);
    const right = cameraTarget(1, 0);
    expect(right[0]).toBeCloseTo(CAMERA.position[0] + CAMERA.swayX, 3);
    expect(cameraTarget(5, 5)).toEqual(cameraTarget(1, 1));
    expect(cameraTarget(-1, -1)[2]).toBe(CAMERA.position[2]);
  });

  it("follows with an exponential factor between 0 and 1 that grows with dt", () => {
    expect(followAlpha(0)).toBe(0);
    expect(followAlpha(1 / 60)).toBeGreaterThan(0);
    expect(followAlpha(1 / 60)).toBeLessThan(followAlpha(1 / 30));
    expect(followAlpha(10)).toBeLessThanOrEqual(1);
  });

  it("renders panels at a readable DOM size", () => {
    for (const p of ROOM_PANELS) {
      expect(p.width * PANEL_SCALE).toBeGreaterThan(0.5);
      expect(p.width).toBeLessThanOrEqual(800);
    }
  });
});
