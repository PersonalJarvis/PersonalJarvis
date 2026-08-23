/**
 * React Query hooks for the Spend & Tokens section.
 *
 * Endpoints: see `jarvis/ui/web/costs_routes.py`. The backend is a read model
 * over the databases the app already writes, so every query here is a plain
 * GET — nothing in this section mutates state.
 */
import { useQuery } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Wire types — mirror the Pydantic models in costs_routes.py
// ---------------------------------------------------------------------------

/** Which model in a turn spent this. */
export type CostRole = "realtime" | "tool" | "pipeline" | "agent" | "worker";

/** Which part of the app it was spent in. */
export type CostSurface = "voice" | "agent-chat" | "mission";

/**
 * How confident the price is.
 * - `recorded` the source priced the call itself (audio rates included)
 * - `derived`  re-priced here from the rate tables
 * - `free`     local engine, subscription seat, or a `:free` model
 * - `unknown`  tokens spent at a rate nobody publishes — an accounting gap
 */
export type PriceSource = "recorded" | "derived" | "free" | "unknown";

export interface CostBucket {
  key: string;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  tokens_cached: number;
  tokens_total: number;
  entries: number;
  gap_tokens: number;
  last_ts_ms: number;
  cost_share: number;
  token_share: number;
  members: string[];
  /**
   * Cost per member of the bucket's second dimension: a day broken down by
   * role, a provider by model. Drives the stacked chart and the tooltips.
   */
  breakdown: Record<string, number>;
}

export interface CostRefBucket extends CostBucket {
  label: string;
  surface: string;
}

export interface CostTotals {
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  tokens_cached: number;
  tokens_total: number;
  entries: number;
  gap_tokens: number;
  gap_entries: number;
  free_tokens: number;
  estimated_usd: number;
  first_ts_ms: number;
  last_ts_ms: number;
}

export interface CostModelRow {
  model: string;
  provider: string;
  price_sources: PriceSource[];
  tokens_total: number;
}

export interface CostFacets {
  providers: string[];
  models: string[];
  roles: CostRole[];
  surfaces: CostSurface[];
}

export interface CostCurrency {
  eur_per_usd: number;
  source: "config" | "default";
}

export interface CostSummary {
  since_ms: number;
  until_ms: number;
  bucket: "day" | "hour";
  totals: CostTotals;
  by_provider: CostBucket[];
  by_model: CostBucket[];
  by_role: CostBucket[];
  by_surface: CostBucket[];
  series: CostBucket[];
  top_refs: CostRefBucket[];
  models: CostModelRow[];
  facets: CostFacets;
  currency: CostCurrency;
  sources_present: string[];
}

export interface CostEntryRow {
  ts_ms: number;
  surface: CostSurface;
  role: CostRole;
  provider: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  tokens_cached: number;
  tokens_total: number;
  cost_usd: number;
  price_source: PriceSource;
  ref_id: string;
  label: string;
}

export interface CostEntriesPage {
  items: CostEntryRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface CostRateRow {
  model: string;
  input_usd_per_mtok: number | null;
  output_usd_per_mtok: number | null;
  audio_input_usd_per_mtok: number | null;
  audio_output_usd_per_mtok: number | null;
  known: boolean;
}

export interface CostPricing {
  rates: CostRateRow[];
  currency: CostCurrency;
}

// ---------------------------------------------------------------------------
// Query state shared by the summary and the line items
// ---------------------------------------------------------------------------

export interface CostFilters {
  /** Rolling window in days; `0` means everything ever recorded. */
  days: number;
  providers: string[];
  models: string[];
  roles: CostRole[];
  surfaces: CostSurface[];
  search: string;
}

export const EMPTY_FILTERS: CostFilters = {
  days: 30,
  providers: [],
  models: [],
  roles: [],
  surfaces: [],
  search: "",
};

/** Are any filters beyond the time window active? */
export function hasActiveFilters(f: CostFilters): boolean {
  return (
    f.providers.length > 0 ||
    f.models.length > 0 ||
    f.roles.length > 0 ||
    f.surfaces.length > 0 ||
    f.search.trim().length > 0
  );
}

function toParams(f: CostFilters): URLSearchParams {
  const params = new URLSearchParams();
  params.set("days", String(f.days));
  // Repeated params rather than one comma-joined value: a model id may
  // legitimately contain a comma-free but slash-heavy vendor prefix, and
  // repeating keeps the split unambiguous on the backend.
  for (const p of f.providers) params.append("provider", p);
  for (const m of f.models) params.append("model", m);
  for (const r of f.roles) params.append("role", r);
  for (const s of f.surfaces) params.append("surface", s);
  if (f.search.trim()) params.set("search", f.search.trim());
  return params;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useCostSummary(filters: CostFilters) {
  const params = toParams(filters);
  return useQuery({
    queryKey: ["costs", "summary", params.toString()],
    queryFn: () => getJson<CostSummary>(`/api/costs/summary?${params.toString()}`),
    // Spend only moves when a turn finishes; a slow poll keeps an open
    // section current without re-reading three SQLite files every second.
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}

export function useCostEntries(
  filters: CostFilters,
  sort: "recent" | "cost" | "tokens",
  limit: number,
  offset: number,
) {
  const params = toParams(filters);
  params.set("sort", sort);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return useQuery({
    queryKey: ["costs", "entries", params.toString()],
    queryFn: () => getJson<CostEntriesPage>(`/api/costs/entries?${params.toString()}`),
    staleTime: 10_000,
    placeholderData: (prev) => prev,
  });
}

export function useCostPricing(days: number, enabled: boolean) {
  return useQuery({
    queryKey: ["costs", "pricing", days],
    queryFn: () => getJson<CostPricing>(`/api/costs/pricing?days=${days}`),
    enabled,
    staleTime: 5 * 60_000,
  });
}
