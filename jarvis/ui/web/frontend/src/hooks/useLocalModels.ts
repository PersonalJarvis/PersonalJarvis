/**
 * Fetch helpers and React Query hooks for the "Local models" section.
 *
 * Endpoints: `jarvis/ui/web/local_models_routes.py`, all under
 * `/api/providers/{id}/local-models/...` and gated on a pull-capable card
 * (Ollama today). The interfaces below are the TS half of the AP-4 contract —
 * they mirror the Pydantic response models one to one.
 *
 * The pull helpers (`startModelPull`, `modelPullStatus`) stay in
 * `useProviders.ts` and are re-exported here so a panel imports one module.
 */
import {
  keepPreviousData,
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import {
  readOverviewSnapshot,
  writeOverviewSnapshot,
} from "../lib/localModelsSnapshot";
import type { OllamaModelOptions } from "../lib/ollamaModelOptions";
import type {
  LibraryModel,
  LibraryTag,
  ModelPullProgress,
  PullableModels,
} from "./useProviders";

export { startModelPull, modelPullStatus } from "./useProviders";
export type {
  LibraryModel,
  LibraryTag,
  ModelPullProgress,
  PullableModels,
} from "./useProviders";
export type { OllamaModelOptions } from "../lib/ollamaModelOptions";

// ---------------------------------------------------------------------------
// Wire types — inventory
// ---------------------------------------------------------------------------

/** The five writable slots (voice = the managed voice server's own brain). */
export type LocalModelRole = "chat" | "voice" | "tools_screen" | "deep" | "embedding";
/** Every role row the backend renders, read-only ones included. */
export type LocalModelRoleId = LocalModelRole | "ack" | "polish";

export interface RunningModelRow {
  name: string;
  size_bytes: number;
  size_vram_bytes: number;
  expires_at: string;
  context_length: number | null;
  digest: string;
}

export interface LocalModelRow {
  name: string;
  size_bytes: number;
  digest: string;
  modified_at: string;
  family: string;
  parameter_size: string;
  quantization_level: string;
  /** Native window from the manifest; null when unknown. */
  context_length: number | null;
  /** "completion" | "vision" | "tools" | "thinking" | "embedding" | "audio" | "insert". */
  capabilities: string[];
  license: string;
  /** False when /api/show failed for this row — "unknown", not "none". */
  probed: boolean;
  used_by: LocalModelRole[];
  loaded: boolean;
  size_vram_bytes: number;
  expires_at: string;
  running_context_length: number | null;
}

export interface LocalModelDetail extends LocalModelRow {
  parameters: string;
  template: string;
}

export interface InventoryResponse {
  provider: string;
  server: string;
  models: LocalModelRow[];
  running: RunningModelRow[];
  disk_bytes: number;
  loaded_vram_bytes: number;
  /** Sentence when the server did not answer; show it instead of "nothing installed". */
  error: string | null;
}

export interface UnloadResponse {
  ok: boolean;
  model: string;
  message: string;
}

export interface DeleteResponse {
  ok: boolean;
  model: string;
  message: string;
  reassigned: LocalModelRole[];
  reassigned_to: string | null;
}

// ---------------------------------------------------------------------------
// Wire types — roles
// ---------------------------------------------------------------------------

export interface RoleRow {
  id: LocalModelRoleId;
  /** i18n key, e.g. "local_models.role_chat". */
  label_key: string;
  /** Dotted config path for the footnote. */
  config_key: string;
  /** Configured tag; "" = the plugin discovers one. */
  current: string;
  /** `current` is on the server right now. */
  installed: boolean;
  required: string[];
  recommended_capabilities: string[];
  /** Installed tags declaring every required capability. */
  qualifying: string[];
  /**
   * The pick for this machine: the best qualifying INSTALLED download, or
   * the shortlist's download when nothing installed qualifies; "" when
   * neither has one.
   */
  recommended: string;
  /** One backend sentence saying why `recommended` is the pick; "" when none. */
  recommended_reason?: string;
  writable: boolean;
  advanced: boolean;
  /** Sentence when the slot is served by something other than Ollama. */
  note: string;
  /** The voice brain's effective context on this machine (voice role only). */
  context_tokens?: number | null;
  /** "automatic" (sized from memory) | "manual" (set in Tune) | "". */
  context_source?: "" | "automatic" | "manual";
}

export interface IdleReleaseResponse {
  /** Minutes of voice idleness before the local stack frees memory; 0 = never. */
  minutes: number;
}

export interface RolesResponse {
  provider: string;
  server: string;
  roles: RoleRow[];
  error: string | null;
}

/** Mirror of `AutostartResponse` (`GET/PUT …/runtime/autostart`). */
export interface AutostartResponse {
  /** `[brain.providers.ollama].autostart` — start the server with Jarvis. */
  enabled: boolean;
  /** Something in this install runs on the local server right now. */
  in_use: boolean;
  /** One backend sentence: what the boot task would do and why. */
  reason: string;
}

/** One step of `POST …/verify`; `ok: null` = not run (role unset / server down). */
export interface VerifyStep {
  id: "server" | "chat" | "embedding" | string;
  ok: boolean | null;
  model: string;
  detail: string;
  ms: number;
}

/** Mirror of `VerifyResponse` (`POST …/verify`). */
export interface VerifyResponse {
  ok: boolean;
  status: "ok" | "needs_setup" | "error" | string;
  reason: string;
  steps: VerifyStep[];
}

export interface RoleSetBody {
  /** "" = back to discovery (brain roles only). */
  model: string;
}

export interface RoleSetResponse {
  ok: boolean;
  role: string;
  model: string;
  config_key: string;
  message: string;
}

// ---------------------------------------------------------------------------
// Wire types — options
// ---------------------------------------------------------------------------

export interface ModelOptionsResponse {
  model: string;
  /** Only the knobs that are set. */
  options: OllamaModelOptions;
  configured: boolean;
  /** The derived alias the brain streams through; null when none is needed. */
  profile_alias: string | null;
}

export interface SuggestedOptionsResponse {
  model: string;
  options: OllamaModelOptions;
  /** One plain sentence per knob, plus one for the memory budget. */
  reasons: string[];
  size_gb: number;
  native_context: number | null;
  accelerator_gb: number;
  /** "nvidia-smi" | "apple-unified" | "none" ... */
  accelerator_source: string;
  ram_gb: number | null;
}

// ---------------------------------------------------------------------------
// Wire types — catalogue
// ---------------------------------------------------------------------------

export type CatalogSort = "popular" | "newest";
export type CatalogCapability = "tools" | "vision" | "embedding" | "thinking";

export interface CatalogSearchResponse {
  query: string;
  sort: CatalogSort;
  capability: CatalogCapability | null;
  models: LibraryModel[];
  error: string | null;
}

/** One tag with the catalogue's two new columns. */
export interface CatalogTag extends LibraryTag {
  /** From the tag name ("q8_0", "bf16", "iq2_xs"); "" for a default tag. */
  quantization: string;
}

export interface CatalogTagsResponse {
  model: string;
  tags: CatalogTag[];
  error: string | null;
}

export interface CatalogRecommendedModel {
  id: string;
  label: string;
  size_gb: number;
  purpose: string;
  role: "chat" | "vision" | "coder" | "embedding";
  tools: boolean;
  vision: boolean;
  installed: boolean;
  fit: "comfortable" | "tight" | "unknown" | string;
  fit_note: string;
  recommended: boolean;
  /** The roles this row is the pick for on this machine. */
  recommended_for: Array<"chat" | "vision" | "coder" | "embedding">;
}

export interface CatalogRecommendedResponse extends Omit<
  PullableModels,
  "models"
> {
  /** ISO date the maintainer last reviewed the shortlist. */
  curated_reviewed_on: string;
  models: CatalogRecommendedModel[];
}

// ---------------------------------------------------------------------------
// Wire types — Hugging Face
// ---------------------------------------------------------------------------

export type HfSort = "downloads" | "lastModified" | "trendingScore";

export interface HfRepo {
  id: string;
  author: string;
  downloads: number;
  likes: number;
  /** ISO 8601; "" when unknown. */
  last_modified: string;
  architecture: string;
  total_params: number | null;
  context_length: number | null;
}

export interface HfFile {
  filename: string;
  /** "Q4_K_M" ...; null = pull the repository default. */
  quant: string | null;
  size_gb: number | null;
  fit: "comfortable" | "tight" | "unknown";
  fit_note: string;
}

export interface HfSearchResponse {
  repos: HfRepo[];
  error: string | null;
}

export interface HfFilesResponse {
  files: HfFile[];
  error: string | null;
}

export interface HfPullBody {
  user: string;
  repo: string;
  quant?: string | null;
}

export interface HfEnabledResponse {
  enabled: boolean;
}

// ---------------------------------------------------------------------------
// Wire types — server
// ---------------------------------------------------------------------------

export type HostKind = "local" | "remote";

export interface ServerResponse {
  installed: boolean;
  binary: string;
  running: boolean;
  version: string;
  detail: string;
  base_url: string;
  /** Hide Install / Start / Stop / log when "remote". */
  host_kind: HostKind;
  models_dir: string;
  running_models: RunningModelRow[];
  disk_bytes: number;
  loaded_vram_bytes: number;
  error: string | null;
}

export interface ServerActionResponse {
  ok: boolean;
  message: string;
}

export interface ServerProbeResponse {
  ok: boolean;
  version: string;
  latency_ms: number;
  detail: string;
}

export interface ServerLogResponse {
  lines: string[];
}

export type EnvGuideOs = "windows" | "macos" | "linux";

export interface EnvGuideRow {
  key: string;
  purpose: string;
  /** The copyable line. */
  command: string;
  restart: string;
}

export interface EnvGuideResponse {
  os: EnvGuideOs;
  rows: EnvGuideRow[];
}

// ---------------------------------------------------------------------------
// Wire types — overview (one round-trip for the whole Simple page)
// ---------------------------------------------------------------------------

/**
 * Mirror of `OverviewResponse` in `jarvis/ui/web/local_models_routes.py`
 * (`GET …/local-models/overview`). A Python parity test reads these six
 * field names — keep them verbatim.
 */
export interface OverviewResponse {
  server: ServerResponse;
  roles: RolesResponse;
  inventory: InventoryResponse;
  recommended: CatalogRecommendedResponse;
  /** "live" = built for this request; "cache" = a stored snapshot, refresh underway. */
  source: "live" | "cache";
  /** Epoch seconds the payload was built. */
  fetched_at: number;
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

/** A non-2xx answer, with the status kept so callers can branch on it. */
export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
  }
}

