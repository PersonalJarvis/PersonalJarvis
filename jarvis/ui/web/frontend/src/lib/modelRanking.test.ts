import { describe, expect, it } from "vitest";

import { rankModels } from "./modelRanking";

const ids = (models: { id: string }[]) => models.map((model) => model.id);

describe("rankModels", () => {
  it("keeps the newest version of each line and folds earlier ones", () => {
    const { current, older } = rankModels([
      { id: "claude-opus-5-5", label: "Claude Opus 5.5" },
      { id: "claude-fable-5-1", label: "Claude Fable 5.1" },
      { id: "claude-fable-5", label: "Claude Fable 5" },
      { id: "claude-opus-5", label: "Claude Opus 5" },
      { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
      { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
      { id: "opusplan", label: "Opus plans, Sonnet builds" },
      { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
      { id: "claude-sonnet-4-5-20250929", label: "Claude Sonnet 4.5" },
    ]);
    expect(ids(current)).toEqual(["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-4-5-20251001"]);
    expect(ids(older)).toEqual(["claude-fable-5", "claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-5-20250929", "opusplan"]);
  });

  it("keeps variants of the newest version together in the lineup", () => {
    const { current } = rankModels([
      { id: "claude-opus-5", label: "Claude Opus 5" },
      { id: "claude-opus-5[1m]", label: "Claude Opus 5", note: "1M context" },
    ]);
    expect(ids(current)).toEqual(["claude-opus-5", "claude-opus-5[1m]"]);
  });

  it("treats named variants as their own lines", () => {
    const { current, older } = rankModels([
      { id: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
      { id: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
      { id: "gpt-5.5", label: "GPT-5.5" },
      { id: "gpt-5.2", label: "GPT-5.2" },
    ]);
    expect(ids(current)).toEqual(["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.5"]);
    expect(ids(older)).toEqual(["gpt-5.2"]);
  });

  it("sorts the lineup newest first and keeps the default on top", () => {
    const { current } = rankModels([
      { id: "", label: "Default model" },
      { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
      { id: "gemini-3.1-pro", label: "Gemini 3.1 Pro" },
    ]);
    expect(ids(current)).toEqual(["", "gemini-3.1-pro", "gemini-2.5-flash"]);
  });

  it("folds nothing when no model carries a version", () => {
    const models = [{ id: "auto", label: "Auto" }, { id: "composer", label: "Composer" }];
    expect(rankModels(models)).toEqual({ current: models, older: [] });
  });
});
