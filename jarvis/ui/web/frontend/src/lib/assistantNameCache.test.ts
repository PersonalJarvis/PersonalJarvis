/**
 * The assistant name shown at boot must never be a trademarked placeholder
 * (e.g. "Jarvis" / Marvel), because it is briefly visible before the real,
 * user-chosen name resolves. These tests lock (a) the round-trip cache so the
 * user's own name paints instantly on the next boot, and (b) the trademark-free
 * neutral fallback used on the first run.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  ASSISTANT_NAME_CACHE_KEY,
  NEUTRAL_ASSISTANT_NAME,
  readCachedAssistantName,
  writeCachedAssistantName,
} from "@/lib/assistantNameCache";

describe("assistantNameCache", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("returns the neutral fallback when nothing is cached", () => {
    expect(readCachedAssistantName()).toBe(NEUTRAL_ASSISTANT_NAME);
  });

  it("round-trips the resolved name for an instant next-boot paint", () => {
    writeCachedAssistantName("Nico");
    expect(readCachedAssistantName()).toBe("Nico");
    expect(localStorage.getItem(ASSISTANT_NAME_CACHE_KEY)).toBe("Nico");
  });

  it("trims whitespace and ignores blank writes (never blanks the wordmark)", () => {
    writeCachedAssistantName("  Athena  ");
    expect(readCachedAssistantName()).toBe("Athena");

    writeCachedAssistantName("   ");
    // Blank write is a no-op — the previous value survives.
    expect(readCachedAssistantName()).toBe("Athena");
  });

  it("honours a caller-supplied fallback", () => {
    expect(readCachedAssistantName("")).toBe("");
  });

  // Trademark guard: the neutral fallback must be a generic word, never a
  // product or character name. This is the regression fence for the boot-time
  // "Jarvis" flash that prompted this cache.
  it("never falls back to a trademarked name", () => {
    expect(NEUTRAL_ASSISTANT_NAME).not.toMatch(/jarvis/i);
    expect(readCachedAssistantName()).not.toMatch(/jarvis/i);
  });
});