/** Long enough for a cold /api/show sweep over a big inventory or a registry read. */
const READ_TIMEOUT_MS = 20_000;
/** Config writes and server actions answer within a few seconds or not at all. */
const WRITE_TIMEOUT_MS = 15_000;

function base(providerId: string): string {
  return `/api/providers/${encodeURIComponent(providerId)}/local-models`;
}

/** Model names may carry ":" and "/" (hf.co/user/repo:Q4_K_M); keep the slashes. */
function modelPath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

async function request<T>(
  url: string,
  init: RequestInit = {},
  timeoutMs = READ_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new HttpError(
        res.status,
        (body as { detail?: string }).detail ?? `HTTP ${res.status}`,
      );
    }
    return body as T;
  } finally {
    clearTimeout(timer);
  }
}

function json(method: "POST" | "PUT", payload: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

// inventory

export async function getInventory(
  providerId: string,
): Promise<InventoryResponse> {
  return request(`${base(providerId)}/inventory`);
}

export async function getInventoryModel(
  providerId: string,
  name: string,
): Promise<LocalModelDetail> {
  return request(`${base(providerId)}/inventory/${modelPath(name)}`);
}

export async function unloadModel(
  providerId: string,
  name: string,
): Promise<UnloadResponse> {
  return request(
    `${base(providerId)}/inventory/${modelPath(name)}/unload`,
    { method: "POST" },
    WRITE_TIMEOUT_MS,
  );
}

export async function deleteModel(
  providerId: string,
  name: string,
  reassign?: string,
): Promise<DeleteResponse> {
  const qs = reassign ? `?reassign=${encodeURIComponent(reassign)}` : "";
  return request(
    `${base(providerId)}/inventory/${modelPath(name)}${qs}`,
    { method: "DELETE" },
    WRITE_TIMEOUT_MS,
  );
}

// roles

export async function getRoles(providerId: string): Promise<RolesResponse> {
  return request(`${base(providerId)}/roles`);
}

export async function getIdleRelease(
  providerId: string,
): Promise<IdleReleaseResponse> {
  return request(`${base(providerId)}/runtime/idle-release`);
}

export async function setIdleRelease(
  providerId: string,
  minutes: number,
): Promise<IdleReleaseResponse> {
  return request(
    `${base(providerId)}/runtime/idle-release`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes }),
    },
    WRITE_TIMEOUT_MS,
  );
}

