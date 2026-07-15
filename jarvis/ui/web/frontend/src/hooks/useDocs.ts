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
  summary: string;
  section: string;
  section_order: number;
  order: number;
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

export interface DocNavSummary {
  title: string;
  slug: string;
  diataxis: DocDiataxis;
  summary: string;
  section: string;
  section_order: number;
  order: number;
  tags: string[];
  related: string[];
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
  summary: string;
  section: string;
  snippet: string;
  score: number;
}

// Order for the sidebar — mirrors backend ``/api/docs/grouped``.
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

export interface DocSection {
  name: string;
  order: number;
  docs: DocNavSummary[];
}

/** Build the reader navigation from compact API metadata. */
export function buildDocSections(
  grouped: Partial<Record<DocDiataxis, DocNavSummary[]>> | undefined,
): DocSection[] {
  const bySection = new Map<string, DocSection>();
  for (const kind of DIATAXIS_ORDER) {
    for (const doc of grouped?.[kind] ?? []) {
      const name = doc.section || "Other";
      const existing = bySection.get(name);
      if (existing) {
        existing.order = Math.min(existing.order, doc.section_order);
        existing.docs.push(doc);
      } else {
        bySection.set(name, {
          name,
          order: doc.section_order,
          docs: [doc],
        });
      }
    }
  }
  const sections = [...bySection.values()].map((section) => ({
    ...section,
    docs: [...section.docs].sort(
      (a, b) => a.order - b.order || a.title.localeCompare(b.title),
    ),
  }));
  return sections.sort(
    (a, b) => a.order - b.order || a.name.localeCompare(b.name),
  );
}

// ----------------------------------------------------------------------
// Fetchers
// ----------------------------------------------------------------------

const DOC_REQUEST_TIMEOUT_MS = 8_000;

async function fetchDocsJson<T>(url: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    DOC_REQUEST_TIMEOUT_MS,
  );
  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    const body = (await res.json().catch(() => null)) as unknown;
    if (!res.ok) {
      const detail =
        body && typeof body === "object" && "detail" in body
          ? String((body as { detail: unknown }).detail)
          : `HTTP ${res.status}`;
      throw new Error(detail);
    }
    return body as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("Documentation request timed out");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function fetchDocsGrouped(): Promise<Partial<Record<DocDiataxis, DocNavSummary[]>>> {
  return fetchDocsJson("/api/docs/grouped?compact=true");
}

async function fetchDocsList(): Promise<DocSummary[]> {
  return fetchDocsJson("/api/docs");
}

async function fetchDocDetail(slug: string): Promise<DocDetail> {
  return fetchDocsJson(`/api/docs/${encodeURIComponent(slug)}`);
}

async function openDocInEditor(slug: string): Promise<{ path: string }> {
  return fetchDocsJson(`/api/docs/${encodeURIComponent(slug)}/open`, {
    method: "POST",
  });
}

async function fetchDocSearch(
  q: string,
  diataxis?: DocDiataxis,
  limit = 20,
): Promise<DocSearchResult[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  if (diataxis) params.set("diataxis", diataxis);
  return fetchDocsJson(`/api/docs/search?${params}`);
}

// ----------------------------------------------------------------------
// Hooks
// ----------------------------------------------------------------------

export function useDocsGrouped() {
  return useQuery({
    queryKey: ["docs", "grouped"],
    queryFn: fetchDocsGrouped,
    staleTime: 5 * 60_000,
    retry: false,
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
    retry: false,
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
    retry: false,
  });
}
