import { describe, expect, it } from "vitest";

import { studioStyle } from "./CodingFloorLook";
import { CODING_SCENE, CODING_STUDIOS, DEPARTMENT_TINTS, OFFICE } from "./officePalette";

describe("coding floor look", () => {
  it("furnishes the six departments a floor can hold as six different studios", () => {
    const styles = Array.from({ length: 6 }, (_, i) => studioStyle(i));
    expect(new Set(styles).size).toBe(6);
    expect(new Set(styles.map((s) => s.pattern)).size).toBe(6);
    expect(new Set(styles.map((s) => s.carpet)).size).toBe(6);
    expect(new Set(styles.map((s) => s.slat)).size).toBe(6);
  });

  it("cycles the studios for any tint index, never failing on one past the set", () => {
    expect(studioStyle(CODING_STUDIOS.length)).toBe(studioStyle(0));
    expect(studioStyle(-1)).toBe(studioStyle(CODING_STUDIOS.length - 1));
  });

  it("shares no floor colour with the agents office below", () => {
    expect(CODING_SCENE.space).not.toBe(OFFICE.space);
    const below = new Set<string>(DEPARTMENT_TINTS);
    for (const studio of CODING_STUDIOS) expect(below.has(studio.carpet)).toBe(false);
  });
});
