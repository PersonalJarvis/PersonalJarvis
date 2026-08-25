import { afterEach, describe, expect, it } from "vitest";

import {
  LOCAL_MODELS_SEED_KEY,
  clearLocalModelsSeed,
  readLocalModelsSeed,
  writeLocalModelsSeed,
} from "./localModelsSeed";

afterEach(() => {
  window.localStorage.removeItem(LOCAL_MODELS_SEED_KEY);
});

describe("localModelsSeed", () => {
  it("reads null when nothing is stored", () => {
    expect(readLocalModelsSeed()).toBeNull();
  });

  it("round-trips a provider id and ignores empty writes", () => {
    writeLocalModelsSeed("ollama");
    expect(readLocalModelsSeed()).toBe("ollama");
    writeLocalModelsSeed("   ");
    expect(readLocalModelsSeed()).toBe("ollama");
  });

  it("clears the seed", () => {
    writeLocalModelsSeed("ollama");
    clearLocalModelsSeed();
    expect(readLocalModelsSeed()).toBeNull();
  });
});
