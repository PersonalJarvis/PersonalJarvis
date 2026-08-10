import { useCallback, useEffect, useRef, useState } from "react";

export type AuthMode = "api_key" | "codex" | "antigravity" | "none";
// Mirror of provider_spec.Tier. "dictation" is the odd one out and deliberately
// so: it is the OPTIONAL tier that tidies dictated text, and a missing key there
// costs nothing — the dictation is delivered exactly as it was recognized. Every
// other tier is load-bearing for the feature it powers.
export type ProviderTier =
  | "brain"
  | "tts"
  | "stt"
  | "realtime"
  | "computer-use"
  | "dictation";
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

/**
 * On-disk truth about a provider that runs locally — mirror of
 * `jarvis.speech.local_models.LocalModelStatus`. The engine (the pip package
 * doing inference) and the model (its weights) are tracked separately because
 * they fail separately and need different fixes: install versus download.
 */
export interface LocalRuntimeStatus {
  runtime: string;
  engine_installed: boolean;
  model_present: boolean;
  model_label: string;
  ready: boolean;
  /** One honest sentence for the card — render it verbatim. */
  detail: string;
}

export interface ProviderDescriptor {
  id: string;
  label: string;
  tier: ProviderTier;
  auth_mode: AuthMode;
  secret_keys: string[];
  secrets_set: Record<string, boolean>;
  /**
   * Fallback-aware per-slot state: true when the dedicated slot is set OR
   * the runtime family chain resolves a shared key for it. Optional so
   * cached older payloads keep parsing.
   */
  secrets_effective?: Record<string, boolean>;
  /** Other provider surfaces (labels) that read the same slot at runtime. */
  secret_shared_with?: Record<string, string[]>;
  dashboard_url: string | null;
  login_cli: string[] | null;
  install_hint: string | null;
  credential_path_hint: string | null;
  configured: boolean;
  active: boolean;
  /**
   * Whether this brain provider is the dedicated Computer-Use planner
   * (`[brain.computer_use].provider`) — an OVERLAY selection, independent of
   * `active`/`brain.primary` above. Only ever true for `tier === "brain"`.
   */
  computer_use_active?: boolean;
  brain_switchable?: boolean;
  /**
   * Whether the provider's CLI is on this machine, as the BACKEND judges it.
   *
   * Deliberately not rendered by any card, and that is the decision rather
   * than an oversight: for a Codex-auth card the backend already answers this
   * inside `codex_status.installed`, and its one carve-out (during an
   * ownership window, answer from PATH instead of the owned profile) protects
   * exactly the states the card's install row suppresses anyway. Reading both
   * would give the same question two answers that can disagree. Kept in the
   * payload for `/api/providers` consumers outside this UI (the control CLI,
   * the setup report).
   */
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
  /** Inverse of `recommended`: a short caution that renders a "Not recommended"
   *  badge with this text as its tooltip (e.g. NVIDIA NIM's slow free tier).
   *  Presentation hint only. null/absent = no caution. */
  caution?: string | null;
  /**
   * The card is nice to have, not required — renders an "Optional" chip and
   * keeps the tier off the "needs setup" dot. Presentation only: it never gates
   * a code path (AP-21), it just stops an optional tier from painting a
   * permanent amber warning onto an install that is working perfectly well
   * without it. Absent on older payloads, which read as "required".
   */
  optional?: boolean;
  /** Unstable provider protocol. The card shows a clear fallback notice. */
  experimental?: boolean;
  /**
   * Dictation-polish cards only: the value `[dictation].polish_provider`
   * actually stores ("groq"), which is NOT this card's `id` ("groq-polish") —
   * a bare "groq" would collide with the brain card, so the tier carries a
   * suffix the config vocabulary knows nothing about. Pinning this tier must
   * send THIS field: `resolve_polish_chain` ignores a family id it does not
   * recognise and falls back to the auto order, so a client that sent `id`
   * stored a value that looked saved and silently did nothing.
   * null/absent on every other card.
   */
  polish_family?: string | null;
  /** Gemini's Vertex alternative; null for single-path providers. */
  alt_credential: AltCredential | null;
  /**
   * On-device cards only: whether the inference engine and its weights are
   * REALLY on this machine. A local provider has no key to check, so without
   * this a local card would render as ready the moment it exists — the defect
   * that got the previous local Whisper card removed. `ready` is the only field
   * a caller should gate on; the two booleans behind it explain WHICH half is
   * missing so the card can offer the right next step. null on cloud cards.
   */
  local_runtime?: LocalRuntimeStatus | null;
  /**
   * Self-hosted realtime card only: fail-closed state of the one-click
   * managed server install. `sentence` is the server's own wording and is
   * rendered verbatim — the client never guesses readiness. null elsewhere.
   */
  managed_server?: ManagedServerStatus | null;
  /** Local/self-hosted cards: whether the card exposes an editable server URL
   *  (persisted via PUT /api/providers/{id}/base-url). */
  supports_base_url?: boolean;
  /**
   * Whether this card's server can be TOLD to download a model
   * (POST /api/providers/{id}/pull). A capability flag rather than a provider
   * name: a generic OpenAI-compatible server has no such API, an Ollama server
   * does, and the UI decides on the flag alone.
   */
  supports_model_pull?: boolean;
  /** Placeholder while no override is stored; null = the user must set one. */
  default_base_url?: string | null;
  /** The stored server-URL override; null = the vendor default is in effect. */
  base_url?: string | null;
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
  reason_code?:
    | "ready"
    | "login_required"
    // An interactive login is running: invite the user to FINISH it in the
    // browser, never to start a second one.
    | "login_in_progress"
    | "lifecycle_unavailable"
    | "not_installed"
    | "setup_invalid"
    // Sticky: the connected ChatGPT plan can never activate this provider.
    | "plan_unsupported"
    // Transient: the profile is briefly owned by another status probe, a
    // login/logout, or a starting voice session. Not a setup defect — the UI
    // shows a neutral "checking" line and never the reconnect warning.
    | "busy";
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

export const PROVIDER_BACKEND_UNREACHABLE = "backend_unreachable";
const PROVIDER_RETRY_DELAYS_MS = [1_000, 2_000, 5_000, 10_000] as const;

interface UseProvidersOptions {
  retryDelaysMs?: readonly number[];
}

/**
 * Loads /api/providers and re-fetches on relevant WS events. The hook updates
 * the UI state live whenever a secret is set on the backend or a brain
 * provider was switched — without the component having to track that itself.
 */
export function useProviders(options: UseProvidersOptions = {}) {
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const retryAttempt = useRef(0);
  const retryDelays = useRef<readonly number[]>(PROVIDER_RETRY_DELAYS_MS);
  retryDelays.current = options.retryDelaysMs ?? PROVIDER_RETRY_DELAYS_MS;

  const refetch = useCallback(async () => {
    const version = ++requestVersion.current;
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setError(null);
    try {
      const res = await fetch("/api/providers", {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: ProvidersResponse = await res.json();
      if (version === requestVersion.current) {
        retryAttempt.current = 0;
        // A 200 whose body is not the expected shape (a proxy's error page, a
        // partially-written response) must leave an EMPTY list, never
        // `undefined`: every consumer maps or filters this, so one malformed
        // payload would take the whole provider console down with a TypeError
        // instead of showing the honest "no providers" state.
        setProviders(Array.isArray(data?.providers) ? data.providers : []);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError" && version === requestVersion.current) {
        setError(
          e instanceof TypeError
            ? PROVIDER_BACKEND_UNREACHABLE
            : (e as Error).message,
        );
      }
    } finally {
      if (version === requestVersion.current) setLoading(false);
      if (requestController.current === controller) requestController.current = null;
    }
  }, []);

  useEffect(() => {
    if (error !== PROVIDER_BACKEND_UNREACHABLE || retryDelays.current.length === 0) {
      return;
    }
    const delays = retryDelays.current;
    const delay = delays[Math.min(retryAttempt.current, delays.length - 1)];
    retryAttempt.current += 1;
    const timer = window.setTimeout(() => void refetch(), delay);
    return () => window.clearTimeout(timer);
  }, [error, refetch]);

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
    window.dispatchEvent(
      new CustomEvent("jarvis:provider-selection-pending", {
        detail: { section: tier, provider: id },
      }),
    );
  }, []);

  useEffect(() => {
    void refetch();
    const onSecret = () => void refetch();
    const onBrain = () => void refetch();
    const onTts = () => void refetch();
    const onStt = () => void refetch();
    const onRealtime = () => void refetch();
    const onComputerUse = () => void refetch();
    const onDictationPolish = () => void refetch();
    window.addEventListener("jarvis:secret-configured", onSecret);
    window.addEventListener("jarvis:brain-switched", onBrain);
    window.addEventListener("jarvis:tts-switched", onTts);
    window.addEventListener("jarvis:stt-switched", onStt);
    window.addEventListener("jarvis:realtime-switched", onRealtime);
    window.addEventListener("jarvis:computer-use-switched", onComputerUse);
    window.addEventListener("jarvis:dictation-polish-switched", onDictationPolish);
    return () => {
      ++requestVersion.current;
      requestController.current?.abort();
      requestController.current = null;
      window.removeEventListener("jarvis:secret-configured", onSecret);
      window.removeEventListener("jarvis:brain-switched", onBrain);
      window.removeEventListener("jarvis:tts-switched", onTts);
      window.removeEventListener("jarvis:stt-switched", onStt);
      window.removeEventListener("jarvis:realtime-switched", onRealtime);
      window.removeEventListener("jarvis:computer-use-switched", onComputerUse);
      window.removeEventListener(
        "jarvis:dictation-polish-switched",
        onDictationPolish,
      );
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
  /** Exact provider/integration checked by the backend. */
  subject_id: string | null;
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
export async function getSectionHealth(
  refresh = false,
  signal?: AbortSignal,
): Promise<SectionHealthResponse> {
  const res = await fetch(
    `/api/providers/section-health${refresh ? "?refresh=true" : ""}`,
    { signal, cache: "no-store" },
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
  const requestVersion = useRef(0);
  const requestController = useRef<AbortController | null>(null);

  const reload = useCallback(async (refresh = false) => {
    const version = ++requestVersion.current;
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    try {
      const data = await getSectionHealth(refresh, controller.signal);
      if (version === requestVersion.current) {
        setHealth(data.sections ?? {});
      }
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      // best-effort — keep whatever we last had rather than clearing to nothing
    } finally {
      if (requestController.current === controller) {
        requestController.current = null;
      }
    }
  }, []);

  useEffect(() => {
    void reload(false);
    // Debounced: one action can fire several of these events back-to-back
    // (switch + refetch + test). Each refresh runs REAL connectivity tests
    // server-side, so bursts are collapsed into a single trailing reload.
    let timer: number | undefined;
    const clearSection = (section?: string) => {
      setHealth((previous) => {
        if (!section) return {};
        if (!(section in previous)) return previous;
        const next = { ...previous };
        delete next[section];
        return next;
      });
    };
    const onChange = (event: Event) => {
      ++requestVersion.current;
      requestController.current?.abort();

      const detail = (event as CustomEvent<ProviderHealthEventDetail>).detail;
      const section = detail?.section ?? SECTION_HEALTH_EVENT_SECTIONS[event.type];
      const testResult = detail?.result;
      if (
        event.type === "jarvis:provider-tested" &&
        detail?.active &&
        testResult &&
        section
      ) {
        setHealth((previous) => ({
          ...previous,
          [section]: sectionHealthFromProviderTest(
            testResult,
            detail.provider_label ?? testResult.provider,
          ),
        }));
      } else if (event.type !== "jarvis:provider-tested") {
        clearSection(section);
      }

      window.clearTimeout(timer);
      if (event.type === "jarvis:provider-selection-pending") return;
      timer = window.setTimeout(() => void reload(true), 400);
    };
    const events = [
      "jarvis:secret-configured",
      "jarvis:brain-switched",
      "jarvis:tts-switched",
      "jarvis:stt-switched",
      "jarvis:realtime-switched",
      "jarvis:computer-use-switched",
      "jarvis:dictation-polish-switched",
      "jarvis:subagent-switched",
      "jarvis:agent-switched",
      "jarvis:provider-tested",
      "jarvis:provider-config-changed",
      "jarvis:provider-selection-pending",
      "jarvis:provider-switch-failed",
    ];
    events.forEach((e) => window.addEventListener(e, onChange));
    return () => {
      ++requestVersion.current;
      requestController.current?.abort();
      window.clearTimeout(timer);
      events.forEach((e) => window.removeEventListener(e, onChange));
    };
  }, [reload]);

  return { health, reload };
}

interface ProviderHealthEventDetail {
  section?: string;
  provider?: string;
  provider_label?: string;
  active?: boolean;
  result?: ProviderTestResult;
}

const SECTION_HEALTH_EVENT_SECTIONS: Record<string, string> = {
  "jarvis:brain-switched": "brain",
  "jarvis:tts-switched": "tts",
  "jarvis:stt-switched": "stt",
  "jarvis:realtime-switched": "realtime",
  "jarvis:computer-use-switched": "computer-use",
  "jarvis:dictation-polish-switched": "dictation",
  "jarvis:subagent-switched": "subagents",
  "jarvis:agent-switched": "subagents",
};

export function sectionHealthForSubject(
  health: SectionHealth | undefined,
  subjectId: string | null | undefined,
): SectionHealth | undefined {
  if (!subjectId || health?.subject_id !== subjectId) return undefined;
  return health;
}

export function sectionHealthFromProviderTest(
  result: ProviderTestResult,
  providerLabel: string,
): SectionHealth {
  const status: SectionHealthStatus =
    result.status === "ok"
      ? "ok"
      : result.status === "not_configured"
        ? "needs_setup"
        : "error";
  return {
    status,
    reason: result.status,
    detail: `${providerLabel}: ${result.detail || result.status}`,
    subject_id: result.provider,
  };
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

export async function saveProviderBaseUrl(
  providerId: string,
  baseUrl: string | null,
): Promise<string | null> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/base-url`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_url: baseUrl }),
    },
  );
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `base-url ${res.status}`);
  }
  const data = (await res.json()) as { base_url: string | null };
  return data.base_url ?? null;
}

export async function startCodexLogin(subscriptionVoice = false): Promise<void> {
  const path = subscriptionVoice
    ? "/api/codex/subscription-voice/login"
    : "/api/codex/login";
  const res = await fetch(path, { method: "POST" });
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

export async function codexLogout(subscriptionVoice = false): Promise<void> {
  const path = subscriptionVoice
    ? "/api/codex/subscription-voice/logout"
    : "/api/codex/logout";
  const res = await fetch(path, { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

/**
 * Result of a live agent-CLI test (POST /api/{claude,codex,antigravity}/test).
 * Cache-busting on the backend: PATH re-augmented, the real binary spawned —
 * so the card can show WHERE the CLI was found (or which dirs were searched).
 */
export interface AgentCliTestResult {
  cli: string;
  ok: boolean;
  installed: boolean;
  binary_path: string | null;
  version: string | null;
  connected: boolean;
  auth_mode: string;
  account: string | null;
  message: string;
  searched_path: string[];
  duration_ms: number;
  cli_kind: string | null;
}

/** Runs the live CLI test behind the "Test" button on the agent cards. */
export async function testAgentCli(endpoint: string): Promise<AgentCliTestResult> {
  const res = await fetch(endpoint, { method: "POST" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as AgentCliTestResult;
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
/** Progress of an on-device provider's engine install + model download. */
export interface LocalInstallProgress {
  state: "idle" | "running" | "done" | "error";
  ready: boolean;
  message: string;
  engine_installed?: boolean;
  model_present?: boolean;
  model_label?: string;
  download_size?: string;
}

/**
 * Kick off the install of a local provider's engine and model. Returns as soon
 * as the work is handed to a background thread — the download runs for minutes,
 * so the caller polls `localInstallStatus` instead of awaiting it.
 */
export async function startLocalInstall(
  providerId: string,
): Promise<LocalInstallProgress> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/local-install`,
    { method: "POST" },
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as LocalInstallProgress;
}

/** Poll install progress; `ready` reflects the on-disk probe, not the run. */
export async function localInstallStatus(
  providerId: string,
): Promise<LocalInstallProgress> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/local-install/status`,
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as LocalInstallProgress;
}

/** Fail-closed readiness of the managed local realtime server. */
export interface ManagedServerStatus {
  ready: boolean;
  /** Install files gone while jarvis.toml still points a launch command at them. */
  stale?: boolean;
  components: Record<string, boolean>;
  sentence: string;
}

/** Live runtime view of the managed server (what the disk cannot know). */
export interface ManagedServerRuntime {
  /** Something answers on the configured port right now. */
  reachable: boolean;
  /**
   * The model pool answers — the first moment a call can actually be taken.
   * An open port only proves a socket exists, so `reachable` alone used to
   * badge a still-loading server as running while every call failed.
   */
  ready?: boolean;
  /** At least one pipeline is free for a NEW call. */
  available?: boolean;
  /** Sanitized pool snapshot; `in_use > 0` means healthy-and-serving. */
  pool?: {
    size: number;
    in_use: number;
    available: number;
    active: number;
    draining: number;
    stuck: number;
  } | null;
  port: number;
  pid: number | null;
  /** The recorded server process verifiably runs (PID-reuse safe). */
  owned: boolean;
  /** A pidfile exists but its process is gone. */
  stale: boolean;
  /**
   * Live boot verdict while an owned child loads its models, plus the
   * consecutive readiness-timeout streak that survives a reaped child.
   * `stage_label` is a backend English sentence fragment rendered verbatim
   * (managed-card doctrine: substance from the server, chrome localized).
   */
  boot?: {
    failed_streak?: number;
    starting?: boolean;
    stage?: string | null;
    stage_label?: string | null;
    elapsed_s?: number;
    expected_total_s?: number | null;
    remaining_s?: number | null;
  } | null;
}

/** Honest go/no-go report for the one-click managed install. */
export interface ManagedPreflight {
  ok: boolean;
  blocker: string;
  actions: string[];
  /** Blocked ONLY by the missing brain — the install can fix that itself. */
  brain_fixable?: boolean;
  usable_gb: number;
  memory_source: string;
  disk_free_gb: number;
  tier: {
    key: string;
    label: string;
    measured: boolean;
    target_class: string;
    download_gb: number;
    expected_latency: string;
  } | null;
  stack_sentence: string;
  brain: { kind: string; model: string; note: string } | null;
}

/** Poll-shaped progress of the managed install engine. */
export interface ManagedInstallProgress {
  phase: string;
  percent: number;
  detail: string;
  error: string;
  running: boolean;
  log_tail: string[];
  started?: boolean;
  message?: string;
}

export async function managedServerPreflight(): Promise<ManagedPreflight> {
  const res = await fetch("/api/providers/local-realtime/managed-server/preflight");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as ManagedPreflight;
}

export async function managedServerInstall(
  confirmedBrain?: string,
  brainModel?: string,
  voiceModel?: string,
): Promise<ManagedInstallProgress> {
  const res = await fetch("/api/providers/local-realtime/managed-server/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // The brain kind the user just confirmed in the preflight — the engine
    // fails the install if it re-resolves differently (no silent
    // local→cloud swap).
    body: JSON.stringify({
      confirmed_brain: confirmedBrain ?? "",
      brain_model: brainModel ?? "",
      voice_model: voiceModel ?? "",
    }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as ManagedInstallProgress;
}

export async function managedServerStatus(): Promise<{
  progress: ManagedInstallProgress;
  server: ManagedServerStatus;
  runtime?: ManagedServerRuntime;
}> {
  const res = await fetch("/api/providers/local-realtime/managed-server/status");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as {
    progress: ManagedInstallProgress;
    server: ManagedServerStatus;
    runtime?: ManagedServerRuntime;
  };
}

export async function managedServerStart(): Promise<ManagedServerRuntime | null> {
  const res = await fetch("/api/providers/local-realtime/managed-server/start", {
    method: "POST",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return (body.runtime ?? null) as ManagedServerRuntime | null;
}

/** One brain candidate for the voice server, annotated for this machine. */
export interface ManagedBrainChoice {
  id: string;
  label: string;
  size_gb: number;
  installed: boolean;
  fits: boolean;
  /** Honest reason when it does not fit; empty otherwise. */
  note: string;
  recommended: boolean;
  current: boolean;
}

export interface ManagedVoiceChoice {
  id: string;
  label: string;
  backend: string;
  model: string;
  languages: string[];
  selectable: boolean;
  recommended: boolean;
  note: string;
  speaker: string;
  current: boolean;
  platform: string;
  /** The adapter + pinned dependency are present in the managed venv. */
  runtime_ready?: boolean;
  pocket_language?: string;
  release_date?: string;
  license?: string;
  source_url?: string;
  streaming?: boolean;
  frontier?: boolean;
  size_gb?: number;
}

export interface ManagedModelCatalog {
  brain: {
    reachable: boolean;
    usable_gb: number;
    current: string;
    models: ManagedBrainChoice[];
  };
  current: string;
  models: ManagedVoiceChoice[];
  hearing: { id: string; label: string; note: string };
}

export interface ManagedSetupResult {
  ok: boolean;
  changed: boolean;
  brain: { kind: string; model: string; note: string };
  voice: { id: string; label: string };
  smoke: {
    ok: boolean;
    language: string;
    audio_bytes: number;
    first_audio_ms: number | null;
    transcript_chars: number;
  };
  runtime?: ManagedServerRuntime;
}

export async function managedServerBrainModels(): Promise<{
  reachable: boolean;
  usable_gb: number;
  current: string;
  models: ManagedBrainChoice[];
}> {
  const res = await fetch(
    "/api/providers/local-realtime/managed-server/brain-models",
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as {
    reachable: boolean;
    usable_gb: number;
    current: string;
    models: ManagedBrainChoice[];
  };
}

export async function managedServerModelCatalog(): Promise<ManagedModelCatalog> {
  const res = await fetch(
    "/api/providers/local-realtime/managed-server/model-catalog",
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as ManagedModelCatalog;
}

export async function managedServerSetup(
  brainModel: string,
  voiceModel: string,
): Promise<ManagedSetupResult> {
  const res = await fetch("/api/providers/local-realtime/managed-server/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brain_model: brainModel, voice_model: voiceModel }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as ManagedSetupResult;
}

/** Re-resolve the Ollama brain model and rewrite the launch command in place. */
export async function managedServerBrain(model?: string): Promise<{
  ok: boolean;
  changed: boolean;
  brain: { kind: string; model: string; note: string };
}> {
  const res = await fetch("/api/providers/local-realtime/managed-server/brain", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: model ?? "" }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as {
    ok: boolean;
    changed: boolean;
    brain: { kind: string; model: string; note: string };
  };
}

export async function managedServerStop(): Promise<ManagedServerRuntime | null> {
  const res = await fetch("/api/providers/local-realtime/managed-server/stop", {
    method: "POST",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return (body.runtime ?? null) as ManagedServerRuntime | null;
}

export async function managedServerUninstall(): Promise<void> {
  const res = await fetch("/api/providers/local-realtime/managed-server", {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
}

/** Live picture of the Ollama runtime itself (mirror of ollama_runtime). */
export interface OllamaRuntimeStatus {
  installed: boolean;
  binary: string;
  running: boolean;
  version: string;
  /** One honest backend sentence — render verbatim. */
  detail: string;
}

/** Poll-shaped progress of the Ollama runtime installer. */
export interface OllamaRuntimeInstallProgress {
  phase: string;
  percent: number;
  detail: string;
  error: string;
  running: boolean;
  log_tail: string[];
  started?: boolean;
  message?: string;
}

export async function ollamaRuntime(providerId: string): Promise<{
  status: OllamaRuntimeStatus;
  install: OllamaRuntimeInstallProgress;
}> {
  const res = await fetch(`/api/providers/${providerId}/ollama-runtime`);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as {
    status: OllamaRuntimeStatus;
    install: OllamaRuntimeInstallProgress;
  };
}

export async function ollamaRuntimeInstall(
  providerId: string,
): Promise<OllamaRuntimeInstallProgress> {
  const res = await fetch(`/api/providers/${providerId}/ollama-runtime/install`, {
    method: "POST",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body as OllamaRuntimeInstallProgress;
}

export async function ollamaRuntimeStart(
  providerId: string,
): Promise<OllamaRuntimeStatus | null> {
  const res = await fetch(`/api/providers/${providerId}/ollama-runtime/start`, {
    method: "POST",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return (body.status ?? null) as OllamaRuntimeStatus | null;
}

/** The job a local model fills — mirror of `ollama_pull.Role`. */
export type PullableRole = "chat" | "vision" | "coder" | "embedding";

/** One curated model a local server can download, annotated for THIS machine. */
export interface PullableModel {
  id: string;
  label: string;
  /** The registry's real download size when it answered, else the estimate. */
  size_gb: number;
  purpose: string;
  /** Which slot this fills. Absent on older payloads — treated as "chat". */
  role?: PullableRole;
  tools: boolean;
  vision: boolean;
  installed: boolean;
  /** "comfortable" | "tight" | "unknown" — advisory, never a block. */
  fit: string;
  fit_note: string;
  /**
   * The backend's pick for THIS machine in this role: the largest model it
   * runs comfortably. At most one per role, and none at all once the role has
   * something installed. Presentation hint — it never gates the pull button.
   */
  recommended?: boolean;
}

export interface PullableModels {
  server: string;
  server_reachable: boolean;
  message: string;
  memory_gb: number | null;
  /** GPU memory the fit verdicts were judged against; 0 = none readable. */
  accelerator_gb?: number;
  /** "nvidia-smi" | "apple-unified" | "none" — where that figure came from. */
  accelerator_source?: string;
  /** Role display order from the backend; the UI never invents its own. */
  roles?: PullableRole[];
  models: PullableModel[];
  installed: string[];
}

/** Progress of ONE model download on the local server. */
export interface ModelPullProgress {
  state: "idle" | "running" | "done" | "error";
  model: string;
  message: string;
  installed?: boolean;
  completed?: number;
  total?: number;
  percent?: number;
  already?: boolean;
}

/** The curated shortlist plus what this machine already holds. */
export async function pullableModels(
  providerId: string,
): Promise<PullableModels> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/pullable-models`,
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as PullableModels;
}

/**
 * Start downloading `model` into the local server. Returns as soon as the pull
 * is running — a multi-gigabyte download takes minutes, so the caller polls
 * `modelPullStatus` instead of awaiting it.
 */
export async function startModelPull(
  providerId: string,
  model: string,
): Promise<ModelPullProgress> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/pull`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    },
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as ModelPullProgress;
}

/** One model in the provider's PUBLIC library — mirror of `ollama_library`. */
export interface LibraryModel {
  name: string;
  description: string;
  /** "vision" | "tools" | "thinking" | "embedding", in the catalog's order. */
  capabilities: string[];
  /** The catalog offers a hosted variant of this model as well. */
  cloud: boolean;
  /** Parameter-count badges ("4b", "122b") — a preview of the tag list. */
  sizes: string[];
  pulls: string;
  updated: string;
  /** Whether any tag of this model is already on the local server. */
  installed?: boolean;
}

/** One installable tag of a library model, judged against THIS machine. */
export interface LibraryTag {
  tag: string;
  /** The full pullable id, e.g. "qwen3.5:4b". */
  id: string;
  /** Download size from the catalog; null when it could not be read. */
  size_gb: number | null;
  context: string;
  inputs: string;
  updated: string;
  /** Hosted-only tag: nothing to download, so it is never offered as a pull. */
  cloud: boolean;
  installed?: boolean;
  /** Same verdict vocabulary as the curated shortlist; "unknown" if no size. */
  fit?: string;
  fit_note?: string;
}

/**
 * Search the provider's public model library.
 *
 * `error` is a normal outcome, not an exception: the catalog lives on the
 * public internet, and an offline machine must still see a working panel with
 * its free-text download field.
 */
export async function searchModelLibrary(
  providerId: string,
  query: string,
): Promise<{ query: string; models: LibraryModel[]; error: string | null }> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/library/search?q=${encodeURIComponent(query)}`,
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as { query: string; models: LibraryModel[]; error: string | null };
}

/** Every published tag of one library model, with size and fit verdict. */
export async function modelLibraryTags(
  providerId: string,
  model: string,
): Promise<{ model: string; tags: LibraryTag[]; error: string | null }> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/library/${encodeURIComponent(model)}/tags`,
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as { model: string; tags: LibraryTag[]; error: string | null };
}

/** Poll one download; `installed` reflects the server's inventory, not the run. */
export async function modelPullStatus(
  providerId: string,
  model: string,
): Promise<ModelPullProgress> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/pull/status?model=${encodeURIComponent(model)}`,
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as ModelPullProgress;
}

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
  acceptExperimental = false,
): Promise<PipelineSwitchResult> {
  const res = await fetch("/api/realtime/switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: providerId,
      persist: true,
      accept_experimental: acceptExperimental,
    }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as PipelineSwitchResult;
}

/**
 * Switches the dedicated GLOBAL Computer-Use planner provider
 * (`[brain.computer_use].provider`). An OVERLAY over the brain-tier provider
 * ids — decoupled from `brain.primary` — so the same CU provider applies in
 * both Pipeline and Realtime mode. Persists to jarvis.toml (3-layer,
 * drift-guarded) and takes effect immediately on the server, so
 * `restart_required` is always false here (unlike TTS/STT/Realtime/worker).
 */
export async function switchComputerUseProvider(
  providerId: string,
): Promise<PipelineSwitchResult> {
  const res = await fetch("/api/computer-use/switch", {
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
 * Pins the model family that cleans up dictated text
 * (`[dictation].polish_provider`).
 *
 * There is no `/api/dictation/switch`: the polish tier has no live client to
 * rebuild, so the pin is an ordinary dictation setting and goes through the one
 * route that owns that block. The next dictation reads it — nothing has to
 * restart. `"auto"` (the default) is not a provider at all; it lets the
 * key-aware chain pick whichever family the user actually holds a credential
 * for and cross to another one when that family is depleted (AP-22), which is
 * why activating a card here is a *narrowing* choice, not a prerequisite.
 *
 * Takes a polish FAMILY id ("openai"), never a card id ("openai-polish") — the
 * two vocabularies differ, and only the family half means anything to the
 * chain. Callers holding a card read `descriptor.polish_family`.
 */
export async function switchDictationPolishProvider(
  familyId: string,
): Promise<void> {
  const res = await fetch("/api/dictation/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ polish_provider: familyId, persist: true }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

/**
 * Switches the active heavy-task WORKER provider
 * (`[brain.worker].provider`; legacy config aliases remain readable). Persists across 3 layers
 * (jarvis.toml + config-soll.json + ENV) so the drift guard doesn't roll (i18n-allow: "soll" is part of the config-soll.json filename)
 * back the switch. The worker re-resolves the provider before every mission,
 * so the next mission uses the selection without an app restart.
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
 * Pins the dedicated mission-worker LLM model (`[brain.worker].model`).
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

// The backend caps a test at 75 s (route-level wait_for); this client-side
// ceiling sits above it so a wedged backend can never leave the "Testing…"
// spinner running forever — the ONE state a test control must never reach.
const PROVIDER_TEST_CLIENT_TIMEOUT_MS = 80_000;

/**
 * Runs a REAL minimal call against the provider (1-token brain completion, a
 * tiny TTS synthesis, an STT transcription, or the Codex OAuth status) and
 * reports the honest outcome — not just whether a key string is stored.
 *
 * Never hangs: aborts client-side after `PROVIDER_TEST_CLIENT_TIMEOUT_MS` and
 * resolves to an honest "unreachable" result instead of rejecting, so the UI
 * always gets a renderable outcome.
 */
export async function testProvider(providerId: string): Promise<ProviderTestResult> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), PROVIDER_TEST_CLIENT_TIMEOUT_MS);
  try {
    const res = await fetch(`/api/providers/${encodeURIComponent(providerId)}/test`, {
      method: "POST",
      signal: controller.signal,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    return body as ProviderTestResult;
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      return {
        provider: providerId,
        status: "unreachable",
        detail: "No answer from the app after 80s — the backend may be busy or stuck.",
        latency_ms: PROVIDER_TEST_CLIENT_TIMEOUT_MS,
        integration_ok: false,
      };
    }
    throw e;
  } finally {
    window.clearTimeout(timer);
  }
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
  /**
   * Why a `curated` list is being shown instead of the provider's own ("no
   * Gemini API key saved yet"). Optional: only the UltraWiki slot catalog
   * fills it in today. Shown verbatim under the picker.
   */
  reason?: string;
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

// ── Realtime model + voice picker (per realtime provider) ──────────────────
// Realtime needs BOTH a model AND a voice per provider (unlike every other
// picker above, which serves ONE selection) — mirrors
// jarvis/ui/web/provider_routes.py::RealtimeOptionsResponse /
// RealtimeOptionsSaveResponse. Curated lists only (no live catalog fetch);
// an empty current_model/current_voice means "use the provider default".

export interface RealtimeOptionInfo {
  id: string;
  label: string;
}

export interface RealtimeOptionsResult {
  provider: string;
  models: RealtimeOptionInfo[];
  voices: RealtimeOptionInfo[];
  current_model: string;
  current_voice: string;
  preview_available: boolean;
}

/**
 * Lists a realtime provider's curated model + voice catalog, plus the
 * currently pinned selection. 400s for a non-realtime-tier id.
 */
export async function getRealtimeOptions(
  providerId: string,
): Promise<RealtimeOptionsResult> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/realtime-options`,
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as RealtimeOptionsResult;
}

export interface RealtimeOptionsSaveResult {
  ok: boolean;
  provider: string;
  model: string;
  voice: string;
  restart_required: boolean;
}

/**
 * Pins the model and/or voice for a realtime provider. An omitted field
 * leaves it unchanged server-side; `""` explicitly resets it to the provider
 * default. 409 without a stored credential.
 */
export async function saveRealtimeOptions(
  providerId: string,
  opts: { model?: string; voice?: string },
): Promise<RealtimeOptionsSaveResult> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/realtime-options`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts),
    },
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body as RealtimeOptionsSaveResult;
}

/**
 * Synthesises a short spoken sample of a realtime provider's voice and
 * returns it as a playable `audio/wav` blob. Mirrors
 * POST /api/providers/{id}/realtime-voice-preview. `model` matters only where
 * the sampler runs through a realtime session (openai-realtime); `""` uses
 * the adapter default. Throws with the backend's message on any failure
 * (no key / quota / transport). Callers only render this action when the
 * realtime-options response advertises `preview_available`.
 */
export async function fetchRealtimeVoicePreview(opts: {
  providerId: string;
  voice: string;
  language: "de" | "en" | "es";
  model?: string;
}): Promise<Blob> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(opts.providerId)}/realtime-voice-preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        voice: opts.voice,
        language: opts.language,
        model: opts.model ?? "",
      }),
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(
      typeof detail === "string" && detail
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message: unknown }).message)
          : `HTTP ${res.status}`,
    );
  }
  return await res.blob();
}