export async function getAutostart(
  providerId: string,
): Promise<AutostartResponse> {
  return request(`${base(providerId)}/runtime/autostart`);
}

export async function setAutostart(
  providerId: string,
  enabled: boolean,
): Promise<AutostartResponse> {
  return request(
    `${base(providerId)}/runtime/autostart`,
    json("PUT", { enabled }),
    WRITE_TIMEOUT_MS,
  );
}

/** Three real round trips: the server, a chat answer, an embedding. */
const VERIFY_TIMEOUT_MS = 120_000;

export async function verifySetup(providerId: string): Promise<VerifyResponse> {
  return request(
    `${base(providerId)}/verify`,
    { method: "POST" },
    VERIFY_TIMEOUT_MS,
  );
}

export async function setRole(
  providerId: string,
  role: LocalModelRole,
  model: string,
): Promise<RoleSetResponse> {
  const body: RoleSetBody = { model };
  return request(
    `${base(providerId)}/roles/${encodeURIComponent(role)}`,
    json("PUT", body),
    WRITE_TIMEOUT_MS,
  );
}

// options

export async function getModelOptions(
  providerId: string,
  name: string,
): Promise<ModelOptionsResponse> {
  return request(`${base(providerId)}/models/${modelPath(name)}/options`);
}

/** REPLACE semantics: send the whole set; an empty object clears the profile. */
export async function saveModelOptions(
  providerId: string,
  name: string,
  options: OllamaModelOptions,
): Promise<ModelOptionsResponse> {
  return request(
    `${base(providerId)}/models/${modelPath(name)}/options`,
    json("PUT", options),
    WRITE_TIMEOUT_MS,
  );
}

