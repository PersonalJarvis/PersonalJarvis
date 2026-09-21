import { describe, expect, test } from "vitest";
import {
  aimCursor,
  beginCursor,
  easeOutCubic,
  glideDurationMs,
  projectFramePoint,
  stepCursor,
} from "./agentCursorMotion";

describe("agent cursor motion", () => {
  test("keeps a short hop visible and a long move quick", () => {
    expect(glideDurationMs(0)).toBe(0);
    expect(glideDurationMs(40)).toBe(130);
    expect(glideDurationMs(250)).toBe(180);
    expect(glideDurationMs(2000)).toBe(340);
  });

  test("eases out so the arrow is still moving as it arrives", () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    expect(easeOutCubic(0.5)).toBeCloseTo(0.875, 5);
  });

  test("places a page point inside the letterboxed picture", () => {
    const point = projectFramePoint(100, 200, 1280, 800, 640, 480);
    expect(point.scale).toBe(0.5);
    expect(point.x).toBe(50);
    expect(point.y).toBe(140);
  });

  test("a new target starts from where the arrow is, not from the old destination", () => {
    const parked = { ...beginCursor(100, 80, 1000), x: 140, y: 90, toX: 180, toY: 90 };
    const aimed = aimCursor(parked, {
      x: 400, y: 90, clickId: 2, clickX: 400, clickY: 90,
    }, 200, 1500, false);
    expect(aimed.motion.fromX).toBe(140);
    expect(aimed.motion.x).toBe(140);
    expect(aimed.motion.toX).toBe(400);
    expect(aimed.motion.duration).toBe(glideDurationMs(200));
    const halfway = stepCursor(aimed.motion, 1500 + aimed.motion.duration / 2);
    expect(halfway.motion.x).toBeCloseTo(140 + (400 - 140) * 0.875, 4);
    expect(halfway.arrivedClick).toBeNull();
    expect(halfway.moving).toBe(true);
    const landed = stepCursor(halfway.motion, 1500 + aimed.motion.duration);
    expect(landed.motion.x).toBeCloseTo(400, 4);
    expect(landed.arrivedClick?.id).toBe(2);
    expect(landed.moving).toBe(false);
  });

  test("a second report of the same point does not restart the glide", () => {
    const moving = aimCursor(beginCursor(10, 10, 0), {
      x: 80, y: 10, clickId: 0, clickX: 0, clickY: 0,
    }, 70, 20, false).motion;
    const again = aimCursor(moving, {
      x: 80, y: 10, clickId: 0, clickX: 0, clickY: 0,
    }, 70, 40, false);
    expect(again.changed).toBe(false);
    expect(again.motion.start).toBe(20);
  });

  test("reduced motion puts the arrow on the button immediately", () => {
    const aimed = aimCursor(beginCursor(0, 0, 0), {
      x: 300, y: 40, clickId: 1, clickX: 300, clickY: 40,
    }, 300, 10, true);
    expect(aimed.motion.x).toBe(300);
    expect(aimed.motion.duration).toBe(0);
    expect(stepCursor(aimed.motion, 10).arrivedClick?.id).toBe(1);
  });

  test("leaving a click early still marks that click", () => {
    const pressing = aimCursor(beginCursor(0, 0, 0), {
      x: 50, y: 20, clickId: 4, clickX: 50, clickY: 20,
    }, 80, 5, false).motion;
    const left = aimCursor(pressing, {
      x: 180, y: 20, clickId: 4, clickX: 50, clickY: 20,
    }, 90, 12, false);
    expect(left.releaseClick?.id).toBe(4);
    expect(left.motion.pendingClick).toBeNull();
  });
});
