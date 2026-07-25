/**
 * Typed fetchers for the UltraWiki REST surface
 * (`jarvis/ui/web/ultrawiki_routes.py`).
 *
 * Contract notes (mirror the route module's docstring):
 * - `GET /api/ultrawiki/status` ALWAYS answers, even while the mode is off —
 *   it is the honesty surface. `fetchUltraWikiStatus` therefore swallows
 *   network failures and returns `null` (same idiom as `fetchWikiHealth`),
 *   because it is polled and a transient blip must not surface as an
 *   unhandled rejection.
 * - Search answers 409 (not 503) while the mode switch is off; 503 means the
 *   service is not wired yet (app still starting). Both arrive here as an
 *   {@link UltraWikiApiError} carrying the backend's honest `detail`.
 */

// Mirrors jarvis/ultrawiki/types.py::ItemState — five-layer anti-drift
// discipline (AP-4 / BUG-008): never retype these values elsewhere.
export type UltraWikiItemState =
  | "captured"
  | "keyword_indexed"
  | "embedded"
  | "distilled"
  | "failed";

// Mirrors jarvis/ultrawiki/types.py::ConsentState.
export type UltraWikiConsent = "pending" | "approved" | "revoked";

// Mirrors jarvis/ultrawiki/service.py::JOB_ACTIVE_STATUSES / JOB_TERMINAL_STATUSES.
export type UltraWikiJobStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "cancelled";

export const ULTRAWIKI_ACTIVE_JOB_STATUSES: readonly string[] = [
  "queued",
  "running",
];

// Mirrors jarvis/ultrawiki/connectors/__init__.py::builtin_connectors().
export const ULTRAWIKI_CONNECTOR_IDS = [
  "obsidian-vault",
  "local-folder",
  "jarvis-conversations",
  "normal-wiki",
  "plugin-bridge",
] as const;
export type UltraWikiConnectorId = (typeof ULTRAWIKI_CONNECTOR_IDS)[number];

export interface UltraWikiCounts {
  captured: number;
  keyword_indexed: number;
  embedded: number;
  distilled: number;
  failed: number;
  total: number;
}

export interface UltraWikiSource {
  id: string;
  connector: string;
  label: string;
  consent: UltraWikiConsent | string;
  enabled: boolean;
  areas: string[];
  counts: Partial<UltraWikiCounts> | null;
  sync_state: Record<string, unknown> | null;
  last_sync_at: string | null;
  last_error: string | null;
}

export interface UltraWikiJob {
  job_id: string;
  source_id: string;
  mode: string;
  status: UltraWikiJobStatus | string;
  started_at: number;
  ended_at: number | null;
  chunks: number;
  new: number;
  changed: number;
  unchanged: number;
  tombstoned: number;
  error: string;
}

/** One embedding/rerank option row from `GET /api/ultrawiki/providers`. */
export interface UltraWikiProviderOption {
  name: string;
  ready: boolean;
  reason: string;
  default_model?: string;
  /** One-line English explanation of what this option is. */
  detail?: string;
}

export interface UltraWikiDbBackendOption {
  name: string;
  ready: boolean;
  reason: string;
  detail: string;
  secret_present?: boolean;
}

export interface UltraWikiProviders {
  embedding: UltraWikiProviderOption[];
  rerank: UltraWikiProviderOption[];
  db_backends: UltraWikiDbBackendOption[];
}

/**
 * Live ranking knobs of the read path — mirrors
 * jarvis/ultrawiki/search.py::ranking_settings.
 */
export interface UltraWikiRanking {
  keyword_weight: number;
  vector_weight: number;
  recency_half_life_days: number;
  /** Absolute 0-10 relevance floor for UNSOLICITED surfaces; 0 = off. */
  rerank_min_score: number;
}

export interface UltraWikiSlotStatus {
  provider: string;
  model?: string;
  ready: boolean;
  reason: string;
  via?: string;
  available?: UltraWikiProviderOption[];
  chain?: string[];
  /** Only on the rerank slot: the knobs that slot governs. */
  ranking?: Partial<UltraWikiRanking>;
}

