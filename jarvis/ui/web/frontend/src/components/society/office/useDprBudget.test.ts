import { describe, expect, it } from "vitest";
import { budgetDpr, MAX_DPR, PIXEL_BUDGET } from "./useDprBudget";

describe("office pixel budget", () => {
  it("gives a narrow side panel the full ratio the screen offers", () => {
    expect(budgetDpr(520, 1100, 1.5)).toBe(1.5);
    expect(budgetDpr(520, 1100, 3)).toBe(MAX_DPR);
  });

  it("holds a full-width 4K view near the budget instead of at the device ratio", () => {
    const dpr = budgetDpr(2100, 1350, 1.5);
    expect(dpr).toBe(1);
    expect(2100 * 1350 * dpr * dpr).toBeLessThan(1.5 * 1.5 * 2100 * 1350);
  });

  it("keeps a mid-size view inside the budget in quarter steps", () => {
    const dpr = budgetDpr(1400, 800, 2);
    expect(dpr * 4).toBe(Math.round(dpr * 4));
    expect(1400 * 800 * dpr * dpr).toBeLessThanOrEqual(PIXEL_BUDGET);
  });

  it("never drops below one and survives an unmeasured view", () => {
    expect(budgetDpr(5000, 3000, 2)).toBe(1);
    expect(budgetDpr(0, 0, 1.25)).toBe(1.25);
  });
});
