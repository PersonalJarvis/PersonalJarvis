/**
 * Per-model Ollama options — the TypeScript half of the AP-4 contract.
 *
 * Mirrors `OLLAMA_MODEL_OPTION_KEYS` / `OllamaModelOptions` in
 * `jarvis/core/config.py`; `tests/unit/core/test_ollama_model_options_parity.py`
 * reads the key list below (one key per line, quoted) and the interface body,
 * so keep both shapes as they are when adding a knob.
 *
 * `null`/absent means "leave Ollama's default alone". Ranges (clamped by the
 * backend, never rejected): num_ctx 512..1048576, num_gpu -1..999, num_thread
 * 0..512, num_predict -2..1048576, temperature 0..2, top_p/min_p 0..1, top_k
 * 0..1000, repeat_penalty 0..3, seed 0..2^31-1, keep_alive Go duration ("30m")
 * | seconds | -1 forever | 0 unload, think boolean | "low" | "medium" | "high"
 * | "max".
 */
export const OLLAMA_MODEL_OPTION_KEYS = [
  "num_ctx",
  "num_gpu",
  "num_thread",
  "num_predict",
  "temperature",
  "top_p",
  "top_k",
  "min_p",
  "repeat_penalty",
  "seed",
  "stop",
  "keep_alive",
  "think",
] as const;

export type OllamaModelOptionKey = (typeof OLLAMA_MODEL_OPTION_KEYS)[number];

export type OllamaThinkLevel = "low" | "medium" | "high" | "max";

export interface OllamaModelOptions {
  num_ctx?: number | null;
  num_gpu?: number | null;
  num_thread?: number | null;
  num_predict?: number | null;
  temperature?: number | null;
  top_p?: number | null;
  top_k?: number | null;
  min_p?: number | null;
  repeat_penalty?: number | null;
  seed?: number | null;
  stop?: string[] | null;
  keep_alive?: string | number | null;
  think?: boolean | OllamaThinkLevel | null;
}

/** Keys that force a derived profile alias on the server (see ollama_profiles.py). */
export const OLLAMA_BAKEABLE_KEYS: readonly OllamaModelOptionKey[] = [
  "num_ctx",
  "num_gpu",
  "num_thread",
  "top_p",
  "top_k",
  "min_p",
  "repeat_penalty",
  "seed",
  "stop",
];

/** Drop every unset knob so a PUT body carries only what the user pinned. */
export function compactOllamaModelOptions(options: OllamaModelOptions): OllamaModelOptions {
  const out: OllamaModelOptions = {};
  for (const key of OLLAMA_MODEL_OPTION_KEYS) {
    const value = options[key];
    if (value === undefined || value === null) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    (out as Record<string, unknown>)[key] = value;
  }
  return out;
}

/** Whether the set needs a profile alias (a first use then takes a few seconds). */
export function needsProfileAlias(options: OllamaModelOptions): boolean {
  return OLLAMA_BAKEABLE_KEYS.some((key) => {
    const value = options[key];
    return value !== undefined && value !== null && !(Array.isArray(value) && value.length === 0);
  });
}
