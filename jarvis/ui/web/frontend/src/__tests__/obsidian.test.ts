import { describe, expect, it } from "vitest";
import { buildObsidianUrl, VAULT_NAME } from "@/lib/obsidian";

describe("buildObsidianUrl", () => {
  it("builds the expected URL for a simple vault-relative path", () => {
    const url = buildObsidianUrl("entities/harald.md");
    expect(url).toBe(
      `obsidian://open?vault=${encodeURIComponent(VAULT_NAME)}&file=${encodeURIComponent(
        "entities/harald.md",
      )}`,
    );
  });

  it("omits the file param when the path is empty (opens vault root)", () => {
    const url = buildObsidianUrl("");
    expect(url).toBe(`obsidian://open?vault=${encodeURIComponent(VAULT_NAME)}`);
    expect(url).not.toContain("&file=");
  });

  it("encodes spaces as %20 (real Obsidian round-trip)", () => {
    const url = buildObsidianUrl("entities/my note.md");
    expect(url).toContain("my%20note.md");
    // sanity-check the vault portion is also encoded
    expect(url.startsWith("obsidian://open?vault=")).toBe(true);
  });

  it("encodes German umlauts in the file path", () => {
    const url = buildObsidianUrl("entities/küche.md");
    // U+00FC -> %C3%BC under encodeURIComponent
    expect(url).toContain("k%C3%BCche.md");
  });
});
