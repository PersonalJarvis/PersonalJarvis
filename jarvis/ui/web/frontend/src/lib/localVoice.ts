/**
 * Local voice card: the backend contract (`jarvis/ui/web/local_voice_routes.py`)
 * and the pure helpers the card renders with.
 *
 * The const tuples below mirror `jarvis/realtime/local_voice_setup.py`
 * (`PHASES`, `MACHINE_CLASSES`, `LATENCY_BASES`, `SETUP_STAGES`) and
 * `jarvis/core/config.py::VOICE_ENGINE_VOICES`;
 * `tests/unit/voice_engine/test_local_voice_parity.py` keeps them in lockstep
 * (AP-4).
 */

export const LOCAL_VOICE_PHASES = [
  "not_installed",
  "installing",
  "stopped",
  "starting",
  "ready",
  "failed",
] as const;
export type LocalVoicePhase = (typeof LOCAL_VOICE_PHASES)[number];

export const LOCAL_VOICE_MACHINE_CLASSES = ["nvidia", "apple", "gpu", "cpu"] as const;
export type LocalVoiceMachineClass = (typeof LOCAL_VOICE_MACHINE_CLASSES)[number];

export const LOCAL_VOICE_LATENCY_BASES = ["measured", "estimate", "selftest"] as const;
export type LocalVoiceLatencyBasis = (typeof LOCAL_VOICE_LATENCY_BASES)[number];

export const LOCAL_VOICE_SETUP_STAGES = [
  "uv",
  "python",
  "packages",
  "engine",
  "models",
  "voice",
  "llm",
  "selftest",
] as const;
export type LocalVoiceSetupStage = (typeof LOCAL_VOICE_SETUP_STAGES)[number];

export const LOCAL_VOICE_VOICES = ["pocket", "piper"] as const;
export type LocalVoiceVoice = (typeof LOCAL_VOICE_VOICES)[number];

export interface LocalVoiceSetupRun {
  running: boolean;
  stage: string;
  progress: number;
  detail: string;
  /** One English sentence from the backend, rendered verbatim. */
  error: string;
  warnings: string[];
  finished_at: number | null;
}

export interface LocalVoiceSelftestLanguage {
  voice?: string;
  heard?: string;
  cer?: number;
  synth_ms?: number;
  stt_ms?: number;
  ok?: boolean;
}

export interface LocalVoiceSelftest {
  ok: boolean;
  at: number | null;
  /** Engine, packages, model or voice changed since this result. */
  stale: boolean;
  reason: string;
  languages: Record<string, LocalVoiceSelftestLanguage>;
  llm: { ok?: boolean; ms?: number; error?: string | null } | null;
}

export interface LocalVoiceLatency {
  low_s: number | null;
  high_s: number | null;
  basis: LocalVoiceLatencyBasis;
}

export interface LocalVoiceStatus {
  phase: LocalVoicePhase;
  stage: string;
  progress: number;
  /** The backend's own sentence when not ready; rendered verbatim. */
  reason: string;
  installed: boolean;
  setup: LocalVoiceSetupRun;
  llm_model: string;
  llm_source: "config" | "setup" | "default";
  /** null when Ollama did not answer (see `llm_error`). */
  llm_installed: boolean | null;
  llm_error: string;
  llm_choices: string[];
  voice: string;
  voices: string[];
  machine_class: LocalVoiceMachineClass;
  expected_latency: LocalVoiceLatency;
  platform: "windows" | "macos" | "linux";
  os_verified: boolean;
  selftest: LocalVoiceSelftest | null;
  selftest_running: boolean;
  started?: boolean;
  message?: string;
}

const BASE = "/api/providers/local-voice";

async function readJson<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  return body as T;
}

export async function fetchLocalVoiceStatus(): Promise<LocalVoiceStatus> {
  return readJson<LocalVoiceStatus>(await fetch(`${BASE}/status`));
}

export async function startLocalVoiceSetup(): Promise<LocalVoiceStatus> {
  return readJson<LocalVoiceStatus>(await fetch(`${BASE}/setup`, { method: "POST" }));
}

export async function runLocalVoiceSelftest(): Promise<LocalVoiceStatus> {
  return readJson<LocalVoiceStatus>(await fetch(`${BASE}/selftest`, { method: "POST" }));
}

export async function saveLocalVoiceSettings(change: {
  voice?: string;
  llm_model?: string;
}): Promise<LocalVoiceStatus> {
  return readJson<LocalVoiceStatus>(
    await fetch(`${BASE}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    }),
  );
}

/** Whether the card should keep polling: something is moving on its own. */
export function localVoiceIsBusy(status: LocalVoiceStatus | null): boolean {
  if (!status) return false;
  return (
    status.setup.running ||
    status.selftest_running ||
    status.phase === "installing" ||
    status.phase === "starting"
  );
}

/** Seconds with the UI language's decimal mark ("0.75" / "0,75"). */
export function formatSeconds(value: number, locale: string): string {
  return value.toLocaleString(locale, { maximumFractionDigits: 2 });
}

/**
 * The latency figure without its basis: "0.75–0.9" or a single value, or null
 * when only the self-test can tell.
 */
export function latencyFigure(latency: LocalVoiceLatency, locale: string): string | null {
  const { low_s: low, high_s: high } = latency;
  if (low === null || high === null) return null;
  if (low === high) return formatSeconds(low, locale);
  return `${formatSeconds(low, locale)}–${formatSeconds(high, locale)}`;
}

/**
 * The i18n stage suffix for a setup stage or an engine load stage.
 *
 * Setup reports its own stages; a starting engine reports the worker's
 * (`process`, `vad`, `turn`, `stt`, `tts:<lang>`, `llm`), folded here into
 * what a person recognises. Unknown stages show no label rather than a raw id.
 */
export function stageKey(stage: string): string {
  if ((LOCAL_VOICE_SETUP_STAGES as readonly string[]).includes(stage)) return stage;
  if (stage === "process") return "process";
  if (stage.startsWith("tts")) return "voice";
  if (stage === "start" || stage === "vad" || stage === "turn" || stage === "stt") return "speech";
  return "";
}

/** 0..100 for a progress bar, clamped. */
export function percentOf(progress: number): number {
  if (!Number.isFinite(progress)) return 0;
  return Math.max(0, Math.min(100, Math.round(progress * 100)));
}
