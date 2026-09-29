import { describe, expect, it } from "vitest";
import { pickRealChats } from "./LiveMonitors";
import { screenFillDistance } from "./OfficeCameraRig";

describe("real chat monitors", () => {
  it("runs the real chat only on the nearest few seated agents", () => {
    const picked = pickRealChats([
      { id: "a", distance: 3 }, { id: "b", distance: 9 }, { id: "c", distance: 5 }, { id: "d", distance: 7 }, { id: "far", distance: 30 },
    ], new Set());
    expect([...picked].sort()).toEqual(["a", "c", "d"]);
  });
  it("keeps a running chat until it is clearly out of range (no flicker at the edge)", () => {
    expect(pickRealChats([{ id: "a", distance: 12 }], new Set()).has("a")).toBe(false);
    expect(pickRealChats([{ id: "a", distance: 12 }], new Set(["a"])).has("a")).toBe(true);
    expect(pickRealChats([{ id: "a", distance: 15 }], new Set(["a"])).has("a")).toBe(false);
  });
});

describe("monitor dive", () => {
  it("stands back far enough for the whole screen to fit, wider views closer", () => {
    const wide = screenFillDistance(35, 2.2);
    const narrow = screenFillDistance(35, 0.8);
    expect(narrow).toBeGreaterThan(wide);
    // At 35° the 0.38 m screen height needs about 0.6 m.
    expect(wide).toBeGreaterThan(0.55);
    expect(wide).toBeLessThan(0.7);
  });
});