export interface UltraWikiStorageSlot {
  configured: string;
  in_use: string;
  ready: boolean;
  reason: string;
  vector?: { ready?: boolean; reason?: string };
}

// Mirrors jarvis/ultrawiki/service.py::PIPELINE_STATES — five-layer anti-drift
// discipline (AP-4 / BUG-008): never retype these values elsewhere.
export type UltraWikiPipelineState =
  | "waiting_for_sources"
  | "idle"
  | "processing"
  | "paused";

export const ULTRAWIKI_PIPELINE_STATES: readonly UltraWikiPipelineState[] = [
  "waiting_for_sources",
  "idle",
  "processing",
  "paused",
];

export interface UltraWikiPipeline {
  /**
   * The worker LOOP is alive — NOT the same as work being done. Reporting this
   * alone read as "something is already pulling my data" on a fresh activation
   * with zero approved sources; `state` is what the UI shows.
   */
  running: boolean;
  state?: UltraWikiPipelineState | string;
  /** One honest English sentence: what is happening, or what blocks it. */
  reason?: string;
  processed: Record<string, number>;
}

export interface UltraWikiSearchLeg {
  available: boolean;
  backend?: string;
  model?: string;
  provider?: string;
  reason?: string;
}

export interface UltraWikiStatus {
  enabled: boolean;
  started: boolean;
  db_backend: string;
  backend_in_use: string;
  slots: {
    embedding?: UltraWikiSlotStatus;
    distill?: UltraWikiSlotStatus;
    rerank?: UltraWikiSlotStatus;
    storage?: UltraWikiStorageSlot;
  };
  counts: Partial<UltraWikiCounts>;
  pipeline: UltraWikiPipeline;
  sources: UltraWikiSource[];
  jobs: UltraWikiJob[];
  search_legs: {
    keyword?: UltraWikiSearchLeg;
    vector?: UltraWikiSearchLeg;
    rerank?: UltraWikiSearchLeg;
    error?: string;
  };
  degradations: string[];
}

/** Mirrors jarvis/ultrawiki/types.py::SearchResult (dataclasses.asdict). */
export interface UltraWikiSearchHit {
  item_id: number;
  source_id: string;
  title: string;
  snippet: string;
  permalink: string;
  timestamp_utc: string;
  /** ORDINAL fused rank score (RRF) — comparable between hits, never absolute. */
  score: number;
  matched_by: string[];
  /** ABSOLUTE 0-10 relevance grade; null when the rerank stage did not run. */
  rerank_score: number | null;
  /** Neighbouring evidence pulled back in after ranking; may be empty. */
  context: string[];
}

export interface UltraWikiSearchResponse {
  query: string;
  results: UltraWikiSearchHit[];
  total: number;
}

export interface UltraWikiActivateBody {
  db_backend?: string;
  embedding_provider: string;
  embedding_model?: string;
  distill_provider?: string;
  distill_model?: string;
  rerank_provider?: string;
  rerank_model?: string;
  areas?: string[];
}

export interface UltraWikiActivateResponse {
  ok: boolean;
  enabled: boolean;
  persisted: boolean;
  next_steps: string;
  persist_error?: string;
  default_area?: string;
  sources_created?: string[];
  sources_existing?: string[];
}

export interface UltraWikiDeactivateResponse {
  ok: boolean;
  enabled: boolean;
  persisted: boolean;
  non_destructive: boolean;
  detail: string;
  persist_error?: string;
}

