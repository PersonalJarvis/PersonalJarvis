import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type SkillState = "draft" | "validated" | "active" | "disabled";

export interface SkillTrigger {
  type: "voice" | "hotkey" | "schedule";
  pattern?: string | null;
  combo?: string | null;
  cron?: string | null;
  language?: string[];
}

export type ResourceKind = "references" | "scripts" | "assets" | "agents";

export const RESOURCE_KINDS: ResourceKind[] = [
  "references",
  "scripts",
  "assets",
  "agents",
];

export const RESOURCE_LABELS: Record<ResourceKind, string> = {
  references: "References",
  scripts: "Scripts",
  assets: "Assets",
  agents: "Agents",
};

export interface SkillSummary {
  name: string;
  state: SkillState;
  is_builtin: boolean;
  error: string | null;
  description: string;
  category: string;
  version: string;
  triggers: SkillTrigger[];
  tags: string[];
  resources: Record<ResourceKind, string[]>;
  resource_count: number;
}

export interface SkillDetail extends SkillSummary {
  body: string;
  body_hash: string;
  path: string;
  frontmatter: Record<string, unknown> | null;
}

interface SkillsListResponse {
  skills: SkillSummary[];
  total: number;
}

async function fetchSkills(): Promise<SkillsListResponse> {
  const res = await fetch("/api/skills");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchSkill(name: string): Promise<SkillDetail> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function saveSkill(
  name: string,
  content: string,
  adminPassword?: string,
): Promise<SkillDetail> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, admin_password: adminPassword ?? null }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function setSkillEnabled(name: string, enabled: boolean): Promise<SkillSummary> {
  const action = enabled ? "enable" : "disable";
  const res = await fetch(
    `/api/skills/${encodeURIComponent(name)}/${action}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function reloadSkills(): Promise<{ ok: boolean; total: number }> {
  const res = await fetch("/api/skills/reload", { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchSkillResource(
  name: string,
  kind: ResourceKind,
  filename: string,
): Promise<string> {
  const url = `/api/skills/${encodeURIComponent(name)}/resources/${kind}/${filename
    .split("/")
    .map(encodeURIComponent)
    .join("/")}`;
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.text();
}

export function useSkillResource(
  name: string | null,
  kind: ResourceKind | null,
  filename: string | null,
) {
  return useQuery({
    queryKey: ["skill-resource", name, kind, filename],
    queryFn: () => fetchSkillResource(name!, kind!, filename!),
    enabled: !!name && !!kind && !!filename,
  });
}

export function useSkillsList() {
  return useQuery({
    queryKey: ["skills"],
    queryFn: fetchSkills,
  });
}

export function useSkillDetail(name: string | null) {
  return useQuery({
    queryKey: ["skill", name],
    queryFn: () => fetchSkill(name!),
    enabled: !!name,
  });
}

export function useSaveSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      content,
      adminPassword,
    }: {
      name: string;
      content: string;
      adminPassword?: string;
    }) => saveSkill(name, content, adminPassword),
    onSuccess: (_detail, vars) => {
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.invalidateQueries({ queryKey: ["skill", vars.name] });
    },
  });
}

export function useSetSkillEnabled() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      setSkillEnabled(name, enabled),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.invalidateQueries({ queryKey: ["skill", vars.name] });
    },
  });
}

export function useReloadSkills() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: reloadSkills,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

async function deleteSkill(
  name: string,
): Promise<{ ok: boolean; removed: boolean }> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function reorderSkills(
  order: string[],
): Promise<{ ok: boolean; order: string[] }> {
  const res = await fetch("/api/skills/order", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export function useDeleteSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteSkill,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

export interface BulkDeleteResult {
  deleted: string[];
  failed: { name: string; detail: string }[];
}

async function bulkDeleteSkills(names: string[]): Promise<BulkDeleteResult> {
  const res = await fetch("/api/skills/bulk-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Delete several user skills in one request. The backend deletes each name
 * independently (built-ins / unknown names land in ``failed``), so a single bad
 * entry never blocks the rest of the batch.
 */
export function useBulkDeleteSkills() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: bulkDeleteSkills,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

/**
 * Persist the user's custom skill order (list view only). The order is applied
 * server-side, so it survives a restart and follows the user across devices.
 */
export function useReorderSkills() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: reorderSkills,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

// ----------------------------------------------------------------------
// Skill creation (user-authored, via the desktop app)
// ----------------------------------------------------------------------

export interface SkillCreatePayload {
  name: string;
  description?: string;
  category?: string;
  tags?: string[];
  triggers?: SkillTrigger[];
  risk_policy?: Record<string, unknown>;
  body?: string;
  homepage_url?: string | null;
  source_url?: string | null;
  docs_url?: string | null;
  author?: string;
}

async function createSkill(payload: SkillCreatePayload): Promise<SkillDetail> {
  const res = await fetch("/api/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export function useCreateSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createSkill,
    onSuccess: (detail) => {
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.setQueryData(["skill", detail.name], detail);
    },
  });
}

// ----------------------------------------------------------------------
// AI Skill Creator
// ----------------------------------------------------------------------

export interface SkillCreatorDraft {
  name: string;
  description: string;
  category: string;
  tags: string[];
  triggers: SkillTrigger[];
  requires_tools: string[];
  risk_policy: Record<string, unknown>;
  body: string;
  questions: string[];
  assumptions: string[];
  test_prompts: string[];
  frontmatter?: Record<string, unknown> | null;
}

export interface SkillCreatorValidation {
  ok: boolean;
  state: SkillState;
  errors: string[];
  warnings: string[];
  parse_error?: string | null;
}

export interface SkillCreatorDraftPayload {
  intent: string;
  name_hint?: string;
  category?: string;
  trigger_hint?: string;
  extra_context?: string;
}

export interface SkillCreatorRefinePayload extends SkillCreatorDraftPayload {
  draft: SkillCreatorDraft;
  feedback: string;
}

export interface SkillCreatorDraftResponse {
  draft: SkillCreatorDraft;
  skill_md: string;
  validation: SkillCreatorValidation;
  brain_used: boolean;
}

async function draftSkill(
  payload: SkillCreatorDraftPayload,
): Promise<SkillCreatorDraftResponse> {
  const res = await fetch("/api/skills/creator/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function refineSkillDraft(
  payload: SkillCreatorRefinePayload,
): Promise<SkillCreatorDraftResponse> {
  const res = await fetch("/api/skills/creator/refine", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function validateSkillDraft(payload: {
  draft?: SkillCreatorDraft;
  skill_md?: string;
}): Promise<{
  skill_md: string;
  validation: SkillCreatorValidation;
  frontmatter: Record<string, unknown> | null;
}> {
  const res = await fetch("/api/skills/creator/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function commitSkillDraft(draft: SkillCreatorDraft): Promise<SkillDetail> {
  const res = await fetch("/api/skills/creator/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ draft }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export function useDraftSkill() {
  return useMutation({ mutationFn: draftSkill });
}

export function useRefineSkillDraft() {
  return useMutation({ mutationFn: refineSkillDraft });
}

export function useValidateSkillDraft() {
  return useMutation({ mutationFn: validateSkillDraft });
}

export function useCommitSkillDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: commitSkillDraft,
    onSuccess: (detail) => {
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.setQueryData(["skill", detail.name], detail);
    },
  });
}

async function importSkill(input: string): Promise<SkillDetail> {
  const res = await fetch("/api/skills/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export function useImportSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: importSkill,
    onSuccess: (detail) => {
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.setQueryData(["skill", detail.name], detail);
    },
  });
}

// ----------------------------------------------------------------------
// Lokale Skill-Suche (BM25 + optionales LLM-Re-Ranking)
// ----------------------------------------------------------------------

export interface LocalSkillQueryFilters {
  q: string;
  category?: string | null;
  state?: SkillState | null;
  risk?: RiskFilter | null;
  is_builtin?: boolean | null;
  tags?: string[];
  limit?: number;
}

export interface LocalSkillHit extends SkillSummary {
  score: number;
  reason: string;
}

export interface LocalSkillQueryResponse {
  skills: LocalSkillHit[];
  total: number;
  brain_used: boolean;
  query: string;
}

async function queryLocalSkills(
  filters: LocalSkillQueryFilters,
): Promise<LocalSkillQueryResponse> {
  const res = await fetch("/api/skills/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      q: filters.q,
      category: filters.category ?? null,
      state: filters.state ?? null,
      risk: filters.risk ?? null,
      is_builtin: filters.is_builtin ?? null,
      tags: filters.tags ?? [],
      limit: filters.limit ?? 30,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * React Query hook for the local skill search. Triggered on every filter
 * change. The hook does **not** run when the query and all filters are
 * empty — the sidebar then falls back to the normal ``useSkillsList``
 * (category-grouped full view).
 */
export function useLocalSkillSearch(
  filters: LocalSkillQueryFilters,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["skill-query", filters],
    queryFn: () => queryLocalSkills(filters),
    enabled,
    staleTime: 30 * 1000,
  });
}

// ----------------------------------------------------------------------
// Link-Health (homepage/source/docs)
// ----------------------------------------------------------------------

export interface LinkHealthEntry {
  url: string;
  status: number;
  ok: boolean;
  checked_at_ns: number;
  fresh: boolean;
}

export interface SkillLinkHealthResponse {
  skill: string;
  fields: {
    homepage_url?: LinkHealthEntry | null;
    source_url?: LinkHealthEntry | null;
    docs_url?: LinkHealthEntry | null;
  };
}

async function fetchLinkHealth(name: string): Promise<SkillLinkHealthResponse> {
  const res = await fetch(
    `/api/skills/${encodeURIComponent(name)}/link-health`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function useSkillLinkHealth(name: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["skill-link-health", name],
    queryFn: () => fetchLinkHealth(name!),
    enabled: !!name && enabled,
    staleTime: 5 * 60 * 1000,
  });
}

// ----------------------------------------------------------------------
// Skill-Finder (Catalog-Search + Install)
// ----------------------------------------------------------------------

export type TrustLevel =
  | "official"
  | "verified"
  | "community"
  | "experimental";

export type TrustFilter = TrustLevel | "any";
export type RiskFilter = "safe" | "monitor" | "ask";

export interface SkillCandidate {
  name: string;
  title: string;
  description: string;
  source: string;
  source_url: string;
  raw_url: string | null;
  trust: TrustLevel;
  stars: number | null;
  categories: string[];
  languages: string[];
  risk: string;
  tags: string[];
  score: number;
  reason: string;
}

export interface SkillSearchFilters {
  query: string;
  trust?: TrustFilter;
  min_stars?: number | null;
  category?: string | null;
  language?: string | null;
  max_risk?: RiskFilter | null;
  limit?: number;
}

export interface SkillSearchResponse {
  query: string;
  count: number;
  candidates: SkillCandidate[];
  brain_used: boolean;
}

export interface SkillCatalogMeta {
  total: number;
  categories: string[];
  languages: string[];
  sources: string[];
  trust_levels: TrustLevel[];
  risk_levels: RiskFilter[];
}

async function searchCatalog(
  filters: SkillSearchFilters,
): Promise<SkillSearchResponse> {
  const res = await fetch("/api/skills/catalog/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filters),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function installFromCatalog(c: SkillCandidate): Promise<{
  ok: boolean;
  name: string;
  path: string;
  skill?: SkillSummary;
}> {
  const res = await fetch("/api/skills/catalog/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: c.name,
      raw_url: c.raw_url,
      source_url: c.source_url,
      title: c.title,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function fetchCatalogMeta(): Promise<SkillCatalogMeta> {
  const res = await fetch("/api/skills/catalog/meta");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function useSkillSearch() {
  return useMutation({
    mutationFn: searchCatalog,
  });
}

export function useSkillInstall() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: installFromCatalog,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

export function useCatalogMeta() {
  return useQuery({
    queryKey: ["catalog-meta"],
    queryFn: fetchCatalogMeta,
    staleTime: 5 * 60 * 1000,
  });
}