export async function resetModelOptions(
  providerId: string,
  name: string,
): Promise<ModelOptionsResponse> {
  return request(
    `${base(providerId)}/models/${modelPath(name)}/options`,
    { method: "DELETE" },
    WRITE_TIMEOUT_MS,
  );
}

export async function getSuggestedOptions(
  providerId: string,
  name: string,
): Promise<SuggestedOptionsResponse> {
  return request(
    `${base(providerId)}/models/${modelPath(name)}/suggested-options`,
  );
}

// catalogue

export interface CatalogQuery {
  q?: string;
  sort?: CatalogSort;
  capability?: CatalogCapability | null;
  limit?: number;
}

export async function searchCatalog(
  providerId: string,
  query: CatalogQuery = {},
): Promise<CatalogSearchResponse> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.sort) params.set("sort", query.sort);
  if (query.capability) params.set("capability", query.capability);
  if (query.limit) params.set("limit", String(query.limit));
  const qs = params.toString();
  return request(`${base(providerId)}/catalog${qs ? `?${qs}` : ""}`);
}

export async function getCatalogTags(
  providerId: string,
  name: string,
): Promise<CatalogTagsResponse> {
  return request(`${base(providerId)}/catalog/${modelPath(name)}/tags`);
}

export async function getCatalogRecommended(
  providerId: string,
): Promise<CatalogRecommendedResponse> {
  return request(`${base(providerId)}/catalog/recommended`);
}

// Hugging Face

export async function getHfEnabled(
  providerId: string,
): Promise<HfEnabledResponse> {
  return request(`${base(providerId)}/hf/enabled`);
}

export async function setHfEnabled(
  providerId: string,
  enabled: boolean,
): Promise<HfEnabledResponse> {
  return request(
    `${base(providerId)}/hf/enabled`,
    json("PUT", { enabled }),
    WRITE_TIMEOUT_MS,
  );
}

export async function searchHf(
  providerId: string,
  q: string,
  sort: HfSort = "downloads",
  limit = 30,
): Promise<HfSearchResponse> {
  const params = new URLSearchParams({ q, sort, limit: String(limit) });
  return request(`${base(providerId)}/hf/search?${params.toString()}`);
}

