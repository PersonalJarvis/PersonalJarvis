import { describe, expect, it } from "vitest";

import { prettyProviderName } from "@/lib/prettyProviderName";

describe("prettyProviderName", () => {
  it("names the Vertex realtime transport the API-Keys card uses", () => {
    expect(prettyProviderName("vertex-live")).toBe("Vertex AI Live");
    expect(prettyProviderName("vertex")).toBe("Vertex AI");
  });

  it("keeps the names the sidebar footer already pins", () => {
    expect(prettyProviderName("openrouter")).toBe("OpenRouter");
    expect(prettyProviderName("claude-api")).toBe("Claude (API)");
  });

  it("names every realtime transport the deck can show", () => {
    expect(prettyProviderName("openai-realtime")).toBe("OpenAI Realtime");
    expect(prettyProviderName("gemini-live")).toBe("Gemini Live");
    expect(prettyProviderName("vertex-live")).toBe("Vertex AI Live");
    expect(prettyProviderName("local-realtime")).toBe("Self-hosted realtime");
  });

  it("does not invent a blank for an unknown id", () => {
    expect(prettyProviderName("future-provider")).toBe("future-provider");
  });

  it("renders unset / unknown as an em dash", () => {
    expect(prettyProviderName("")).toBe("—");
    expect(prettyProviderName("unknown")).toBe("—");
    expect(prettyProviderName("  ")).toBe("—");
  });
});
