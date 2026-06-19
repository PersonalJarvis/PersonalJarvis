import { useMutation, useQuery } from "@tanstack/react-query";

// ----------------------------------------------------------------------
// Types — analog jarvis/docs/schema.py DocFrontmatter
// ----------------------------------------------------------------------

export type DocDiataxis =
  | "tutorial"
  | "howto"
  | "reference"
  | "explanation"
  | "troubleshooting"
  | "adr"
  | "unclassified";

export type DocStatus = "draft" | "active" | "deprecated";

export type DocAudience = "developer" | "operator" | "end-user";

export interface DocSummary {
  title: string;
  slug: string;
  diataxis: DocDiataxis;
  status: DocStatus;
  owner: string;
  last_reviewed: string | null;
  phase: string;
  audience: DocAudience;
  tags: string[];
  related: string[];
  deprecates: string | null;
  deprecated_by: string | null;
  next_review_due: string | null;
  version_min: string | null;
  path: string;
  body_hash: string;
  error: string | null;
  heading_count: number;
}

export interface DocHeading {
  level: number;
  text: string;
  slug: string;
}

export interface DocDetail extends DocSummary {
  body: string;
  headings: DocHeading[];
}

export interface DocSearchResult {
  slug: string;
  title: string;
  diataxis: DocDiataxis;
  phase: string;
  snippet: string;
  score: number;
}

// Reihenfolge fuer die Sidebar — analog backend ``/api/docs/grouped``.
export const DIATAXIS_ORDER: DocDiataxis[] = [
  "tutorial",
  "howto",
  "explanation",
  "reference",
  "troubleshooting",
  "adr",
  "unclassified",
];

export const DIATAXIS_LABELS: Record<DocDiataxis, string> = {
  tutorial: "Tutorials",
  howto: "How-Tos",
  explanation: "Concepts",
  reference: "References",
  troubleshooting: "Troubleshooting",
  adr: "ADRs",
  unclassified: "Unclassified",
};

// ----------------------------------------------------------------------
// Fetchers
// ----------------------------------------------------------------------

async function fetchDocsGrouped(): Promise<Record<DocDiataxis, DocSummary[]>> {
  const res = await fetch("/api/docs/grouped");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchDocsList(): Promise<DocSummary[]> {
  const res = await fetch("/api/docs");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchDocDetail(slug: string): Promise<DocDetail> {
  const res = await fetch(`/api/docs/${encodeURIComponent(slug)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function openDocInEditor(slug: string): Promise<{ path: string }> {
  const res = await fetch(`/api/docs/${encodeURIComponent(slug)}/open`, {
    method: "POST",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function fetchDocSearch(
  q: string,
  diataxis?: DocDiataxis,
  limit = 20,
): Promise<DocSearchResult[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  if (diataxis) params.set("diataxis", diataxis);
  const res = await fetch(`/api/docs/search?${params}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ----------------------------------------------------------------------
// Hooks
// ----------------------------------------------------------------------

export function useDocsGrouped() {
  return useQuery({
    queryKey: ["docs", "grouped"],
    queryFn: fetchDocsGrouped,
    staleTime: 30_000,
  });
}

export function useDocsList() {
  return useQuery({
    queryKey: ["docs", "list"],
    queryFn: fetchDocsList,
    staleTime: 30_000,
  });
}

export function useDocDetail(slug: string | null) {
  return useQuery({
    queryKey: ["docs", "detail", slug],
    queryFn: () => fetchDocDetail(slug!),
    enabled: slug !== null,
    staleTime: 60_000,
  });
}

export function useOpenDocInEditor() {
  return useMutation({
    mutationFn: openDocInEditor,
  });
}

export function useDocSearch(
  q: string,
  diataxis?: DocDiataxis,
  enabled = true,
) {
  return useQuery({
    queryKey: ["docs", "search", q, diataxis],
    queryFn: () => fetchDocSearch(q, diataxis),
    enabled: enabled && q.trim().length > 0,
    staleTime: 5_000,
  });
}