export async function getHfFiles(
  providerId: string,
  user: string,
  repo: string,
): Promise<HfFilesResponse> {
  return request(
    `${base(providerId)}/hf/${encodeURIComponent(user)}/${encodeURIComponent(repo)}/files`,
  );
}

/** Starts `ollama pull hf.co/<user>/<repo>[:<quant>]`; poll with `modelPullStatus`. */
export async function startHfPull(
  providerId: string,
  body: HfPullBody,
): Promise<ModelPullProgress> {
  return request(
    `${base(providerId)}/hf/pull`,
    json("POST", body),
    WRITE_TIMEOUT_MS,
  );
}

/** The pull name the backend builds, for the status poll. */
export function hfPullName(
  user: string,
  repo: string,
  quant?: string | null,
): string {
  return quant ? `hf.co/${user}/${repo}:${quant}` : `hf.co/${user}/${repo}`;
}

// server

export async function getServer(providerId: string): Promise<ServerResponse> {
  return request(`${base(providerId)}/server`);
}

export async function stopServer(
  providerId: string,
): Promise<ServerActionResponse> {
  return request(
    `${base(providerId)}/server/stop`,
    { method: "POST" },
    WRITE_TIMEOUT_MS,
  );
}

export async function testServer(
  providerId: string,
  baseUrl: string,
): Promise<ServerProbeResponse> {
  return request(
    `${base(providerId)}/server/test`,
    json("POST", { base_url: baseUrl }),
    WRITE_TIMEOUT_MS,
  );
}

export async function getServerLog(
  providerId: string,
  lines = 40,
): Promise<ServerLogResponse> {
  return request(`${base(providerId)}/server/log?lines=${lines}`);
}

export async function getEnvGuide(
  providerId: string,
  os?: EnvGuideOs,
): Promise<EnvGuideResponse> {
  const qs = os ? `?os=${os}` : "";
  return request(`${base(providerId)}/server/env-guide${qs}`);
}

// overview

/**
 * The whole Simple page in one round-trip. A backend that predates the route
 * (a CLI-only install that lags the UI bundle) answers 404: then the four
 * legacy reads are composed here so the page still paints — that fallback is
 * permanent, not a migration shim.
 */
export async function getOverview(
  providerId: string,
  fresh = false,
): Promise<OverviewResponse> {
  try {
    return await request<OverviewResponse>(
      `${base(providerId)}/overview${fresh ? "?fresh=1" : ""}`,
    );
  } catch (err) {
    if (!(err instanceof HttpError) || err.status !== 404) throw err;
  }
  const [server, roles, inventory, recommended] = await Promise.all([
    getServer(providerId),
    getRoles(providerId),
    getInventory(providerId),
    getCatalogRecommended(providerId),
  ]);
  return {
    server,
    roles,
    inventory,
    recommended,
    source: "live",
    fetched_at: Math.floor(Date.now() / 1000),
  };
}

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const localModelsKeys = {
  all: ["local-models"] as const,
  overview: (p: string) => ["local-models", p, "overview"] as const,
  inventory: (p: string) => ["local-models", p, "inventory"] as const,
  model: (p: string, name: string) =>
    ["local-models", p, "inventory", name] as const,
  roles: (p: string) => ["local-models", p, "roles"] as const,
  idleRelease: (p: string) => ["local-models", p, "idle-release"] as const,
  autostart: (p: string) => ["local-models", p, "autostart"] as const,
  options: (p: string, name: string) =>
    ["local-models", p, "options", name] as const,
  suggested: (p: string, name: string) =>
    ["local-models", p, "suggested-options", name] as const,
  catalog: (p: string, q: CatalogQuery) =>
    [
      "local-models",
      p,
      "catalog",
      q.q ?? "",
      q.sort ?? "popular",
      q.capability ?? "",
      q.limit ?? 50,
    ] as const,
  catalogTags: (p: string, name: string) =>
    ["local-models", p, "catalog", "tags", name] as const,
  recommended: (p: string) =>
    ["local-models", p, "catalog", "recommended"] as const,
  hfEnabled: (p: string) => ["local-models", p, "hf", "enabled"] as const,
  hfSearch: (p: string, q: string, sort: HfSort) =>
    ["local-models", p, "hf", "search", q, sort] as const,
  hfFiles: (p: string, user: string, repo: string) =>
    ["local-models", p, "hf", "files", user, repo] as const,
  server: (p: string) => ["local-models", p, "server"] as const,
  serverLog: (p: string, lines: number) =>
    ["local-models", p, "server", "log", lines] as const,
  envGuide: (p: string, os: EnvGuideOs | "") =>
    ["local-models", p, "server", "env-guide", os] as const,
};

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

