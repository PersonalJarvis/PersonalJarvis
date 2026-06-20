import { describe, expect, it } from "vitest";
import { deriveAssistantName } from "./deriveAssistantName";

describe("deriveAssistantName", () => {
  it("strips a wake prefix and title-cases", () => {
    expect(deriveAssistantName("Hey Alex")).toBe("Alex");
    expect(deriveAssistantName("hey computer")).toBe("Computer");
    expect(deriveAssistantName("ok friday")).toBe("Friday");
    expect(deriveAssistantName("Micron")).toBe("Micron");
    expect(deriveAssistantName("micron")).toBe("Micron");
  });

  it("returns empty string for blank input", () => {
    expect(deriveAssistantName("")).toBe("");
    expect(deriveAssistantName("   ")).toBe("");
  });

  it("keeps an all-prefix phrase rather than emptying it", () => {
    // mirrors backend phrase_core: never returns empty for a non-empty phrase
    expect(deriveAssistantName("Hey")).toBe("Hey");
  });

  it("strips multiple leading wake prefixes", () => {
    expect(deriveAssistantName("ok hey nova")).toBe("Nova");
    expect(deriveAssistantName("Hey Hey Nova")).toBe("Nova");
  });
});
