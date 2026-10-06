import { describe, expect, it } from "vitest";
import { offeredModels } from "./agentProviderPrefs";

const MODELS = [
  { id: "claude-opus-5-5", label: "Claude Opus 5.5" },
  { id: "claude-opus-5", label: "Claude Opus 5" },
  { id: "opusplan", label: "Opus plans, Sonnet builds" },
];

describe("offeredModels", () => {
  it("leaves out the models the API Keys page hid for the row", () => {
    const row = { id: "claude-api", hidden_models: ["claude-opus-5", "opusplan"] };
    expect(offeredModels(row, MODELS).map((m) => m.id)).toEqual(["claude-opus-5-5"]);
  });

  it("returns the same list when nothing is hidden", () => {
    expect(offeredModels({ id: "claude-api" }, MODELS)).toBe(MODELS);
    expect(offeredModels({ id: "claude-api", hidden_models: [] }, MODELS)).toBe(MODELS);
  });

  it("keeps the current pick listed even when it is hidden", () => {
    const row = { id: "claude-api", hidden_models: ["claude-opus-5", "opusplan"] };
    expect(offeredModels(row, MODELS, "opusplan").map((m) => m.id)).toEqual(["claude-opus-5-5", "opusplan"]);
  });

  it("prefers the lists this window saved over the row's older copy", () => {
    const row = { id: "claude-api", hidden_models: ["claude-opus-5"] };
    expect(offeredModels(row, MODELS, "", { "claude-api": ["opusplan"] }).map((m) => m.id)).toEqual([
      "claude-opus-5-5",
      "claude-opus-5",
    ]);
    // A save that left this provider with nothing hidden shows everything again.
    expect(offeredModels(row, MODELS, "", {})).toBe(MODELS);
  });
});