/**
 * How long an unused section query stays in memory. A tab switch inside the
 * section, or leaving and coming back within half an hour, must reopen warm
 * instead of re-sweeping the server.
 */
export const SECTION_GC_MS = 30 * 60_000;

/** Options for the overview query — shared by `useOverview` and the idle prefetch. */
export function overviewQueryOptions(providerId: string) {
  return {
    queryKey: localModelsKeys.overview(providerId),
    queryFn: () => getOverview(providerId),
    staleTime: 5_000,
    gcTime: SECTION_GC_MS,
  };
}

/**
 * The Simple page's data, painted first from the on-disk snapshot and
 * refreshed at once. While the snapshot is on screen and the refresh runs,
 * `data.source === "cache"` and `isFetching` is true — the panel shows
 * "Checking…". Every fresh payload is written back to the snapshot and
 * seeds the legacy per-section queries so Catalogue / Models / Tune open warm.
 */
export function useOverview(providerId: string | undefined, enabled = true) {
  const qc = useQueryClient();
  const id = providerId ?? "";
  const snapshot = useMemo(
    () => (providerId ? readOverviewSnapshot(providerId) : null),
    [providerId],
  );
  const query = useQuery({
    ...overviewQueryOptions(id),
    enabled: enabled && !!providerId,
    initialData: snapshot ?? undefined,
    initialDataUpdatedAt: snapshot ? snapshot.fetched_at * 1000 : undefined,
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });

  const data = query.data;
  useEffect(() => {
    if (!providerId || !data) return;
    const updatedAt = data.fetched_at * 1000;
    qc.setQueryData(localModelsKeys.server(providerId), data.server, {
      updatedAt,
    });
    qc.setQueryData(localModelsKeys.roles(providerId), data.roles, {
      updatedAt,
    });
    qc.setQueryData(localModelsKeys.inventory(providerId), data.inventory, {
      updatedAt,
    });
    qc.setQueryData(
      localModelsKeys.recommended(providerId),
      data.recommended,
      { updatedAt },
    );
    // The snapshot we painted from is already on disk.
    if (data !== snapshot && data.source === "live")
      writeOverviewSnapshot(providerId, data);
  }, [data, providerId, qc, snapshot]);

  return query;
}

