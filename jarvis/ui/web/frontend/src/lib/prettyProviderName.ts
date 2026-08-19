/**
 * Cosmetic fallback when a backend pretty-label has not arrived yet.
 *
 * The registry (`provider_spec.py`) is the source of the names the API-Keys
 * cards use. This map is only for surfaces that already have a provider *id*
 * (sidebar footer, mission-deck header/orb) and must not flash the raw id
 * while `/api/settings/voice-mode` is still in flight. Unknown ids pass
 * through unchanged so a newly added provider never renders as a blank.
 */
const PROVIDER_NAMES: Record<string, string> = {
  "claude-api": "Claude (API)",
  openrouter: "OpenRouter",
  ollama: "Ollama (local)",
  "ollama-local": "Ollama (local)",
  "ollama-cloud": "Ollama (Cloud)",
  gemini: "Gemini",
  vertex: "Vertex AI",
  openai: "OpenAI",
  grok: "Grok",
  nvidia: "NVIDIA NIM",
  codex: "Codex",
  mock: "Mock-Brain",
  // Realtime tier — used when the backend's pretty label is unavailable.
  "openai-realtime": "OpenAI Realtime",
  "gemini-live": "Gemini Live",
  "vertex-live": "Vertex AI Live",
  "local-realtime": "Self-hosted realtime",
  unknown: "—",
};

export function prettyProviderName(id: string): string {
  const key = id.trim();
  if (!key) return "—";
  return PROVIDER_NAMES[key] ?? key;
}
