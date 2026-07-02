/**
 * Client-side API-key format recognition.
 *
 * Pure, no network: the entered key is NEVER sent to the backend just to
 * classify it (latency + it keeps the secret on the client until the user
 * deliberately saves). The motivating case is the 2026-06-22 forensic — a user
 * topped up a Google AI Studio key while Jarvis was wired to a Vertex AI
 * service account. Surfacing "this looks like an AI Studio key" / "this is a
 * Vertex service-account JSON, not an AI Studio key" at type-time prevents that
 * whole class of mix-up. It only ever HINTS; it never blocks a save.
 */

export type KeyFormatKind =
  | "google-aistudio"
  | "vertex-service-account"
  | "anthropic"
  | "openai"
  | "openrouter"
  | "xai"
  | "cartesia"
  | "elevenlabs"
  | "groq"
  | "unknown";

export interface KeyFormatHint {
  kind: KeyFormatKind;
  /** Short human label, e.g. "Google AI Studio key". */
  label: string;
  /** Optional contextual note (e.g. the AI-Studio-vs-Vertex clarification). */
  note?: string;
}

/**
 * Best-effort classification of a pasted credential by its shape. Returns
 * `null` for blank input. Prefix order matters: the more specific `sk-ant-` /
 * `sk-or-` must be tested before the generic `sk-` (OpenAI).
 */
export function detectKeyFormat(value: string): KeyFormatHint | null {
  const v = value.trim();
  if (!v) return null;

  // Vertex AI service account = a JSON blob with "type": "service_account".
  if (v.startsWith("{") && /"type"\s*:\s*"service_account"/.test(v)) {
    return {
      kind: "vertex-service-account",
      label: "Vertex AI service account",
      note: "This is a Vertex AI service-account file, not an AI Studio key — it bills a separate Google Cloud project.",
    };
  }
  if (/^sk-ant-/.test(v)) return { kind: "anthropic", label: "Anthropic API key" };
  if (/^sk-or-/.test(v)) return { kind: "openrouter", label: "OpenRouter API key" };
  if (/^sk_car_/.test(v)) return { kind: "cartesia", label: "Cartesia API key" };
  // ElevenLabs keys start with `sk_` (tested AFTER the more specific `sk_car_`
  // so Cartesia wins its own prefix). Older 32-char hex keys fall through to
  // "unknown" — harmless, since the hint never blocks a save.
  if (/^sk_/.test(v)) return { kind: "elevenlabs", label: "ElevenLabs API key" };
  if (/^gsk_/.test(v)) return { kind: "groq", label: "Groq API key" };
  if (/^xai-/.test(v)) return { kind: "xai", label: "xAI (Grok) API key" };
  if (/^AIza/.test(v) || /^AQ\./.test(v)) {
    return {
      kind: "google-aistudio",
      label: "Google AI Studio key",
      note: "Looks like a Google AI Studio key — Jarvis uses it directly (Vertex stays off).",
    };
  }
  if (/^sk-/.test(v)) return { kind: "openai", label: "OpenAI API key" };
  return { kind: "unknown", label: "Unrecognized key format" };
}

/**
 * The key kind a given Credential-Manager slot expects, or `null` when the slot
 * has no recognizable format (e.g. a file path, a generic token). Used to warn
 * on a mismatch (an Anthropic key pasted into the OpenAI field).
 */
export function expectedKindForSecret(secretKey: string): KeyFormatKind | null {
  switch (secretKey) {
    case "gemini_api_key":
      return "google-aistudio";
    case "anthropic_api_key":
      return "anthropic";
    case "openai_api_key":
    case "codex_openai_api_key":
      return "openai";
    case "openrouter_api_key":
      return "openrouter";
    case "grok_api_key":
      return "xai";
    case "cartesia_api_key":
      return "cartesia";
    case "elevenlabs_api_key":
      return "elevenlabs";
    case "groq_api_key":
      return "groq";
    default:
      return null;
  }
}

export interface KeyMatchResult {
  /** False only when we recognize the key AND it's the wrong kind for the slot. */
  match: boolean;
  /** The detected format of the entered value (null for blank input). */
  detected: KeyFormatHint | null;
  /** The format the slot expects (null when the slot has no known format). */
  expected: KeyFormatKind | null;
}

/**
 * Compares an entered value against the format its slot expects. Stays neutral
 * (`match: true`) for blank input, for slots without a known format, and for
 * an unrecognized but possibly-valid key — it only reports `match: false` when
 * the value is confidently a DIFFERENT known provider's key (the actionable
 * mistake). Never blocks; the UI shows this as a soft warning.
 */
export function keyMatchesSecret(secretKey: string, value: string): KeyMatchResult {
  const detected = detectKeyFormat(value);
  const expected = expectedKindForSecret(secretKey);
  if (!detected || expected === null) {
    return { match: true, detected, expected };
  }
  if (detected.kind === "unknown") {
    return { match: true, detected, expected };
  }
  return { match: detected.kind === expected, detected, expected };
}
