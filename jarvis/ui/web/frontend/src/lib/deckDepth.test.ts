import { describe, expect, it } from "vitest";
import {
  DECK_PERSPECTIVE_PX,
  PARALLAX_MAX_PX,
  SLOT_DEPTH,
  compensatingScale,
  driftCompensation,
  parallaxShift,
  pointerOffset,
  slotDepthVars,
} from "@/lib/deckDepth";

describe("deckDepth", () => {
  it("keeps the centre column on the wall so the orb's travel lands where it measured", () => {
    expect(SLOT_DEPTH["centre-top"].z).toBe(0);
    expect(SLOT_DEPTH["centre-top"].rotateY).toBe(0);
  });

  it("puts what is acted on in front and what is looked at behind", () => {
    expect(SLOT_DEPTH["left-top"].z).toBeGreaterThan(0); // the log
    expect(SLOT_DEPTH["right-bottom"].z).toBeGreaterThan(0); // terminals
    expect(SLOT_DEPTH["right-top"].z).toBeLessThan(0); // the memory map
  });

  it("turns left slots toward the centre and right slots the other way", () => {
    expect(SLOT_DEPTH["left-top"].rotateY).toBeGreaterThan(0);
    expect(SLOT_DEPTH["right-bottom"].rotateY).toBeLessThan(0);
  });

  it("compensates the perspective so a near plane does not grow into its neighbour", () => {
    const z = 90;
    const projected = DECK_PERSPECTIVE_PX / (DECK_PERSPECTIVE_PX - z);
    expect(compensatingScale(z) * projected).toBeCloseTo(1, 3);
    expect(compensatingScale(0)).toBe(1);
    expect(compensatingScale(-90, 0.985) * (DECK_PERSPECTIVE_PX / (DECK_PERSPECTIVE_PX + 90))).toBeCloseTo(0.985, 3);
  });

  it("emits one custom property per transform input, as CSS strings", () => {
    const vars = slotDepthVars("left-bottom");
    expect(vars["--slot-z"]).toBe("90px");
    expect(vars["--slot-ry"]).toBe("4deg");
    expect(vars["--slot-par"]).toBe("1.1");
    expect(Number(vars["--slot-scale"])).toBeGreaterThan(0.9);
    expect(Number(vars["--slot-scale"])).toBeLessThan(1);
  });

  it("maps the pointer to −1…1 with the centre at zero and clamps outside the box", () => {
    const box = { left: 100, top: 50, width: 400, height: 200 };
    expect(pointerOffset(300, 150, box)).toEqual({ x: 0, y: 0 });
    expect(pointerOffset(100, 50, box)).toEqual({ x: -1, y: -1 });
    expect(pointerOffset(900, 900, box)).toEqual({ x: 1, y: 1 });
    expect(pointerOffset(10, 10, { left: 0, top: 0, width: 0, height: 0 })).toEqual({ x: 0, y: 0 });
  });

  it("pre-translates a near plane so its centre still projects onto its slot", () => {
    // A plane 90 px in front, centred 650 px left of the vanishing point:
    // projected centre = (cx + dx) · p/(p−z) must equal cx.
    const z = 90;
    const cx = -650;
    const { dx, dy } = driftCompensation(cx, 0, z);
    expect(((cx + dx) * DECK_PERSPECTIVE_PX) / (DECK_PERSPECTIVE_PX - z)).toBeCloseTo(cx, 0);
    expect(dy).toBe(0);
    // On the wall's plane nothing drifts; behind it the shift points inward.
    expect(driftCompensation(cx, 200, 0)).toEqual({ dx: 0, dy: 0 });
    expect(driftCompensation(cx, 0, -90).dx).toBeLessThan(0);
  });

  it("moves the planes against the pointer, and never more than the cap", () => {
    const shift = parallaxShift({ x: 1, y: 1 });
    expect(shift.x).toBe(-PARALLAX_MAX_PX);
    expect(Math.abs(shift.y)).toBeLessThan(PARALLAX_MAX_PX);
    expect(parallaxShift({ x: 0, y: 0 })).toEqual({ x: 0, y: 0 });
    expect(parallaxShift({ x: -0.5, y: 0 }).x).toBeGreaterThan(0);
  });
});
