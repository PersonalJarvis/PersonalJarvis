import { useCallback, useEffect, useState } from "react";

export type AuthMode = "api_key" | "codex" | "antigravity" | "none";
export type ProviderTier = "brain" | "tts" | "stt" | "realtime";
/** How using a provider is billed — mirror of provider_spec.Billing. */
export type Billing = "api" | "subscription" | "subscription_or_api" | "local";

/**
 * An alternative credential path for the same provider — mirror of
 * provider_spec.AltCredential. Gemini's AI-Studio-vs-Vertex split is the only
 * one today; `null` for single-path providers.
 */
export interface AltCredential {
  label: string;
  billing: Billing;
  credential_help: string;
  dashboard_url: string | null;
  credential_path_hint: string | null;
}

export interface ProviderDescriptor {
  id: string;
  label: string;
  tier: ProviderTier;
  auth_mode: AuthMode;
  secret_keys: string[];
  secrets_set: Record<string, boolean>;
  dashboard_url: string | null;
  login_cli: string[] | null;
  install_hint: string | null;
  credential_path_hint: string | null;
  configured: boolean;
  active: boolean;
  brain_switchable?: boolean;
  cli_installed: boolean | null;
  /** Plain-English "which key / subscription, and what for". */
  credential_help: string | null;
  /** Where to sign up for the account/subscription (distinct from dashboard_url). */
  signup_url: string | null;
  /** How using this provider is billed. */
  billing: Billing;
  /** Maintainer-recommended pick for this tier — renders a "Recommended" badge
   *  on the provider card (brain tier only today). Presentation hint only. */
  recommended?: boolean;
  /** The model the recommendation points at (e.g. "gemini-3.5-flash"), shown as
   *  an "empfohlen" marker in the model picker. null = provider-level only. */
  recommended_model?: string | null;
  /** Gemini's Vertex alternative; null for single-path providers. */
  alt_credential: AltCredential | null;
  /**
   * Codex only: legacy credential readiness kept in /api/providers for older
   * UI consumers. The current UI does not render Codex as a switchable Brain;
   * Codex is connected and selected from the Subagent section.
   */
  codex_brain_ready?: boolean;
  codex_status?: CodexStatus;
  /**
   * Antigravity only: the honest Google CLI login snapshot (mirror of
   * `GoogleCliAuthStatus.to_dict()`). Drives the OAuth connect/disconnect widget
   * in the Subagent section. It is not switchable as the main Brain provider.
   */
  antigravity_status?: AntigravityStatus;
}

export interface CodexStatus {
  installed: boolean;
  connected: boolean;
  mode: "missing" | "not_connected" | "chatgpt" | "api_key" | "unknown";
  message: string;
  version?: string | null;
  accountLabel?: string | null;
  account_label?: string | null;
  user_email?: string | null;
  binaryPath?: string | null;
  binary_path?: string | null;
  error?: string | null;
}

/**
 * Mirror of `jarvis/google_cli/auth_service.py::GoogleCliAuthStatus.to_dict()`.
 * The Google-subscription sibling of `CodexStatus`: whether the official
 * `agy`/`gemini` CLI is installed and signed in with Google, plus the account
 * email so the connected card can show whose subscription is billed.
 */
export interface AntigravityStatus {
  installed: boolean;
  connected: boolean;
  mode: string; // "oauth-personal" | "api_key" | "unknown"
  cli_kind: string | null; // "agy" | "gemini"
  message: string;
  version: string | null;
  user_email: string | null;
  binary_path: string;
  error: string | null;
}

/**
 * Mirror of `jarvis/claude_auth.py::ClaudeAuthStatus.to_dict()`. The Anthropic
 * sibling of `CodexStatus` / `AntigravityStatus`: whether the `claude` CLI is
 * installed and whether the subagent runs over the Claude Max subscription
 * (the OAuth login) or an Anthropic API key, plus the connected account email +
 * subscription tier so the card can show "Connected as <email>".
 */
export interface ClaudeStatus {
  installed: boolean;
  connected: boolean;
  mode: string; // "subscription" | "api_key" | "unknown"
  message: string;
  version?: string | null;
  account_label?: string | null;
  user_email?: string | null;
  subscription_type?: string | null; // raw tier, e.g. "max"
  binary_path?: string | null;
  error?: string | null;
  /** True when a classic Anthropic API key (sk-ant-api…) is stored — drives the
   * API-key field's "configured" state on the subagent card. Never the key. */
  api_key_present?: boolean;
}

