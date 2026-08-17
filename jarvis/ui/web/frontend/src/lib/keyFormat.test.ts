import { describe, expect, it } from "vitest";
import {
  detectKeyFormat,
  expectedKindForSecret,
  keyFormatConfirmed,
  keyMatchesSecret,
} from "./keyFormat";

describe("detectKeyFormat", () => {
  it("returns null for blank input", () => {
    expect(detectKeyFormat("")).toBeNull();
    expect(detectKeyFormat("   ")).toBeNull();
  });

  it("recognizes Google AI Studio keys (AIza / AQ.)", () => {
    expect(detectKeyFormat("AIzaSyABCDEF1234567890")?.kind).toBe("google-aistudio");
    expect(detectKeyFormat("AQ.Ab8RN6...rest")?.kind).toBe("google-aistudio");
  });

  it("tells AQ. keys they may be Vertex express (auto-routed), never 'Vertex stays off'", () => {
    // AQ. is issued by BOTH AI Studio and Vertex express — the hint must not
    // promise a fixed endpoint (the old text claimed "Vertex stays off" and
    // sent express-key users into a silent auth dead end).
    const hint = detectKeyFormat("AQ.Ab8RN6...rest");
    expect(hint?.label).toBe("Google API key (AI Studio or Vertex)");
    expect(hint?.note).toContain("decides the endpoint");
    // The AIza label stays endpoint-NEUTRAL too. It used to read "Google AI
    // Studio key", which became actively misleading once the same shape could be
    // a Cloud API key restricted to aiplatform.googleapis.com — a Vertex user
    // would be told their correct key was an AI Studio one, in the very field
    // this module exists to keep straight.
    const classic = detectKeyFormat("AIzaSyABCDEF1234567890");
    expect(classic?.label).toBe("Google API key");
    expect(classic?.note).toContain("Vertex AI");
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

  // A Vertex key wears the SAME AIza / AQ. shape as an AI Studio one — which
  // account it belongs to is invisible in the string. Warning about that would
  // fire on every correctly pasted Vertex key, so the two Google kinds are
  // treated as compatible in both directions.
  it("never cries mismatch between the two indistinguishable Google kinds", () => {
    for (const slot of ["vertex_api_key", "realtime_vertex_api_key"]) {
      for (const key of ["AIzaSy123", "AQ.Ab8RN6xyz"]) {
        expect(keyMatchesSecret(slot, key).match).toBe(true);
      }
    }
    expect(keyMatchesSecret("gemini_api_key", "AQ.Ab8RN6xyz").match).toBe(true);
  });

  it("still flags a genuinely foreign key in a Vertex field", () => {
    const r = keyMatchesSecret("vertex_api_key", "sk-ant-api03-xyz");
    expect(r.match).toBe(false);
    expect(r.detected?.kind).toBe("anthropic");
  });

  it("flags a service-account JSON pasted into the Vertex KEY field", () => {
    // The project path takes a FILE PATH in jarvis.toml, not the JSON itself in
    // the key box — the most likely Vertex-specific mistake there is.
    const r = keyMatchesSecret("vertex_api_key", '{"type":"service_account"}');
    expect(r.match).toBe(false);
    expect(r.detected?.kind).toBe("vertex-service-account");
  });
});

describe("keyFormatConfirmed", () => {
  it("gives a Vertex slot the green tick for a Google-shaped key", () => {
    // Without the compatibility rule the tick never appears on a Vertex card,
    // leaving every correct key looking unverified.
    expect(keyFormatConfirmed(keyMatchesSecret("vertex_api_key", "AIzaSy123"))).toBe(true);
  });

  it("withholds it for a key we do not recognize", () => {
    // match=true there means "no complaint", which is not the same as "right".
    const r = keyMatchesSecret("vertex_api_key", "some-opaque-token");
    expect(r.match).toBe(true);
    expect(keyFormatConfirmed(r)).toBe(false);
  });

  it("withholds it for a confirmed mismatch", () => {
    expect(keyFormatConfirmed(keyMatchesSecret("vertex_api_key", "sk-ant-x"))).toBe(false);
  });
});