/** Every download with facts, loaded state and the roles using it. Polls slowly. */
export function useInventory(providerId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: localModelsKeys.inventory(providerId ?? ""),
    queryFn: () => getInventory(providerId as string),
    enabled: enabled && !!providerId,
    refetchInterval: 15_000,
    staleTime: 5_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useInventoryModel(
  providerId: string | undefined,
  name: string | undefined,
) {
  return useQuery({
    queryKey: localModelsKeys.model(providerId ?? "", name ?? ""),
    queryFn: () => getInventoryModel(providerId as string, name as string),
    enabled: !!providerId && !!name,
    staleTime: 30_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useRoles(providerId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: localModelsKeys.roles(providerId ?? ""),
    queryFn: () => getRoles(providerId as string),
    enabled: enabled && !!providerId,
    staleTime: 10_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useModelOptions(
  providerId: string | undefined,
  name: string | undefined,
) {
  return useQuery({
    queryKey: localModelsKeys.options(providerId ?? "", name ?? ""),
    queryFn: () => getModelOptions(providerId as string, name as string),
    enabled: !!providerId && !!name,
    staleTime: 30_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useSuggestedOptions(
  providerId: string | undefined,
  name: string | undefined,
) {
  return useQuery({
    queryKey: localModelsKeys.suggested(providerId ?? "", name ?? ""),
    queryFn: () => getSuggestedOptions(providerId as string, name as string),
    enabled: !!providerId && !!name,
    staleTime: 5 * 60_000,
    gcTime: SECTION_GC_MS,
  });
}

/** Browse-by-default: fetches on mount; the backend caches the page for 10 minutes. */
export function useCatalog(
  providerId: string | undefined,
  query: CatalogQuery = {},
  enabled = true,
) {
  return useQuery({
    queryKey: localModelsKeys.catalog(providerId ?? "", query),
    queryFn: () => searchCatalog(providerId as string, query),
    enabled: enabled && !!providerId,
    staleTime: 10 * 60_000,
    gcTime: SECTION_GC_MS,
    placeholderData: (prev) => prev,
  });
}

export function useCatalogTags(
  providerId: string | undefined,
  name: string | undefined,
) {
  return useQuery({
    queryKey: localModelsKeys.catalogTags(providerId ?? "", name ?? ""),
    queryFn: () => getCatalogTags(providerId as string, name as string),
    enabled: !!providerId && !!name,
    staleTime: 10 * 60_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useCatalogRecommended(
  providerId: string | undefined,
  enabled = true,
) {
  return useQuery({
    queryKey: localModelsKeys.recommended(providerId ?? ""),
    queryFn: () => getCatalogRecommended(providerId as string),
    enabled: enabled && !!providerId,
    staleTime: 60_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useHfEnabled(providerId: string | undefined) {
  return useQuery({
    queryKey: localModelsKeys.hfEnabled(providerId ?? ""),
    queryFn: () => getHfEnabled(providerId as string),
    enabled: !!providerId,
    staleTime: 60_000,
    gcTime: SECTION_GC_MS,
  });
}

/** Only runs while the switch is on AND a query is typed — no idle HF traffic. */
export function useHfSearch(
  providerId: string | undefined,
  q: string,
  sort: HfSort = "downloads",
  enabled = true,
) {
  return useQuery({
    queryKey: localModelsKeys.hfSearch(providerId ?? "", q, sort),
    queryFn: () => searchHf(providerId as string, q, sort),
    enabled: enabled && !!providerId && q.trim().length > 0,
    staleTime: 10 * 60_000,
    gcTime: SECTION_GC_MS,
    placeholderData: (prev) => prev,
  });
}

export function useHfFiles(
  providerId: string | undefined,
  user: string | undefined,
  repo: string | undefined,
) {
  return useQuery({
    queryKey: localModelsKeys.hfFiles(providerId ?? "", user ?? "", repo ?? ""),
    queryFn: () =>
      getHfFiles(providerId as string, user as string, repo as string),
    enabled: !!providerId && !!user && !!repo,
    staleTime: 10 * 60_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useServer(providerId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: localModelsKeys.server(providerId ?? ""),
    queryFn: () => getServer(providerId as string),
    enabled: enabled && !!providerId,
    refetchInterval: 15_000,
    staleTime: 5_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useServerLog(
  providerId: string | undefined,
  lines = 40,
  enabled = true,
) {
  return useQuery({
    queryKey: localModelsKeys.serverLog(providerId ?? "", lines),
    queryFn: () => getServerLog(providerId as string, lines),
    enabled: enabled && !!providerId,
    refetchInterval: 10_000,
    gcTime: SECTION_GC_MS,
  });
}

export function useEnvGuide(providerId: string | undefined, os?: EnvGuideOs) {
  return useQuery({
    queryKey: localModelsKeys.envGuide(providerId ?? "", os ?? ""),
    queryFn: () => getEnvGuide(providerId as string, os),
    enabled: !!providerId,
    staleTime: Infinity,
    gcTime: SECTION_GC_MS,
  });
}

// ---------------------------------------------------------------------------
// Mutation hooks — each one invalidates what its write changes
// ---------------------------------------------------------------------------

/** Invalidate every query of the section for one provider. */
/**
 * Refetch the overview LIVE after a change this section made itself.
 *
 * A plain invalidation would re-read `GET …/overview`, which answers the
 * disk snapshot first (`source: "cache"`) and refreshes in the background —
 * so the row the user just changed would sit on its old value until the
 * next poll. `?fresh=1` skips the cache; the answer replaces the cached
 * payload under the same key, so every panel repaints from it.
 */
export function refetchOverviewFresh(
  qc: QueryClient,
  providerId: string,
): Promise<OverviewResponse | undefined> {
  return qc
    .fetchQuery({
      queryKey: localModelsKeys.overview(providerId),
      queryFn: () => getOverview(providerId, true),
      staleTime: 0,
    })
    .catch((err: unknown) => {
      // The write already landed; a failed re-read only delays the repaint
      // until the next poll, so it is logged rather than surfaced.
      console.warn("[local-models] fresh overview failed", err);
      return undefined;
    });
}

export function useInvalidateLocalModels(providerId: string | undefined) {
  const qc = useQueryClient();
  return () => {
    const id = providerId ?? "";
    void refetchOverviewFresh(qc, id);
    return qc.invalidateQueries({
      queryKey: ["local-models", id],
      // The overview is being fetched fresh above; refetching it again
      // here would race that answer with a cache-first one.
      predicate: (q) =>
        !(q.queryKey.length === 3 && q.queryKey[2] === "overview"),
    });
  };
}

export function useSetRole(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ role, model }: { role: LocalModelRole; model: string }) =>
      setRole(providerId as string, role, model),
    onSuccess: () => {
      const id = providerId ?? "";
      void qc.invalidateQueries({ queryKey: localModelsKeys.roles(id) });
      void qc.invalidateQueries({ queryKey: localModelsKeys.inventory(id) });
      void refetchOverviewFresh(qc, id);
    },
  });
}

export function useSaveModelOptions(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      options,
    }: {
      name: string;
      options: OllamaModelOptions;
    }) => saveModelOptions(providerId as string, name, options),
    onSuccess: (_data, { name }) => {
      void qc.invalidateQueries({
        queryKey: localModelsKeys.options(providerId ?? "", name),
      });
    },
  });
}

export function useResetModelOptions(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => resetModelOptions(providerId as string, name),
    onSuccess: (_data, name) => {
      void qc.invalidateQueries({
        queryKey: localModelsKeys.options(providerId ?? "", name),
      });
    },
  });
}

export function useUnloadModel(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unloadModel(providerId as string, name),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: localModelsKeys.inventory(providerId ?? ""),
      });
      void qc.invalidateQueries({
        queryKey: localModelsKeys.server(providerId ?? ""),
      });
      void qc.invalidateQueries({
        queryKey: localModelsKeys.overview(providerId ?? ""),
      });
    },
  });
}

export function useDeleteModel(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, reassign }: { name: string; reassign?: string }) =>
      deleteModel(providerId as string, name, reassign),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["local-models", providerId ?? ""],
      });
    },
  });
}