export interface UltraWikiSettingsBody {
  db_backend?: string;
  /**
   * The named storage preset (sqlite / supabase / neon / postgres). Send this
   * rather than `db_backend`: the backend derives the two-value functional
   * enum from the preset, so the UI never has to mirror it (AP-4).
   */
  storage_provider?: string;
  embedding_provider?: string;
  embedding_model?: string;
  distill_provider?: string;
  distill_model?: string;
  rerank_provider?: string;
  /** Only meaningful for rerank_provider="llm"; empty = cheap chain default. */
  rerank_model?: string;
  ollama_endpoint?: string;
  /** Ranking knobs — see UltraWikiRanking. Sent only when the user edits one. */
  rerank_min_score?: number;
  rrf_keyword_weight?: number;
  rrf_vector_weight?: number;
  recency_half_life_days?: number;
  confirm_reembed?: boolean;
}

// Mirrors jarvis/ultrawiki/provider_catalog.py::SlotName / UltraWikiAuthMode —
// five-layer anti-drift discipline (AP-4 / BUG-008): never retype these.
export const ULTRAWIKI_SLOT_NAMES = [
  "storage",
  "embedding",
  "distill",
  "rerank",
] as const;
export type UltraWikiSlotName = (typeof ULTRAWIKI_SLOT_NAMES)[number];

export type UltraWikiAuthMode =
  | "none"
  | "api_key"
  | "connection_string"
  | "managed_link";

/**
 * One provider row of `GET /api/ultrawiki/catalog`: the declared spec plus the
 * live truth about THIS machine (which credential slots hold a value, which
 * other Jarvis surfaces share them, whether the provider's own probe passes).
 */
export interface UltraWikiCatalogRow {
  id: string;
  slot: UltraWikiSlotName;
  label: string;
  auth_mode: UltraWikiAuthMode;
  secret_keys: string[];
  dashboard_url: string | null;
  credential_help: string;
  default_model: string;
  supports_base_url: boolean;
  default_base_url: string | null;
  recommended: boolean;
  caution: string | null;
  db_backend: string | null;
  connection_hint: string | null;
  ready: boolean;
  reason: string;
  selected: boolean;
  secrets_set: Record<string, boolean>;
  secret_shared_with: Record<string, string[]>;
}

export interface UltraWikiCatalog {
  slots: Record<UltraWikiSlotName, UltraWikiCatalogRow[]>;
  selected: Record<UltraWikiSlotName, string>;
  models: { embedding: string; distill: string };
  ollama_endpoint: string;
}

/**
 * Mirrors `GET /api/ultrawiki/models/{slot}` — deliberately the same shape as
 * `BrainModelsResult` so the shared picker consumes it unchanged.
 */
export interface UltraWikiSlotModels {
  provider: string;
  current_model: string;
  models: { id: string; label: string }[];
  source: "live" | "cache" | "static" | "curated";
  fetched_at: number;
  selects?: "model" | "voice";
  /** Why a curated list is shown ("no Gemini API key saved yet"). */
  reason?: string;
}

export interface SupabaseProject {
  ref: string;
  name: string;
  region: string;
  status: string;
}

export interface SupabaseProjectsResponse {
  projects: SupabaseProject[];
  total: number;
  tokens_url: string;
}

export interface SupabaseLinkBody {
  project_ref: string;
  db_password: string;
  pool_mode?: "transaction" | "session";
  save_anyway?: boolean;
}

export interface SupabaseLinkResponse {
  ok: boolean;
  project_ref: string;
  endpoint: {
    host: string;
    port: number;
    user: string;
    database: string;
    mode: string;
  };
  note: string;
  probe_ok: boolean;
  probe_detail: string;
  persisted: boolean;
  restart_required: boolean;
  detail: string;
  persist_error?: string;
}

export interface UltraWikiSettingsResponse {
  ok: boolean;
  changed: string[];
  persisted: boolean;
  reembed_started: boolean;
  persist_error?: string;
}

export interface UltraWikiSlotTestResult {
  ok: boolean;
  detail: string;
  latency_ms: number;
}

export interface UltraWikiBridgeCandidate {
  id: string;
  kind: "plugin" | "mcp" | string;
  label: string;
  detail: string;
}

export interface UltraWikiArea {
  id: string;
  name: string;
}

