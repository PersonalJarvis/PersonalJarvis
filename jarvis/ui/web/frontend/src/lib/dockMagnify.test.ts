import { describe, expect, it } from "vitest";
import {
  DOCK_MAX_SCALE,
  DOCK_RADIUS_UNITS,
  dockSlotAt,
  layoutDock,
  magnifyScale,
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
    expect(items.map((i) => i.size)).toEqual([32, 32, 32, 32]);
    expect(extent).toBe(4 * 40 + 8);
  });

  it("magnifies the icon under the pointer, its neighbours less — and moves nothing", () => {
    const rest = layoutDock(5, 32, 8, null);
    // Pointer exactly on the centre of the third icon.
    const { items, extent } = layoutDock(5, 32, 8, rest.items[2].center);

    expect(items[2].scale).toBeCloseTo(DOCK_MAX_SCALE, 6);
    expect(items[2].size).toBeCloseTo(32 * DOCK_MAX_SCALE, 6);
    expect(items[1].scale).toBeGreaterThan(1);
    expect(items[1].scale).toBeLessThan(items[2].scale);
    expect(items[3].scale).toBeCloseTo(items[1].scale, 9);

    // The column is rigid: every centre is its rest centre, the extent is the
    // rest extent. Only sizes changed.
    expect(items.map((i) => i.center)).toEqual(rest.items.map((i) => i.center));
    expect(extent).toBe(rest.extent);
  });

  it("grows each icon around its own centre, so a hovered icon stays where it was", () => {
    const rest = layoutDock(9, 30, 8, null);
    for (let k = 0; k < 9; k++) {
      const { items } = layoutDock(9, 30, 8, rest.items[k].center);
      expect(items[k].center).toBe(rest.items[k].center);
      expect(items[k].center - items[k].size / 2).toBeLessThan(rest.items[k].center - 15);
      expect(items[k].center + items[k].size / 2).toBeGreaterThan(rest.items[k].center + 15);
    }
  });

  it("leaves distant icons untouched", () => {
    const rest = layoutDock(12, 32, 8, null);
    const { items } = layoutDock(12, 32, 8, rest.items[0].center);
    expect(items[11].scale).toBe(1);
    expect(items[11].size).toBe(32);
  });

  it("keeps the peak overlap of neighbouring boxes to a few px", () => {
    // Boxes may touch at the peak (only the hovered one paints a surface), but
    // a hill so steep that neighbours swallow each other would read as a mess.
    const rest = layoutDock(7, 30, 8, null);
    const { items } = layoutDock(7, 30, 8, rest.items[3].center);
    const overlap = items[3].size / 2 + items[2].size / 2 - (items[3].center - items[2].center);
    expect(overlap).toBeLessThan(6);
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
