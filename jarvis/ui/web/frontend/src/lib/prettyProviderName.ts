/**
 * Cosmetic fallback when a backend pretty-label has not arrived yet.
 *
 * The registry (`provider_spec.py`) is the source of the names the API-Keys
 * cards use. This map is only for surfaces that already have a provider *id*
 * (sidebar footer, mission-deck header/orb) and must not flash the raw id
 * while `/api/settings/voice-mode` is still in flight. Unknown ids pass
 * through unchanged so a newly added provider never renders as a blank.
 */
import { translate } from "@/i18n";

const PROVIDER_NAMES: Record<string, string> = {
  "claude-api": "Claude (API)",
  openrouter: "OpenRouter",
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
  unknown: "—",
};

/** Fallback names that contain ordinary words, translated when read. */
const PROVIDER_NAME_KEYS: Record<string, string> = {
  ollama: "voice.polish.ollama_local",
  "ollama-local": "voice.polish.ollama_local",
  "local-realtime": "provider_switcher.name_local_realtime",
  "local-voice": "provider_switcher.name_local_voice",
};

export function prettyProviderName(id: string): string {
  const key = id.trim();
  if (!key) return "—";
  const i18nKey = PROVIDER_NAME_KEYS[key];
  if (i18nKey) return translate(i18nKey);
  return PROVIDER_NAMES[key] ?? key;
}
