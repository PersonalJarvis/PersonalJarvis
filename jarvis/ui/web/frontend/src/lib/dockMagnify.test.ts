import { describe, expect, it } from "vitest";
import { DOCK_MAX_SCALE, DOCK_RADIUS_UNITS, layoutDock, magnifyScale } from "@/lib/dockMagnify";

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
