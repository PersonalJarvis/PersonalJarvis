import { describe, expect, it } from "vitest";

import {
  OLLAMA_BAKEABLE_KEYS,
  OLLAMA_MODEL_OPTION_KEYS,
  compactOllamaModelOptions,
  needsProfileAlias,
} from "./ollamaModelOptions";

describe("ollamaModelOptions", () => {
  it("lists every key once and every bakeable key is a known key", () => {
    expect(new Set(OLLAMA_MODEL_OPTION_KEYS).size).toBe(OLLAMA_MODEL_OPTION_KEYS.length);
    for (const key of OLLAMA_BAKEABLE_KEYS) {
      expect(OLLAMA_MODEL_OPTION_KEYS).toContain(key);
    }
  });

  it("compacts unset knobs out of a PUT body", () => {
    expect(
      compactOllamaModelOptions({
        num_ctx: 16384,
        num_gpu: null,
        temperature: undefined,
        stop: [],
        think: false,
      }),
    ).toEqual({ num_ctx: 16384, think: false });
  });

  it("knows when a set needs a profile alias", () => {
    expect(needsProfileAlias({ temperature: 0.2, keep_alive: "30m", think: "low" })).toBe(false);
    expect(needsProfileAlias({ num_ctx: 8192 })).toBe(true);
    expect(needsProfileAlias({ stop: [] })).toBe(false);
  });
});
