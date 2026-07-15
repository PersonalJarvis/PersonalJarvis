import { describe, expect, it } from "vitest";
import { detectKeyFormat, expectedKindForSecret, keyMatchesSecret } from "./keyFormat";

describe("detectKeyFormat", () => {
  it("returns null for blank input", () => {
    expect(detectKeyFormat("")).toBeNull();
    expect(detectKeyFormat("   ")).toBeNull();
  });

  it("recognizes Google AI Studio keys (AIza / AQ.)", () => {
    expect(detectKeyFormat("AIzaSyABCDEF1234567890")?.kind).toBe("google-aistudio");
    expect(detectKeyFormat("AQ.Ab8RN6...rest")?.kind).toBe("google-aistudio");
  });

  it("recognizes a Vertex AI service-account JSON, not an AI Studio key", () => {
    const sa = '{ "type": "service_account", "project_id": "x" }';
    expect(detectKeyFormat(sa)?.kind).toBe("vertex-service-account");
  });

  it("distinguishes the sk- prefixes (anthropic / openrouter / openai)", () => {
    expect(detectKeyFormat("sk-ant-api03-xyz")?.kind).toBe("anthropic");
    expect(detectKeyFormat("sk-or-v1-xyz")?.kind).toBe("openrouter");
    expect(detectKeyFormat("sk-proj-abc123")?.kind).toBe("openai");
  });

  it("recognizes an NVIDIA NIM key (nvapi-)", () => {
    expect(detectKeyFormat("nvapi-abc123def456")?.kind).toBe("nvidia");
  });

  it("recognizes xAI, Cartesia, ElevenLabs and Groq keys", () => {
    expect(detectKeyFormat("xai-abc123")?.kind).toBe("xai");
    expect(detectKeyFormat("sk_car_abc123")?.kind).toBe("cartesia");
    // Cartesia's more specific sk_car_ must win over the generic ElevenLabs sk_.
    expect(detectKeyFormat("sk_elevenlabsvoicekey123")?.kind).toBe("elevenlabs");
    expect(detectKeyFormat("gsk_abc123")?.kind).toBe("groq");
  });

  it("falls back to unknown for an unrecognized format", () => {
    expect(detectKeyFormat("hello-world")?.kind).toBe("unknown");
  });
});

describe("expectedKindForSecret", () => {
  it("maps secret slots to the key kind they expect", () => {
    expect(expectedKindForSecret("gemini_api_key")).toBe("google-aistudio");
    expect(expectedKindForSecret("anthropic_api_key")).toBe("anthropic");
    expect(expectedKindForSecret("openai_api_key")).toBe("openai");
    expect(expectedKindForSecret("codex_openai_api_key")).toBe("openai");
    expect(expectedKindForSecret("nvidia_api_key")).toBe("nvidia");
    expect(expectedKindForSecret("grok_api_key")).toBe("xai");
    expect(expectedKindForSecret("cartesia_api_key")).toBe("cartesia");
    expect(expectedKindForSecret("elevenlabs_api_key")).toBe("elevenlabs");
    expect(expectedKindForSecret("jarvis_agent_openai_api_key")).toBe("openai");
    expect(expectedKindForSecret("jarvis_agent_gemini_api_key")).toBe("google-aistudio");
    expect(expectedKindForSecret("realtime_grok_api_key")).toBe("xai");
  });

  it("returns null for slots without a known key format", () => {
    expect(expectedKindForSecret("google_tts_credentials_path")).toBeNull();
  });
});

describe("keyMatchesSecret", () => {
  it("confirms a matching key", () => {
    expect(keyMatchesSecret("gemini_api_key", "AIzaSy123").match).toBe(true);
  });

  it("flags a Vertex JSON pasted into the AI-Studio Gemini field", () => {
    const r = keyMatchesSecret("gemini_api_key", '{"type":"service_account"}');
    expect(r.match).toBe(false);
    expect(r.detected?.kind).toBe("vertex-service-account");
  });

  it("flags an Anthropic key pasted into the OpenAI field", () => {
    const r = keyMatchesSecret("openai_api_key", "sk-ant-api03-xyz");
    expect(r.match).toBe(false);
    expect(r.detected?.kind).toBe("anthropic");
  });

  it("stays neutral (match=true) when the slot has no known format", () => {
    expect(keyMatchesSecret("google_tts_credentials_path", "/path/to.json").match).toBe(true);
  });

  it("stays neutral for blank input", () => {
    expect(keyMatchesSecret("gemini_api_key", "").match).toBe(true);
    expect(keyMatchesSecret("gemini_api_key", "   ").detected).toBeNull();
  });
});
