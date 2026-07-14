/**
 * Typed fetchers for the wiki REST API exposed by Agent A.
 *
 * Contract is defined in docs/plans/b3/00-OVERVIEW.md §3.1 — any deviation
 * here would break the B/A merge. All endpoints return HTTP 200 even on
 * logical errors; clients read `ok`.
 */

export type WikiKind = "entity" | "concept" | "project" | "session";

export interface WikiTreeFile {
  slug: string;
  title: string;
  mtime: number;
  size: number;
}

export interface WikiTreeFolder {
  name: string;
  kind: WikiKind | string;
  count: number;
  files: WikiTreeFile[];
}

export interface WikiTreeStats {
  total_pages: number;
  total_links: number;
  last_curator_run: string | null;
}

export interface WikiTreeResponse {
  ok: boolean;
  vault_root?: string;
  folders?: WikiTreeFolder[];
  stats?: WikiTreeStats;
  error?: string;
}

export interface WikiPageStats {
  words: number;
  bytes: number;
  mtime: number;
}

export interface WikiPageResponse {
  ok: boolean;
  slug?: string;
  kind?: WikiKind;
  title?: string;
  path?: string;
  frontmatter?: Record<string, string | string[]>;
  body_md?: string;
  wikilinks?: string[];
  stats?: WikiPageStats;
  error?: string;
}

export interface WikiGraphNode {
  id: string;
  kind: WikiKind | string;
  title: string;
}

export interface WikiGraphEdge {
  source: string;
  target: string;
  context: string;
}

export interface WikiGraphResponse {
  ok: boolean;
  nodes?: WikiGraphNode[];
  edges?: WikiGraphEdge[];
  broken?: Array<{ source: string; target: string }>;
  error?: string;
}

export interface WikiBacklink {
  slug: string;
  title: string;
  snippet: string;
}

export interface WikiBacklinksResponse {
  ok: boolean;
  slug?: string;
  backlinks?: WikiBacklink[];
  error?: string;
}

export interface WikiSearchHit {
  slug: string;
  title: string;
  path: string;
  snippet: string;
  score: number;
}

export interface WikiSearchResponse {
  ok: boolean;
  query?: string;
  hits?: WikiSearchHit[];
  error?: string;
}

export interface WikiHealthLastWrite {
  ts: number;
  ok: boolean;
  pages: string[];
  error: string | null;
  source: string;
}

export interface WikiHealthLastChainFailure {
  ts: number;
  detail: string;
}

export interface WikiHealthSnapshot {
  bootstrap_ok: boolean | null;
  bootstrap_error: string | null;
  vault_root: string | null;
  vault_root_source: string | null;
  vault_legacy_conflict: boolean;
  last_write: WikiHealthLastWrite | null;
  last_chain_failure: WikiHealthLastChainFailure | null;
  journal_backlog: number;
  indexed_pages: number;
  vault_pages: number;
  index_state: "ok" | "stale";
}

export interface WikiReindexResponse {
  ok: boolean;
  indexed_pages?: number;
  vault_pages?: number;
  error?: string;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    // HTTP 500 / 404 only — logical errors come back with ok=false at 200
    throw new Error(`HTTP ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export async function fetchWikiTree(): Promise<WikiTreeResponse> {
  return getJson<WikiTreeResponse>("/api/wiki/tree");
}

export async function fetchWikiPage(slug: string): Promise<WikiPageResponse> {
  return getJson<WikiPageResponse>(`/api/wiki/page/${encodeURIComponent(slug)}`);
}

export async function fetchWikiGraph(): Promise<WikiGraphResponse> {
  return getJson<WikiGraphResponse>("/api/wiki/graph");
}

export async function fetchWikiBacklinks(slug: string): Promise<WikiBacklinksResponse> {
  return getJson<WikiBacklinksResponse>(
    `/api/wiki/backlinks/${encodeURIComponent(slug)}`,
  );
}

export async function fetchWikiSearch(
  query: string,
  k = 10,
): Promise<WikiSearchResponse> {
  const params = new URLSearchParams({ q: query, k: String(k) });
  return getJson<WikiSearchResponse>(`/api/wiki/search?${params.toString()}`);
}

/**
 * Fetch the wiki subsystem's own health snapshot (bootstrap state, last
 * write outcome, journal backlog). Unlike the other fetchers here, this one
 * swallows both HTTP-level and network-level failures and returns `null`
 * instead of throwing — it is polled on a timer by `WikiHealthStrip`
 * (`WikiView.tsx`), and a transient network blip during polling must not
 * surface as an unhandled rejection. Callers treat `null` as "unknown", the
 * same way the status strip treats a not-yet-resolved health check.
 */
export async function fetchWikiHealth(): Promise<WikiHealthSnapshot | null> {
  try {
    const res = await fetch("/api/wiki/health");
    if (!res.ok) return null;
    const body = (await res.json()) as {
      ok: boolean;
      health?: WikiHealthSnapshot;
    };
    return body.ok && body.health ? body.health : null;
  } catch {
    return null;
  }
}

export async function rebuildWikiIndex(): Promise<WikiReindexResponse> {
  const res = await fetch("/api/wiki/reindex", { method: "POST" });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as WikiReindexResponse;
}