export function useSetHfEnabled(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      setHfEnabled(providerId as string, enabled),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: localModelsKeys.hfEnabled(providerId ?? ""),
      });
    },
  });
}

export function useStartHfPull(providerId: string | undefined) {
  return useMutation({
    mutationFn: (body: HfPullBody) => startHfPull(providerId as string, body),
  });
}

export function useStopServer(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => stopServer(providerId as string),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: localModelsKeys.server(providerId ?? ""),
      });
      void qc.invalidateQueries({
        queryKey: localModelsKeys.inventory(providerId ?? ""),
      });
      void qc.invalidateQueries({
        queryKey: localModelsKeys.overview(providerId ?? ""),
      });
    },
  });
}

export function useIdleRelease(providerId: string | undefined) {
  return useQuery({
    queryKey: localModelsKeys.idleRelease(providerId ?? ""),
    queryFn: () => getIdleRelease(providerId as string),
    enabled: !!providerId,
    gcTime: SECTION_GC_MS,
  });
}

export function useSetIdleRelease(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (minutes: number) => setIdleRelease(providerId as string, minutes),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: localModelsKeys.idleRelease(providerId ?? ""),
      });
    },
  });
}

export function useAutostart(providerId: string | undefined) {
  return useQuery({
    queryKey: localModelsKeys.autostart(providerId ?? ""),
    queryFn: () => getAutostart(providerId as string),
    enabled: !!providerId,
    gcTime: SECTION_GC_MS,
  });
}

export function useSetAutostart(providerId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      setAutostart(providerId as string, enabled),
    onSuccess: (data) => {
      qc.setQueryData(localModelsKeys.autostart(providerId ?? ""), data);
    },
  });
}

/** The on-demand proof; the sidebar badge follows the health record it writes. */
export function useVerifySetup(providerId: string | undefined) {
  return useMutation({
    mutationFn: () => verifySetup(providerId as string),
  });
}

export function useTestServer(providerId: string | undefined) {
  return useMutation({
    mutationFn: (baseUrl: string) => testServer(providerId as string, baseUrl),
  });
}
