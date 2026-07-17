/**
 * The agent-system display brand follows the wake-word-derived assistant name
 * for ANY name — never a hardcoded product name (2026-07-17 rebrand).
 */
import { describe, expect, it } from "vitest";

import { agentBrand, agentsBrand } from "./agentBrand";

describe("agentBrand", () => {
  it("suffixes -Agent onto any assistant name (wake-word agnostic)", () => {
    for (const name of ["Ruben", "Harald", "Athena", "Computer", "Nova Prime"]) {
      expect(agentBrand(name)).toBe(`${name}-Agent`);
      expect(agentsBrand(name)).toBe(`${name}-Agents`);
    }
  });

  it("falls back to the neutral name — never a trademarked one", () => {
    expect(agentBrand("")).toBe("Assistant-Agent");
    expect(agentBrand("   ")).toBe("Assistant-Agent");
    expect(agentBrand("")).not.toContain("Jarvis");
  });
});
