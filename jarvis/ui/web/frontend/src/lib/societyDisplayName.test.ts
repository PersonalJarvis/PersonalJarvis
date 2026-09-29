/**
 * The society lead follows the wake-word-derived assistant name for ANY
 * name — never a hardcoded product name. Specialists keep their roster name.
 */
import { describe, expect, it } from "vitest";

import { societyDisplayName } from "./societyDisplayName";

describe("societyDisplayName", () => {
  it("shows the wake-word name for the lead (wake-word agnostic)", () => {
    for (const name of ["Hanna", "Ruben", "Athena", "Nova", "Computer"]) {
      expect(societyDisplayName({ tier: "lead", name: "Jarvis" }, name)).toBe(name);
    }
  });

  it("falls back to the neutral name — never the backend name", () => {
    expect(societyDisplayName({ tier: "lead", name: "Jarvis" }, "")).toBe("Assistant");
    expect(societyDisplayName({ tier: "lead", name: "Jarvis" }, "   ")).toBe("Assistant");
    expect(societyDisplayName({ tier: "lead", name: "Jarvis" }, "")).not.toBe("Jarvis");
  });

  it("keeps the roster name for specialists and orchestrators", () => {
    expect(societyDisplayName({ tier: "specialist", name: "Scout" }, "Hanna")).toBe("Scout");
    expect(societyDisplayName({ tier: "orchestrator", name: "Planner" }, "Hanna")).toBe("Planner");
  });
});