interface ProvidersResponse {
  providers: ProviderDescriptor[];
}

/**
 * Loads /api/providers and re-fetches on relevant WS events. The hook updates
 * the UI state live whenever a secret is set on the backend or a brain
 * provider was switched — without the component having to track that itself.
 */
export function useProviders() {
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/providers");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: ProvidersResponse = await res.json();
      setProviders(data.providers);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Optimistically flip the active provider within a tier, in-memory, BEFORE
   * the backend switch resolves. The `/api/{brain,tts,stt}/switch` calls can
   * take a few seconds — a TTS switch rebuilds the provider client and injects
   * it into the live pipeline — and the UI used to update only after that call
   * AND a full `/api/providers` refetch, so the "active" highlight lagged for
   * seconds. Callers flip the highlight here on click, then run the switch and
   * `refetch()` to confirm; on failure a `refetch()` restores server truth.
   */
  const setActiveOptimistic = useCallback((tier: ProviderTier, id: string) => {
    setProviders((prev) =>
      prev.map((p) => (p.tier === tier ? { ...p, active: p.id === id } : p)),
    );
  }, []);

  useEffect(() => {
    void refetch();
    const onSecret = () => void refetch();
    const onBrain = () => void refetch();
    const onTts = () => void refetch();
    const onStt = () => void refetch();
    const onRealtime = () => void refetch();
    window.addEventListener("jarvis:secret-configured", onSecret);
    window.addEventListener("jarvis:brain-switched", onBrain);
    window.addEventListener("jarvis:tts-switched", onTts);
    window.addEventListener("jarvis:stt-switched", onStt);
    window.addEventListener("jarvis:realtime-switched", onRealtime);
    return () => {
      window.removeEventListener("jarvis:secret-configured", onSecret);
      window.removeEventListener("jarvis:brain-switched", onBrain);
      window.removeEventListener("jarvis:tts-switched", onTts);
      window.removeEventListener("jarvis:stt-switched", onStt);
      window.removeEventListener("jarvis:realtime-switched", onRealtime);
    };
  }, [refetch]);

  return { providers, loading, error, refetch, setActiveOptimistic };
}

// ── Section health (the at-a-glance API-Keys tab indicators) ────────────────
// Mirrors SECTION_HEALTH_STATUSES in jarvis/brain/section_health.py and the
// SectionHealthStatusLiteral in provider_routes.py (five-layer anti-drift; a
// backend parity test guards the Python↔Pydantic side, this union is the UI
// mirror). Only "needs_setup" (amber) and "error" (red) draw a dot; "ok" and
// "unknown" stay silent.
export type SectionHealthStatus = "ok" | "needs_setup" | "error" | "unknown";

export interface SectionHealth {
  status: SectionHealthStatus;
  /** Machine cause (the underlying provider-test status / "not_configured" /
   * "no_active" / "local" / "ok" / "unknown") — for tooltips + debugging. */
  reason: string;
  /** Plain-English one-liner for the hover tooltip. */
  detail: string;
}

export interface SectionHealthResponse {
  sections: Record<string, SectionHealth>;
  checked_at: number;
  cached: boolean;
}

/**
 * Fetches the per-tab health rollup. `refresh=true` bypasses the server-side
 * TTL cache — used right after a key save / provider switch so the dot reflects
 * the change immediately instead of a stale cached result.
 */