/** Error carrying the backend's honest `detail` payload (string or object). */
export class UltraWikiApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = "UltraWikiApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** The 409 payload of a guarded embedding change (PUT /settings). */
export function reembedGateOf(
  error: unknown,
): { message: string; vector_items: number } | null {
  if (!(error instanceof UltraWikiApiError) || error.status !== 409) return null;
  const detail = error.detail;
  if (
    detail &&
    typeof detail === "object" &&
    "vector_items" in detail &&
    typeof (detail as { vector_items: unknown }).vector_items === "number"
  ) {
    return {
      message: String((detail as { message?: unknown }).message ?? ""),
      vector_items: (detail as { vector_items: number }).vector_items,
    };
  }
  return null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // A non-JSON body (proxy error page) falls through to the status error.
  }
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail &&
            typeof detail === "object" &&
            typeof (detail as { message?: unknown }).message === "string"
          ? (detail as { message: string }).message
          : `HTTP ${res.status} ${res.statusText}`;
    throw new UltraWikiApiError(message, res.status, detail);
  }
  return body as T;
}

function postJson<T>(url: string, payload?: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

/**
 * Poll-safe status fetch: swallows HTTP and network failures and returns
 * `null` ("unknown") instead of throwing — the mode toggle and the Ultra
 * panel treat `null` as "backend unreachable" and stay honest about it.
 */
export async function fetchUltraWikiStatus(): Promise<UltraWikiStatus | null> {
  try {
    const res = await fetch("/api/ultrawiki/status");
    if (!res.ok) return null;
    return (await res.json()) as UltraWikiStatus;
  } catch {
    return null;
  }
}

export function fetchUltraWikiProviders(): Promise<UltraWikiProviders> {
  return request<UltraWikiProviders>("/api/ultrawiki/providers");
}

export function fetchUltraWikiCatalog(): Promise<UltraWikiCatalog> {
  return request<UltraWikiCatalog>("/api/ultrawiki/catalog");
}

/**
 * The selectable models for one slot's provider, in the exact shape the
 * API-Keys model picker consumes — so the slots get THAT picker (searchable,
 * refreshable, custom ids allowed) instead of a free-text box.
 */
export function fetchUltraWikiSlotModels(
  slot: UltraWikiSlotName,
  provider: string,
  refresh = false,
): Promise<UltraWikiSlotModels> {
  const params = new URLSearchParams();
  if (provider) params.set("provider", provider);
  if (refresh) params.set("refresh", "true");
  const query = params.toString();
  return request<UltraWikiSlotModels>(
    `/api/ultrawiki/models/${encodeURIComponent(slot)}${query ? `?${query}` : ""}`,
  );
}

export function fetchSupabaseProjects(): Promise<SupabaseProjectsResponse> {
  return request<SupabaseProjectsResponse>(
    "/api/ultrawiki/storage/supabase/projects",
  );
}

export function linkSupabaseProject(
  body: SupabaseLinkBody,
): Promise<SupabaseLinkResponse> {
  return postJson<SupabaseLinkResponse>(
    "/api/ultrawiki/storage/supabase/link",
    body,
  );
}

/**
 * The 409 payload of a refused Supabase link — the connection probe failed and
 * nothing was saved. Carries the sanitized reason plus the offer to save
 * anyway, for the user whose database is only reachable over a VPN.
 */
export function supabaseLinkProbeFailureOf(error: unknown): {
  message: string;
  probe_detail: string;
  can_save_anyway: boolean;
} | null {
  if (!(error instanceof UltraWikiApiError) || error.status !== 409) return null;
  const detail = error.detail;
  if (
    detail &&
    typeof detail === "object" &&
    (detail as { can_save_anyway?: unknown }).can_save_anyway === true
  ) {
    const d = detail as { message?: unknown; probe_detail?: unknown };
    return {
      message: String(d.message ?? ""),
      probe_detail: String(d.probe_detail ?? ""),
      can_save_anyway: true,
    };
  }
  return null;
}

export function activateUltraWiki(
  body: UltraWikiActivateBody,
): Promise<UltraWikiActivateResponse> {
  return postJson<UltraWikiActivateResponse>("/api/ultrawiki/activate", body);
}

export function deactivateUltraWiki(): Promise<UltraWikiDeactivateResponse> {
  return postJson<UltraWikiDeactivateResponse>("/api/ultrawiki/deactivate");
}

export function updateUltraWikiSettings(
  body: UltraWikiSettingsBody,
): Promise<UltraWikiSettingsResponse> {
  return request<UltraWikiSettingsResponse>("/api/ultrawiki/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function testUltraWikiSlot(
  slot: "embedding" | "distill" | "rerank" | "storage",
): Promise<UltraWikiSlotTestResult> {
  return postJson<UltraWikiSlotTestResult>(
    `/api/ultrawiki/test/${encodeURIComponent(slot)}`,
  );
}

export function createUltraWikiSource(body: {
  connector: string;
  label: string;
  config?: Record<string, unknown>;
  areas?: string[];
}): Promise<UltraWikiSource> {
  return postJson<UltraWikiSource>("/api/ultrawiki/sources", body);
}

export function approveUltraWikiSource(
  sourceId: string,
): Promise<UltraWikiSource> {
  return postJson<UltraWikiSource>(
    `/api/ultrawiki/sources/${encodeURIComponent(sourceId)}/approve`,
  );
}

export function revokeUltraWikiSource(
  sourceId: string,
): Promise<UltraWikiSource> {
  return postJson<UltraWikiSource>(
    `/api/ultrawiki/sources/${encodeURIComponent(sourceId)}/revoke`,
  );
}

/**
 * Start a sync. `full` clears the resume checkpoint and the incremental
 * cursor so everything is re-read — the only mode in which deletions are
 * detected (an incremental run cannot see a file that no longer exists).
 */
export function startUltraWikiSync(
  sourceId: string,
  options: { full?: boolean } = {},
): Promise<{
  job_id: string;
  status: string;
  source_id: string;
  full?: boolean;
}> {
  return postJson(
    `/api/ultrawiki/sources/${encodeURIComponent(sourceId)}/sync`,
    { full: Boolean(options.full) },
  );
}

/** Return dead-lettered items to the pipeline (after fixing what broke). */
export function requeueUltraWikiFailed(
  sourceId?: string,
): Promise<{ ok: boolean; requeued: number; source_id: string; detail: string }> {
  return postJson("/api/ultrawiki/pipeline/requeue-failed", {
    source_id: sourceId ?? "",
  });
}

export function cancelUltraWikiJob(
  jobId: string,
): Promise<{ job_id: string; cancel_requested: boolean }> {
  return postJson<{ job_id: string; cancel_requested: boolean }>(
    `/api/ultrawiki/jobs/${encodeURIComponent(jobId)}/cancel`,
  );
}

export function fetchUltraWikiBridgeCandidates(): Promise<{
  candidates: UltraWikiBridgeCandidate[];
  total: number;
}> {
  return request<{ candidates: UltraWikiBridgeCandidate[]; total: number }>(
    "/api/ultrawiki/bridge/candidates",
  );
}

export function fetchUltraWikiAreas(): Promise<{
  areas: UltraWikiArea[];
  total: number;
}> {
  return request<{ areas: UltraWikiArea[]; total: number }>(
    "/api/ultrawiki/areas",
  );
}

export function searchUltraWiki(
  query: string,
  k = 20,
): Promise<UltraWikiSearchResponse> {
  const params = new URLSearchParams({ q: query, k: String(k) });
  return request<UltraWikiSearchResponse>(
    `/api/ultrawiki/search?${params.toString()}`,
  );
}

/** True when any sync job is still queued or running (drives poll cadence). */
export function hasActiveUltraWikiJobs(
  jobs: UltraWikiJob[] | undefined | null,
): boolean {
  return (jobs ?? []).some((job) =>
    ULTRAWIKI_ACTIVE_JOB_STATUSES.includes(job.status),
  );
}
