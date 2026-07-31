/**
 * Typed fetchers for the UltraWiki Explore surface
 * (`jarvis/ui/web/ultrawiki_explore_routes.py`).
 *
 * Explore is the readable view over the store: entity pages, moment pages,
 * and the graph over them, all served from the derived projection. Every
 * answer carries the corpus counts and a named reason, so an empty view can
 * say WHY it is empty instead of showing a blank canvas.
 *
 * Like the rest of the UltraWiki surface, 409 means "Ultra mode is off" (the
 * app is healthy, the normal wiki answers) and 503 means the service is not
 * wired yet. Both arrive as an {@link UltraWikiApiError}.
 */

import { UltraWikiApiError } from "@/lib/ultrawikiApi";

// Mirrors jarvis/ultrawiki/types.py::ExploreReason — five-layer anti-drift
// discipline (AP-4 / BUG-008). Parity-tested in
// tests/unit/ultrawiki/test_explore_reason_parity.py; never retype elsewhere.
export const ULTRAWIKI_EXPLORE_REASONS = [
  "ok",
  "no_sources",
  "nothing_imported",
  "nothing_distilled",
  "no_entities",
] as const;
export type UltraWikiExploreReason = (typeof ULTRAWIKI_EXPLORE_REASONS)[number];

/** How much the knowledge base holds — what turns "empty" into a diagnosis. */
export interface UltraWikiCorpusCounts {
  sources: number;
  items: number;
  distilled: number;
}

/** One neighbour of an entity, with the label a human should see. */
export interface UltraWikiEntityNeighbor {
  key: string;
  label: string;
  shared: number;
}

/** One browsable entity page. */
export interface UltraWikiEntity {
  key: string;
  label: string;
  mentions: number;
  first_seen: string;
  last_seen: string;
  neighbors: UltraWikiEntityNeighbor[];
}

/** One distilled moment — titled by the question it answers. */
export interface UltraWikiMoment {
  document_id: number;
  item_id: number;
  title: string;
  summary: string;
  resolution: string;
  entity_keys: string[];
  timestamp_utc: string;
  month: string;
  source_id: string;
  source_label: string;
  permalink: string;
}

export interface UltraWikiEntitiesPage {
  ok: boolean;
  entities: UltraWikiEntity[];
  total: number;
  corpus: UltraWikiCorpusCounts;
  reason: UltraWikiExploreReason;
}

export interface UltraWikiEntityDetail {
  ok: boolean;
  entity: UltraWikiEntity;
  moments: UltraWikiMoment[];
  total: number;
  corpus: UltraWikiCorpusCounts;
}

export interface UltraWikiMomentsPage {
  ok: boolean;
  moments: UltraWikiMoment[];
  total: number;
  corpus: UltraWikiCorpusCounts;
  reason: UltraWikiExploreReason;
}

export interface UltraWikiGraphNode {
  key: string;
  label: string;
  mentions: number;
  first_seen: string;
  last_seen: string;
}

export interface UltraWikiGraphEdge {
  source: string;
  target: string;
  weight: number;
}

export interface UltraWikiGraphPayload {
  ok: boolean;
  nodes: UltraWikiGraphNode[];
  edges: UltraWikiGraphEdge[];
  min_mentions: number;
  total_entities: number;
  corpus: UltraWikiCorpusCounts;
  reason: UltraWikiExploreReason;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    let detail: unknown = null;
    try {
      const body = await response.json();
      detail = (body as { detail?: unknown })?.detail ?? body;
    } catch {
      detail = null;
    }
    throw new UltraWikiApiError(
      `HTTP ${response.status}`,
      response.status,
      detail,
    );
  }
  return (await response.json()) as T;
}

export async function fetchExploreEntities(
  options: { q?: string; limit?: number } = {},
): Promise<UltraWikiEntitiesPage> {
  const params = new URLSearchParams();
  if (options.q) params.set("q", options.q);
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  return getJson<UltraWikiEntitiesPage>(
    `/api/ultrawiki/explore/entities${query ? `?${query}` : ""}`,
  );
}

export async function fetchExploreEntity(
  key: string,
): Promise<UltraWikiEntityDetail> {
  return getJson<UltraWikiEntityDetail>(
    `/api/ultrawiki/explore/entities/${encodeURIComponent(key)}`,
  );
}

export async function fetchExploreMoments(
  options: { entity?: string; month?: string; limit?: number } = {},
): Promise<UltraWikiMomentsPage> {
  const params = new URLSearchParams();
  if (options.entity) params.set("entity", options.entity);
  if (options.month) params.set("month", options.month);
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  return getJson<UltraWikiMomentsPage>(
    `/api/ultrawiki/explore/moments${query ? `?${query}` : ""}`,
  );
}

export async function fetchExploreGraph(
  minMentions: number,
): Promise<UltraWikiGraphPayload> {
  return getJson<UltraWikiGraphPayload>(
    `/api/ultrawiki/explore/graph?min_mentions=${minMentions}`,
  );
}

/**
 * The i18n key explaining an empty Explore view.
 *
 * Kept next to the type so a new reason cannot be added without a matching
 * message — a blank screen with no explanation is the exact failure this
 * whole reason mechanism exists to prevent.
 */
export function exploreReasonKey(reason: UltraWikiExploreReason): string {
  return `ultrawiki.explore.empty.${reason}`;
}

/** What Obsidian knows about the vault on this machine. */
export interface UltraWikiObsidianState {
  installed: boolean;
  registered: boolean;
  config_path: string;
  error: string;
}

export interface UltraWikiVaultStatus {
  ok: boolean;
  path: string;
  exists: boolean;
  notes: number;
  last_export_at: string;
  obsidian: UltraWikiObsidianState;
}

export interface UltraWikiVaultExport {
  ok: boolean;
  path: string;
  topics: number;
  moments: number;
  written: number;
  unchanged: number;
  removed: number;
}

export interface UltraWikiVaultRegistration {
  ok: boolean;
  status: string;
  path: string;
  error: string;
}

async function postJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { method: "POST" });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = String((body as { detail?: unknown })?.detail ?? "");
    } catch {
      detail = "";
    }
    throw new UltraWikiApiError(
      detail || `HTTP ${response.status}`,
      response.status,
      detail,
    );
  }
  return (await response.json()) as T;
}

export async function fetchVaultStatus(): Promise<UltraWikiVaultStatus> {
  return getJson<UltraWikiVaultStatus>("/api/ultrawiki/vault/status");
}

export async function exportVault(): Promise<UltraWikiVaultExport> {
  return postJson<UltraWikiVaultExport>("/api/ultrawiki/vault/export");
}

export async function registerVaultWithObsidian(): Promise<UltraWikiVaultRegistration> {
  return postJson<UltraWikiVaultRegistration>("/api/ultrawiki/vault/register");
}
