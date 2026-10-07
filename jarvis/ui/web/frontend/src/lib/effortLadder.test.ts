import { describe, expect, it } from "vitest";
import { effortLadder, snapEffort } from "./effortLadder";

const CLAUDE = ["low", "medium", "high", "xhigh", "max"];

describe("snapEffort", () => {
  it("keeps a level the ladder offers", () => {
    expect(snapEffort("medium", CLAUDE, "high")).toBe("medium");
  });

  it("folds onto the lower neighbour, as the backend does", () => {
    // Claude Code runs xhigh as high on Opus 4.6.
    expect(snapEffort("xhigh", ["low", "medium", "high", "max"], "high")).toBe("high");
    expect(snapEffort("medium", ["low", "high"], "medium")).toBe("low");
    expect(snapEffort("ultra", ["low", "medium", "high", "xhigh", "max"], "medium")).toBe("max");
  });

  it("turns the agent's default into the provider's default level when the ladder has none", () => {
    // Never the lowest level: an empty pick once became "low".
    expect(snapEffort("", CLAUDE, "high")).toBe("high");
    expect(snapEffort("", ["", "low", "high"], "")).toBe("");
  });

  it("has no level for a model without an effort knob", () => {
    expect(snapEffort("medium", [], "high")).toBe("");
  });
});

describe("effortLadder", () => {
  const provider = { effort_levels: CLAUDE };
  const models = [
    { id: "claude-opus-4-6", label: "Opus 4.6", efforts: ["low", "medium", "high", "max"] },
    { id: "claude-haiku-4-5", label: "Haiku 4.5", efforts: [] },
    { id: "claude-opus-5-5", label: "Opus 5.5" },
  ];

  it("narrows to the model's own levels when the catalog lists them", () => {
    expect(effortLadder(provider, models, "claude-opus-4-6")).toEqual(["low", "medium", "high", "max"]);
    expect(effortLadder(provider, models, "claude-haiku-4-5")).toEqual([]);
    expect(effortLadder(provider, models, "claude-opus-5-5")).toEqual(CLAUDE);
    expect(effortLadder(null, models, "claude-opus-5-5")).toEqual([]);
  });
});
