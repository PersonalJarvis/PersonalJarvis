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

export interface UltraWikiSlotStatus {
  provider: string;
  model?: string;
  ready: boolean;
  reason: string;
  available?: UltraWikiProviderOption[];
  chain?: string[];
}

export interface UltraWikiStorageSlot {
  configured: string;
  in_use: string;
  ready: boolean;
  reason: string;
  vector?: { ready?: boolean; reason?: string };
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
  pipeline: { running: boolean; processed: Record<string, number> };
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
  score: number;
  matched_by: string[];
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
  embedding_provider?: string;
  embedding_model?: string;
  distill_provider?: string;
  distill_model?: string;
  rerank_provider?: string;
  confirm_reembed?: boolean;
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

export function startUltraWikiSync(
  sourceId: string,
): Promise<{ job_id: string; status: string; source_id: string }> {
  return postJson<{ job_id: string; status: string; source_id: string }>(
    `/api/ultrawiki/sources/${encodeURIComponent(sourceId)}/sync`,
  );
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
