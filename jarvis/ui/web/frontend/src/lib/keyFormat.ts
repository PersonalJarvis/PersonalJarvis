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
  // `AIza` — works on AI Studio, refused by Vertex (measured 2026-08-17).
  | "google-aistudio"
  // `AQ.` — issued by BOTH AI Studio and Vertex express mode, and accepted by
  // both. The one Google shape that genuinely cannot be pinned to a service.
  | "google-express"
  // What a Vertex AI slot EXPECTS. Never returned by `detectKeyFormat`: the
  // only key Vertex takes is an express key, whose `AQ.` shape it shares with
  // AI Studio. The kind exists so the UI can name what a slot wants — and so
  // `keyMatchesSecret` can accept `AQ.` there while warning about `AIza`.
  | "google-vertex"
  | "vertex-service-account"
  | "anthropic"
  | "openai"
  | "openrouter"
  | "nvidia"
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
  if (/^nvapi-/.test(v)) return { kind: "nvidia", label: "NVIDIA API key" };
  if (/^sk_car_/.test(v)) return { kind: "cartesia", label: "Cartesia API key" };
  // ElevenLabs keys start with `sk_` (tested AFTER the more specific `sk_car_`
  // so Cartesia wins its own prefix). Older 32-char hex keys fall through to
  // "unknown" — harmless, since the hint never blocks a save.
  if (/^sk_/.test(v)) return { kind: "elevenlabs", label: "ElevenLabs API key" };
  if (/^gsk_/.test(v)) return { kind: "groq", label: "Groq API key" };
  if (/^xai-/.test(v)) return { kind: "xai", label: "xAI (Grok) API key" };
  // AIza is the classic Google API-key prefix, used by AI Studio AND by a
  // Google Cloud API key restricted to aiplatform.googleapis.com. Which one it
  // is depends on the project it was created in, not on the string, so the slot
  // it is pasted into is what decides where Jarvis sends it.
  // AIza is the classic Google API-key prefix. Measured 2026-08-17: a key of
  // this shape works on AI Studio and is REFUSED by Vertex ("API keys are not
  // supported by this API") even when it was created restricted to
  // aiplatform.googleapis.com — so this kind is genuinely AI-Studio-only, and a
  // Vertex slot warns about it rather than accepting it silently.
  if (/^AIza/.test(v)) {
    return {
      kind: "google-aistudio",
      label: "Google AI Studio key",
      note: "This shape works on Google AI Studio. Vertex AI refuses it — Vertex takes only an express-mode key (AQ.) or a Cloud project via [google].vertex_project.",
    };
  }
  // AQ. is issued by BOTH Google AI Studio and Vertex AI express mode — the
  // prefix cannot tell them apart. Same kind (both Google slots accept it); in
  // the Gemini slots the backend probes once and routes the key, and in a Vertex
  // slot the endpoint is already decided by the card.
  if (/^AQ\./.test(v)) {
    return {
      kind: "google-express",
      label: "Google API key (AI Studio or Vertex express)",
      note: "AI Studio and Vertex AI express keys share this format — the card you save it under decides the endpoint, and for a Gemini card Jarvis detects it automatically.",
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
    case "realtime_gemini_api_key":
    case "jarvis_agent_gemini_api_key":
      return "google-aistudio";
    case "vertex_api_key":
    case "google_vertex_api_key":
    case "realtime_vertex_api_key":
    case "jarvis_agent_vertex_api_key":
      return "google-vertex";
    case "anthropic_api_key":
    case "jarvis_agent_anthropic_api_key":
      return "anthropic";
    case "openai_api_key":
    case "codex_openai_api_key":
    case "realtime_openai_api_key":
    case "jarvis_agent_openai_api_key":
      return "openai";
    case "openrouter_api_key":
    case "jarvis_agent_openrouter_api_key":
      return "openrouter";
    case "nvidia_api_key":
    case "jarvis_agent_nvidia_api_key":
      return "nvidia";
    case "grok_api_key":
    case "realtime_grok_api_key":
    case "jarvis_agent_grok_api_key":
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
 * Which detected kinds satisfy which expected kind, where equality is too
 * strict. Only ONE Google shape is genuinely ambiguous: `AQ.`, issued by AI
 * Studio and by Vertex express alike and accepted by both, so it must never
 * draw a warning in either slot.
 *
 * `AIza` is deliberately NOT in the Vertex row. It used to be, on the
 * assumption that a Cloud API key restricted to aiplatform would work; measured
 * 2026-08-17, Vertex refuses that key outright. Staying silent there would let
 * the user save a key that cannot ever work and find out from a failed call.
 */
const COMPATIBLE_KINDS: Partial<Record<KeyFormatKind, ReadonlySet<KeyFormatKind>>> = {
  "google-aistudio": new Set<KeyFormatKind>(["google-express"]),
  "google-vertex": new Set<KeyFormatKind>(["google-express"]),
};

/**
 * Compares an entered value against the format its slot expects. Stays neutral
 * (`match: true`) for blank input, for slots without a known format, for an
 * unrecognized but possibly-valid key, and between the two indistinguishable
 * Google kinds — it only reports `match: false` when the value is confidently a
 * DIFFERENT known provider's key (the actionable mistake). Never blocks; the UI
 * shows this as a soft warning.
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
  if (COMPATIBLE_KINDS[expected]?.has(detected.kind)) {
    return { match: true, detected, expected };
  }
  return { match: detected.kind === expected, detected, expected };
}

/**
 * Whether the entered value is POSITIVELY the right shape for its slot — the
 * condition for the green reassurance, as opposed to merely "no complaint".
 *
 * Separate from `match` because the two differ: `match` is also true for a key
 * we simply do not recognize, where saying "looks right" would be a guess. An
 * `AQ.` express key counts as confirmed in a Vertex slot even though the kinds
 * are not equal, since that IS the shape Vertex takes — it is the account behind
 * it that no local check can see, and withholding the tick over that would leave
 * every correctly pasted express key looking unverified.
 */
export function keyFormatConfirmed(result: KeyMatchResult): boolean {
  const { detected, expected, match } = result;
  if (!match || !detected || expected === null) return false;
  if (detected.kind === expected) return true;
  return Boolean(COMPATIBLE_KINDS[expected]?.has(detected.kind));
}
