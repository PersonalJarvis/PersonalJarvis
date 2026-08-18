import { describe, expect, it } from "vitest";
import {
  DOCK_MAX_SCALE,
  DOCK_RADIUS_UNITS,
  dockSlotAt,
  layoutDock,
  magnifyScale,
  maxAnchorLift,
} from "@/lib/dockMagnify";

describe("magnifyScale", () => {
  it("is full size under the pointer and rest size beyond the radius", () => {
    expect(magnifyScale(0)).toBeCloseTo(DOCK_MAX_SCALE, 6);
    expect(magnifyScale(DOCK_RADIUS_UNITS)).toBe(1);
    expect(magnifyScale(DOCK_RADIUS_UNITS + 5)).toBe(1);
    expect(magnifyScale(Number.NaN)).toBe(1);
  });

  it("falls off smoothly and symmetrically", () => {
    const near = magnifyScale(0.5);
    const far = magnifyScale(1.5);
    expect(near).toBeGreaterThan(far);
    expect(far).toBeGreaterThan(1);
    expect(magnifyScale(-1.2)).toBeCloseTo(magnifyScale(1.2), 9);
    // Cosine window: at half the radius the lift is exactly half.
    expect(magnifyScale(DOCK_RADIUS_UNITS / 2)).toBeCloseTo(1 + (DOCK_MAX_SCALE - 1) / 2, 6);
  });
});

describe("layoutDock", () => {
  it("lays icons out at rest with the base stride when there is no pointer", () => {
    const { items, extent } = layoutDock(4, 32, 8, null);
    expect(items.map((i) => i.scale)).toEqual([1, 1, 1, 1]);
    expect(items.map((i) => i.center)).toEqual([24, 64, 104, 144]);
    expect(extent).toBe(4 * 40 + 8);
  });

  it("magnifies the icon under the pointer and pushes the rest outward", () => {
    const rest = layoutDock(5, 32, 8, null);
    // Pointer exactly on the centre of the third icon.
    const { items, extent } = layoutDock(5, 32, 8, rest.items[2].center);

    expect(items[2].scale).toBeCloseTo(DOCK_MAX_SCALE, 6);
    expect(items[1].scale).toBeGreaterThan(1);
    expect(items[3].scale).toBeCloseTo(items[1].scale, 9);
    // The row grew — nothing overlaps because everything after the big icon
    // moved by exactly the extra room it needs.
    expect(extent).toBeGreaterThan(rest.extent);
    for (let i = 1; i < items.length; i++) {
      const prevEnd = items[i - 1].center + items[i - 1].size / 2;
      const thisStart = items[i].center - items[i].size / 2;
      expect(thisStart).toBeGreaterThanOrEqual(prevEnd + 8 - 1e-9);
    }
  });

  it("leaves distant icons untouched", () => {
    const rest = layoutDock(12, 32, 8, null);
    const { items } = layoutDock(12, 32, 8, rest.items[0].center);
    expect(items[11].scale).toBe(1);
    expect(items[11].size).toBe(32);
  });
});

describe("layoutDock — pointer anchoring", () => {
  it("does not lift the row without headroom (top-anchored, as before)", () => {
    const rest = layoutDock(9, 30, 8, null);
    const { items, shift } = layoutDock(9, 30, 8, rest.items[4].center);
    expect(shift).toBe(0);
    // Everything after the hill has been pushed DOWN — the icon under the
    // pointer is no longer centred on it.
    expect(items[4].center).toBeGreaterThan(rest.items[4].center + 10);
  });

  it("keeps the icon under the pointer under the pointer when it may lift", () => {
    const rest = layoutDock(9, 30, 8, null);
    const p = rest.items[4].center;
    const { items, shift } = layoutDock(9, 30, 8, p, undefined, undefined, 40);
    expect(shift).toBeLessThan(0);
    expect(items[4].center).toBeCloseTo(p, 6);
    // Still no overlap: neighbours moved by exactly the room they need.
    for (let i = 1; i < items.length; i++) {
      const prevEnd = items[i - 1].center + items[i - 1].size / 2;
      const thisStart = items[i].center - items[i].size / 2;
      expect(thisStart).toBeGreaterThanOrEqual(prevEnd + 8 - 1e-9);
    }
  });

  it("pins the material point, not just the centre — the icon's edges stay put too", () => {
    const rest = layoutDock(9, 30, 8, null);
    const top = rest.items[4].center - 15;
    const { items } = layoutDock(9, 30, 8, top, undefined, undefined, 40);
    expect(items[4].center - items[4].size / 2).toBeCloseTo(top, 6);
  });

  it("never lifts more than the headroom allows", () => {
    const rest = layoutDock(9, 30, 8, null);
    const { shift, items } = layoutDock(9, 30, 8, rest.items[6].center, undefined, undefined, 5);
    expect(shift).toBeGreaterThanOrEqual(-5);
    expect(items[0].center - items[0].size / 2).toBeGreaterThanOrEqual(8 - 5 - 1e-9);
  });

  it("a pointer past the end pins that end, so the last icon grows towards it", () => {
    const rest = layoutDock(5, 30, 8, null);
    const bottom = rest.items[4].center + 15;
    const { items } = layoutDock(5, 30, 8, bottom + 30, undefined, undefined, 40);
    expect(items[4].center + items[4].size / 2).toBeCloseTo(bottom, 6);
  });
});

describe("dockSlotAt", () => {
  it("maps a rest position to its icon, gaps split between neighbours", () => {
    expect(dockSlotAt(8 + 15, 5, 30, 8)).toBe(0);
    expect(dockSlotAt(8 + 30 + 3, 5, 30, 8)).toBe(0); // first half of the gap
    expect(dockSlotAt(8 + 30 + 5, 5, 30, 8)).toBe(1); // second half
    expect(dockSlotAt(8 + 4 * 38 + 15, 5, 30, 8)).toBe(4);
  });

  it("is -1 outside the row", () => {
    expect(dockSlotAt(-20, 5, 30, 8)).toBe(-1);
    expect(dockSlotAt(8 + 5 * 38 + 4, 5, 30, 8)).toBe(-1);
    expect(dockSlotAt(Number.NaN, 5, 30, 8)).toBe(-1);
    expect(dockSlotAt(10, 0, 30, 8)).toBe(-1);
  });
});

describe("maxAnchorLift", () => {
  it("is the headroom the anchoring actually needs — no sample lifts further", () => {
    const need = maxAnchorLift(30, 8);
    expect(need).toBeGreaterThan(0);
    const rest = layoutDock(15, 30, 8, null);
    for (let p = rest.items[6].center - 19; p <= rest.items[8].center + 19; p += 1) {
      const { shift } = layoutDock(15, 30, 8, p, undefined, undefined, 1000);
      expect(-shift).toBeLessThanOrEqual(need + 1e-9);
    }
  });
});
