import { describe, expect, it } from "vitest";

import { resolveVoiceEngineDisplay, type VoiceEngineDisplayInput } from "@/lib/voiceEngineDisplay";

const PIPELINE: VoiceEngineDisplayInput = {
  mode: "pipeline",
  activeProvider: "vertex-live",
  activeProviderLabel: "Vertex AI Live",
  activeModel: "gemini-live-2.5-flash-preview-native-audio-dialog",
  sessionActive: false,
  activeSessionMode: null,
  activeSessionProvider: "",
  activeSessionModel: "",
  brainProvider: "openrouter",
  brainModel: "google/gemini-3.5-flash",
};

describe("resolveVoiceEngineDisplay", () => {
  it("pipeline mode keeps the classic brain even when a realtime provider is fully configured", () => {
    const got = resolveVoiceEngineDisplay(PIPELINE);
    expect(got.tier).toBe("pipeline");
    expect(got.providerLabel).toBe("OpenRouter");
    expect(got.model).toBe("google/gemini-3.5-flash");
  });

  it("realtime mode names the realtime pick, not the dormant pipeline brain", () => {
    // The bug on the mission deck: header and orb said "openrouter" while
    // Vertex AI Live was the selected (and running) voice engine.
    const got = resolveVoiceEngineDisplay({ ...PIPELINE, mode: "realtime" });
    expect(got.tier).toBe("realtime");
    expect(got.providerId).toBe("vertex-live");
    expect(got.providerLabel).toBe("Vertex AI Live");
    expect(got.model).toBe("gemini-live-2.5-flash-preview-native-audio-dialog");
  });

  it("a running realtime session's live provider/model outrank the configured pick", () => {
    const got = resolveVoiceEngineDisplay({
      ...PIPELINE,
      mode: "realtime",
      sessionActive: true,
      activeSessionMode: "realtime",
      activeSessionProvider: "openai-realtime",
      activeSessionModel: "gpt-realtime-2.1",
    });
    expect(got.providerLabel).toBe("OpenAI Realtime");
    expect(got.model).toBe("gpt-realtime-2.1");
  });

  it("falls back to a pretty id when the backend label has not landed", () => {
    const got = resolveVoiceEngineDisplay({
      ...PIPELINE,
      mode: "realtime",
      activeProvider: "vertex-live",
      activeProviderLabel: null,
      activeModel: null,
    });
    expect(got.providerLabel).toBe("Vertex AI Live");
    expect(got.model).toBe("");
  });

  it("renders an em dash when realtime is on but no provider is resolved yet", () => {
    const got = resolveVoiceEngineDisplay({
      ...PIPELINE,
      mode: "realtime",
      activeProvider: null,
      activeProviderLabel: null,
      activeModel: null,
    });
    expect(got.providerLabel).toBe("—");
  });

  it("names Gemini Live and the self-hosted transport the same way", () => {
    expect(
      resolveVoiceEngineDisplay({
        ...PIPELINE,
        mode: "realtime",
        activeProvider: "gemini-live",
        activeProviderLabel: "Gemini Live",
        activeModel: "gemini-3.1-flash-live-preview",
      }).providerLabel,
    ).toBe("Gemini Live");
    expect(
      resolveVoiceEngineDisplay({
        ...PIPELINE,
        mode: "realtime",
        activeProvider: "local-realtime",
        activeProviderLabel: "Self-hosted realtime (OpenAI-compatible)",
        activeModel: "auto",
      }).providerLabel,
    ).toBe("Self-hosted realtime (OpenAI-compatible)");
  });

  it("keeps the registry label when a live session is still on the configured pick", () => {
    const got = resolveVoiceEngineDisplay({
      ...PIPELINE,
      mode: "realtime",
      activeProvider: "local-realtime",
      activeProviderLabel: "Self-hosted realtime (OpenAI-compatible)",
      activeModel: "auto",
      sessionActive: true,
      activeSessionMode: "realtime",
      activeSessionProvider: "local-realtime",
      activeSessionModel: "auto",
    });
    expect(got.providerLabel).toBe("Self-hosted realtime (OpenAI-compatible)");
  });
});
