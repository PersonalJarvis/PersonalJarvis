import { useCallback, useEffect, useState } from "react";

export type AuthMode = "api_key" | "codex" | "none";
export type ProviderTier = "brain" | "tts" | "stt";

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
  cli_installed: boolean | null;
  /**
   * Codex only: whether an OpenAI API key usable by the Codex *brain* is
   * configured (codex_openai_api_key / openai_api_key / OPENAI_API_KEY). The
   * ChatGPT login alone cannot back a chat brain, so the brain "activate" radio
   * is gated on this rather than on the OAuth connection.
   */
  codex_brain_ready?: boolean;
  codex_status?: CodexStatus;
}

export interface CodexStatus {
  installed: boolean;
  connected: boolean;
  mode: "missing" | "not_connected" | "chatgpt" | "api_key" | "unknown";
  message: string;
  version?: string | null;
  accountLabel?: string | null;
  binaryPath?: string | null;
}

interface ProvidersResponse {
  providers: ProviderDescriptor[];
}

/**
 * Lädt /api/providers und re-fetched bei relevanten WS-Events. Die Hook stellt
 * den UI-State live ein, wenn Backend-seitig ein Secret gesetzt oder ein
 * Brain-Provider gewechselt wurde — ohne dass die Komponente das selbst
 * tracken muss.
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

  useEffect(() => {
    void refetch();
    const onSecret = () => void refetch();
    const onBrain = () => void refetch();
    const onTts = () => void refetch();
    const onStt = () => void refetch();
    window.addEventListener("jarvis:secret-configured", onSecret);
    window.addEventListener("jarvis:brain-switched", onBrain);
    window.addEventListener("jarvis:tts-switched", onTts);
    window.addEventListener("jarvis:stt-switched", onStt);
    return () => {
      window.removeEventListener("jarvis:secret-configured", onSecret);
      window.removeEventListener("jarvis:brain-switched", onBrain);
      window.removeEventListener("jarvis:tts-switched", onTts);
      window.removeEventListener("jarvis:stt-switched", onStt);
    };
  }, [refetch]);

  return { providers, loading, error, refetch };
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

// Backwards-compat alias — alter Name war TTS-spezifisch.
export type TtsSwitchResult = PipelineSwitchResult;

/**
 * Wechselt den aktiven TTS-Provider. Persistiert in jarvis.toml.
 *
 * Anders als beim Brain gibt es keinen Live-Manager — die SpeechPipeline
 * haelt ihre TTS-Instanz fest. Der Switch greift erst beim naechsten
 * Pipeline-Start (Voice-Toggle oder App-Restart). Die Backend-Response
 * setzt `restart_required = true` damit die UI das transparent macht.
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
 * Wechselt den aktiven STT-Provider. Persistiert in jarvis.toml.
 *
 * Genau wie TTS: der Whisper/Cloud-STT wird beim Pipeline-Bootstrap
 * einmalig instanziiert (Model-Load ist teuer), daher greift der
 * Switch erst beim naechsten Voice-Restart.
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
 * Wechselt den aktiven Heavy-Task SUBAGENT-Provider
 * (`[brain.sub_jarvis].provider`). Persistiert 3-schichtig
 * (jarvis.toml + config-soll.json + ENV), damit der Drift-Guard den Switch
 * nicht zurueckrollt. Der Worker liest den Provider beim Mission-Bootstrap
 * einmalig, daher setzt das Backend `restart_required = true`.
 */
export async function switchSubagentProvider(
  providerId: string,
): Promise<PipelineSwitchResult> {
  const res = await fetch("/api/subagent/switch", {
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
  const res = await fetch("/api/subagent/model", {
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
}

export interface BrainModelsResult {
  provider: string;
  current_model: string;
  models: BrainModel[];
  source: "live" | "cache" | "static";
  fetched_at: number;
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
  probe: BrainModelProbe;
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