export async function getSectionHealth(refresh = false): Promise<SectionHealthResponse> {
  const res = await fetch(
    `/api/providers/section-health${refresh ? "?refresh=true" : ""}`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as SectionHealthResponse;
}

/**
 * Drives the tab status dots in the API-Keys view. Fetches once on mount (the
 * server runs the REAL connectivity test of each tier's active provider, cached
 * briefly) and re-fetches with `refresh=true` whenever a key is saved, a provider
 * is switched, or a manual per-card test completes — so the dot tracks live.
 *
 * Health is best-effort: a failed fetch leaves the map empty (no dots), never
 * breaking the page.
 */
export function useSectionHealth() {
  const [health, setHealth] = useState<Record<string, SectionHealth>>({});

  const reload = useCallback(async (refresh = false) => {
    try {
      const data = await getSectionHealth(refresh);
      setHealth(data.sections ?? {});
    } catch {
      // best-effort — keep whatever we last had rather than clearing to nothing
    }
  }, []);

  useEffect(() => {
    void reload(false);
    const onChange = () => void reload(true);
    const events = [
      "jarvis:secret-configured",
      "jarvis:brain-switched",
      "jarvis:tts-switched",
      "jarvis:stt-switched",
      "jarvis:realtime-switched",
      "jarvis:subagent-switched",
      "jarvis:provider-tested",
    ];
    events.forEach((e) => window.addEventListener(e, onChange));
    return () => events.forEach((e) => window.removeEventListener(e, onChange));
  }, [reload]);

  return { health, reload };
}

export async function postSecret(key: string, value: string): Promise<void> {
  const res = await fetch(`/api/secrets/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

export async function deleteSecret(key: string): Promise<void> {
  const res = await fetch(`/api/secrets/${encodeURIComponent(key)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

export async function startCodexLogin(): Promise<void> {
  const res = await fetch("/api/codex/login", { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body.detail;
    throw new Error(
      typeof detail === "object" && detail?.message
        ? detail.message
        : detail ?? `HTTP ${res.status}`,
    );
  }
}

export async function codexLogout(): Promise<void> {
  const res = await fetch("/api/codex/logout", { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

/**
 * Starts the interactive "Sign in with Google" flow by driving the official
 * `agy`/`gemini` CLI as a subprocess (POST /api/antigravity/login). The Google
 * sibling of `startCodexLogin` — a 409 means no Google CLI is installed (the
 * detail carries an install_command).
 */
export async function loginAntigravity(): Promise<void> {
  const res = await fetch("/api/antigravity/login", { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body.detail;
    throw new Error(
      typeof detail === "object" && detail?.message
        ? detail.message
        : detail ?? `HTTP ${res.status}`,
    );
  }
}

/** Disconnects the Google login (POST /api/antigravity/logout). */
export async function logoutAntigravity(): Promise<void> {
  const res = await fetch("/api/antigravity/logout", { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

/**
 * Starts the interactive Claude sign-in by driving the `claude` CLI as a
 * subprocess (POST /api/claude/login). The Anthropic sibling of
 * `startCodexLogin` — a 409 means no Claude CLI is installed (the detail carries
 * an install_command).
 */
export async function loginClaude(): Promise<void> {
  const res = await fetch("/api/claude/login", { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body.detail;
    throw new Error(
      typeof detail === "object" && detail?.message
        ? detail.message
        : detail ?? `HTTP ${res.status}`,
    );
  }
}

/** Disconnects the Claude subscription login (POST /api/claude/logout). */
export async function logoutClaude(): Promise<void> {
  const res = await fetch("/api/claude/logout", { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

export async function setCodexBinaryPath(binaryPath: string): Promise<void> {
  const res = await fetch("/api/codex/binary-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ binary_path: binaryPath }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

export async function switchBrainProvider(providerId: string): Promise<void> {
  const res = await fetch("/api/brain/switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: providerId, persist: true }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

export interface PipelineSwitchResult {
  ok: boolean;
  active: string;
  persisted: boolean;
  restart_required: boolean;
}

// Backwards-compat alias — the old name was TTS-specific.
export type TtsSwitchResult = PipelineSwitchResult;

/**
 * Switches the active TTS provider. Persists to jarvis.toml.
 *
 * Unlike the brain, there's no live manager — the SpeechPipeline holds onto
 * its TTS instance. The switch only takes effect on the next pipeline start
 * (voice toggle or app restart). The backend response sets
 * `restart_required = true` so the UI makes that transparent.
 */
export async function switchTtsProvider(
  providerId: string,
): Promise<PipelineSwitchResult> {
  const res = await fetch("/api/tts/switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: providerId, persist: true }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as PipelineSwitchResult;
}

/**
 * Switches the active STT provider. Persists to jarvis.toml.
 *
 * Just like TTS: the Whisper/cloud STT is instantiated once at pipeline
 * bootstrap (model load is expensive), so the switch only takes effect
 * on the next voice restart.
 */
export async function switchSttProvider(
  providerId: string,
): Promise<PipelineSwitchResult> {
  const res = await fetch("/api/stt/switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: providerId, persist: true }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as PipelineSwitchResult;
}

/**
 * Switches the active full-duplex Realtime provider (speech-to-speech).
 * Persists to jarvis.toml. Mirrors `switchSttProvider` — the pipeline is
 * only (re)built on the next voice start, so the backend response sets
 * `restart_required = true`.
 */
export async function switchRealtimeProvider(
  providerId: string,
): Promise<PipelineSwitchResult> {
  const res = await fetch("/api/realtime/switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: providerId, persist: true }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as PipelineSwitchResult;
}

/**
 * Switches the active heavy-task WORKER provider
 * (`[brain.sub_jarvis].provider`). Persists across 3 layers
 * (jarvis.toml + config-soll.json + ENV) so the drift guard doesn't roll (i18n-allow: "soll" is part of the config-soll.json filename)
 * back the switch. The worker reads the provider once at mission bootstrap,
 * so the backend sets `restart_required = true`.
 */
export async function switchSubagentProvider(
  providerId: string,
): Promise<PipelineSwitchResult> {
  const res = await fetch("/api/jarvis-agent/switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: providerId, persist: true }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as PipelineSwitchResult;
}

/**
 * Pins the dedicated subagent LLM model (`[brain.sub_jarvis].model`).
 * Empty string resets to the active subagent provider's deep model.
 * 3-layer persisted server-side (drift-guard pinned key).
 */
export async function saveSubagentModel(
  model: string,
): Promise<PipelineSwitchResult> {
  const res = await fetch("/api/jarvis-agent/model", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, persist: true }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as PipelineSwitchResult;
}

// ── Provider connectivity test ──────────────────────────────────────────────
// Mirrors PROVIDER_TEST_STATUSES in jarvis/brain/provider_test.py and the
// ProviderTestStatusLiteral in provider_routes.py (anti-drift; a backend parity
// test guards the Python↔Pydantic side, this union is the UI mirror).
export type ProviderTestStatus =
  | "ok"
  | "not_configured"
  | "bad_key"
  | "no_credits"
  | "rate_limited"
  | "model_unavailable"
  | "unreachable"
  | "error";

export interface ProviderTestResult {
  provider: string;
  status: ProviderTestStatus;
  detail: string;
  latency_ms: number;
  /**
   * True when the provider was reached and answered at the protocol level —
   * the integration code is sound and only the credential/account/model is the
   * blocker. False only for "unreachable" / "error".
   */
  integration_ok: boolean;
}

/**
 * Runs a REAL minimal call against the provider (1-token brain completion, a
 * tiny TTS synthesis, an STT transcription, or the Codex OAuth status) and
 * reports the honest outcome — not just whether a key string is stored.
 */
export async function testProvider(providerId: string): Promise<ProviderTestResult> {
  const res = await fetch(`/api/providers/${encodeURIComponent(providerId)}/test`, {
    method: "POST",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as ProviderTestResult;
}

// ── Per-provider model picker ───────────────────────────────────────────────
// The brain provider's model list comes from its OWN /v1/models catalog (or
// OpenRouter's public catalog), so a freshly released model shows up without any
// code change. `source` is honest: "live" (just fetched) / "cache" (served from
// a still-fresh prior fetch) / "static" (offline fallback — show a hint).

export interface BrainModel {
  id: string;
  label: string;
  // Presentation-only classification from the backend (classify_model) that
  // drives the picker's filter chips + star. All optional/defaulting to false so
  // older payloads and the custom-id row stay valid. Never gate behavior on them.
  free?: boolean;
  frontier?: boolean;
  value?: boolean;
  starred?: boolean;
  // Tri-state vision-input capability from the provider's model metadata:
  // true = understands images, false = text-only, null/undefined = unknown
  // (the provider doesn't expose modality data — treated as capable). The
  // Computer-Use picker hides ONLY explicit false entries.
  vision?: boolean | null;
}

export interface BrainModelsResult {
  provider: string;
  current_model: string;
  models: BrainModel[];
  source: "live" | "cache" | "static" | "curated";
  fetched_at: number;
  // What the picker writes: "model" (brain/stt/cartesia) or "voice" (most TTS).
  selects?: "model" | "voice";
}

/** Lists the available models for a brain provider for the picker dropdown. */
export async function getBrainProviderModels(
  providerId: string,
  refresh = false,
): Promise<BrainModelsResult> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/models${refresh ? "?refresh=true" : ""}`,
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as BrainModelsResult;
}

export interface BrainModelProbe {
  status: ProviderTestStatus;
  detail: string;
  latency_ms: number;
  integration_ok: boolean;
}

export interface BrainModelSaveResult {
  ok: boolean;
  provider: string;
  model: string;
  persisted: boolean;
  applied_live: boolean;
  restart_required: boolean;
  // Only brain providers run a live probe; TTS/STT save without one (null).
  probe: BrainModelProbe | null;
}

/**
 * Pins a brain provider's model and verifies it with a REAL 1-token probe.
 * Empty `model` resets the provider to its frontier default. The selection is
 * saved regardless of the probe outcome; `probe.status` reports the truth
 * (ok / bad_key / no_credits / model_unavailable / …).
 */
export async function saveBrainProviderModel(
  providerId: string,
  model: string,
  persist = true,
): Promise<BrainModelSaveResult> {
  const res = await fetch(`/api/providers/${encodeURIComponent(providerId)}/model`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, persist }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as BrainModelSaveResult;
}

// ── Per-model TTS voice picker + audio preview (OpenRouter TTS) ──────────────
// A TTS model ships its own voices, each speaking a language (or multilingual).
// These feed the voice picker under the model selector on the OpenRouter-TTS
// card: list the chosen model's voices tagged by language, persist a pick, and
// synthesise a short spoken sample so the user can HEAR a voice.

export interface TtsVoiceEntry {
  id: string;
  /** ISO-639-1 code ("en"/"de"/"es"/"fr"/…) or "multi" (multilingual). */
  language: string;
}

export interface TtsVoicesResult {
  provider: string;
  model: string;
  voices: TtsVoiceEntry[];
  /** The model's safe default voice (pre-selects the picker). */
  default: string;
  /** The persisted voice IF valid for this model, else "" (stale → placeholder). */
  current: string;
}

/** Lists a TTS model's voices, each tagged with its spoken language. */
export async function getTtsVoices(
  model: string,
  provider = "openrouter-tts",
): Promise<TtsVoicesResult> {
  const res = await fetch(
    `/api/tts/voices?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`,
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as TtsVoicesResult;
}

/** Persists the chosen global TTS voice ([tts] voice_de/voice_en). */
export async function saveTtsVoice(
  voice: string,
  persist = true,
): Promise<BrainModelSaveResult> {
  const res = await fetch("/api/tts/voice", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ voice, persist }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as BrainModelSaveResult;
}

/**
 * Synthesises a SHORT spoken sample with a model + voice in the given language
 * and returns it as a WAV Blob (playable by an <audio> element). Throws a clean
 * Error with the backend's message on any failure (no key / rate limit / …).
 */
export async function fetchTtsPreview(opts: {
  model: string;
  voice: string;
  language: "de" | "en" | "es";
  provider?: string;
}): Promise<Blob> {
  const res = await fetch("/api/tts/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: opts.provider ?? "openrouter-tts",
      model: opts.model,
      voice: opts.voice,
      language: opts.language,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return await res.blob();
}

// Phase 3: per-provider Computer-Use model. CU runs on the provider's main
// `model` by default; a pinned `cu_model` lets the user run CU on a different
// (e.g. stronger) model than chat. `cu_model === ""` means "use my main model".
export interface CuModelResult {
  ok?: boolean;
  provider: string;
  cu_model: string; // the pinned value ("" = use the main model)
  effective_model: string; // what Computer-Use would actually run
  uses_main: boolean; // true when nothing is pinned
  persisted?: boolean;
  restart_required?: boolean;
}

/** Reads the per-provider Computer-Use model selection. */
export async function getCuModel(providerId: string): Promise<CuModelResult> {
  const res = await fetch(`/api/providers/${encodeURIComponent(providerId)}/cu-model`);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as CuModelResult;
}

/**
 * Pins (or clears with "") the per-provider Computer-Use model. Returns a
 * BrainModelSaveResult shape so it can drive the shared BrainModelSelector's
 * `onSave`. No live probe — CU validates the model lazily on its next dispatch.
 */
export async function saveCuModel(
  providerId: string,
  cuModel: string,
  persist = true,
): Promise<BrainModelSaveResult> {
  const res = await fetch(`/api/providers/${encodeURIComponent(providerId)}/cu-model`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cu_model: cuModel, persist }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  const r = body as CuModelResult;
  return {
    ok: r.ok ?? true,
    provider: r.provider,
    model: r.cu_model,
    persisted: r.persisted ?? false,
    applied_live: !(r.restart_required ?? false),
    restart_required: r.restart_required ?? false,
    probe: null,
  };
}

