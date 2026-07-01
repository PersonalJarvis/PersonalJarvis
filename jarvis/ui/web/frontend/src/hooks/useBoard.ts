import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// ----------------------------------------------------------------------
// Types (mirror jarvis/ui/web/board_routes.py Pydantic schema)
// ----------------------------------------------------------------------

export interface SummaryTotals {
  tasks_completed: number;
  tasks_failed: number;
  voice_commands: number;
  hours_saved: number;
  activity_events: number;
  conversation_hours: number;
  user_words: number;
  jarvis_words: number;
  session_count: number;
  active_days: number;
  first_day: string | null;
}

export interface SummaryWindow {
  tasks_completed: number;
  tasks_failed: number;
  voice_commands: number;
  hours_saved: number;
  activity_events: number;
  conversation_hours: number;
  user_words: number;
  jarvis_words: number;
  session_count: number;
  voice_first_try_rate: number | null;
  unique_tools: number;
}

export interface BoardSummary {
  window_days: number;
  totals: SummaryTotals;
  window: SummaryWindow;
  streak_days: number;
  longest_streak: number;
}

export interface HeatmapCell {
  date: string; // ISO YYYY-MM-DD
  tasks_completed: number;
  tasks_failed: number;
  activity_events: number;
  conversation_hours: number;
  user_words: number;
  jarvis_words: number;
}

export interface BoardHeatmap {
  start: string;
  end: string;
  days: number;
  cells: HeatmapCell[];
}

export interface CategoryEntry {
  category: string;
  count: number;
}

export interface BoardCategories {
  window_days: number | null;
  total: number;
  categories: CategoryEntry[];
}

export interface ToolHistogramEntry {
  tool: string;
  days_used: number;
}

export interface BoardTools {
  window_days: number;
  total_unique: number;
  histogram: ToolHistogramEntry[];
}

export interface PersonalRecord {
  metric: string;
  value: number;
  achieved_on: string;
  context: Record<string, unknown>;
}

export interface BoardRecords {
  records: PersonalRecord[];
}

// ----------------------------------------------------------------------
// Fetchers
// ----------------------------------------------------------------------

async function fetchSummary(): Promise<BoardSummary> {
  const res = await fetch("/api/board/personal/summary");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchHeatmap(days = 365): Promise<BoardHeatmap> {
  const res = await fetch(`/api/board/personal/heatmap?days=${days}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchTools(windowDays = 90): Promise<BoardTools> {
  const res = await fetch(
    `/api/board/personal/tools?window_days=${windowDays}`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchRecords(): Promise<BoardRecords> {
  const res = await fetch("/api/board/personal/records");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchCategories(): Promise<BoardCategories> {
  const res = await fetch("/api/board/personal/categories");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function triggerRefresh(): Promise<{ ok: boolean; triggered: boolean }> {
  const res = await fetch("/api/board/personal/refresh", { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

// ----------------------------------------------------------------------
// Hooks
// ----------------------------------------------------------------------

/**
 * Dashboard summary. Polling every 30s is Plan-Decision §5-A #1. NO
 * pull-to-refresh, NO slot-machine UX — just deterministic polling
 * plus a manual refresh button that invalidates the same query.
 */
export function useBoardSummary() {
  return useQuery({
    queryKey: ["board", "summary"],
    queryFn: fetchSummary,
    // Live indicators: poll fast so spoken words climb visibly. The backend
    // re-aggregates on read (TTL-gated), so each poll reflects sessions.db.
    refetchInterval: 8_000,
    staleTime: 4_000,
  });
}

export function useBoardHeatmap(days = 365) {
  return useQuery({
    queryKey: ["board", "heatmap", days],
    queryFn: () => fetchHeatmap(days),
    // Drives the words-over-time chart + calendar — keep it reasonably live.
    refetchInterval: 20_000,
    staleTime: 8_000,
  });
}

export function useBoardTools(windowDays = 90) {
  return useQuery({
    queryKey: ["board", "tools", windowDays],
    queryFn: () => fetchTools(windowDays),
    refetchInterval: 2 * 60_000,
    staleTime: 60_000,
  });
}

export function useBoardRecords() {
  return useQuery({
    queryKey: ["board", "records"],
    queryFn: fetchRecords,
    refetchInterval: 2 * 60_000,
    staleTime: 60_000,
  });
}

export function useBoardCategories() {
  return useQuery({
    queryKey: ["board", "categories"],
    queryFn: fetchCategories,
    refetchInterval: 12_000,
    staleTime: 6_000,
  });
}

export function useBoardRefresh() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: triggerRefresh,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["board"] });
    },
  });
}

// ----------------------------------------------------------------------
// Phase B — Achievements + Bio
// ----------------------------------------------------------------------

export type AchievementTier = "mastery" | "reflection" | "social";

export interface AchievementItem {
  id: string;
  title: string;
  description: string;
  tier: AchievementTier;
  unlocked_at: string | null;
  evidence: Record<string, unknown>;
}

export interface AchievementListResponse {
  total: number;
  unlocked: number;
  items: AchievementItem[];
}

export interface BioResponse {
  text: string | null;
  generated_at: string | null;
  model_used: string | null;
  triggered_by: string | null;
  staleness_days: number | null;
}

export interface BioRegenerateResult {
  ok: boolean;
  generated_at: string | null;
  text: string | null;
  reason?: string | null;
}

export type BioFeedbackKind = "trifft" | "trifft_nicht" | "haerter"; // i18n-allow: API contract values matched by backend logic

export interface BioFeedbackResult {
  ok: boolean;
  reason?: string | null;
}

async function fetchAchievements(): Promise<AchievementListResponse> {
  const res = await fetch("/api/board/achievements");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchBio(): Promise<BioResponse> {
  const res = await fetch("/api/board/bio");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function triggerBioRegen(body: {
  memory_text?: string;
  soul_text?: string;
}): Promise<BioRegenerateResult> {
  const res = await fetch("/api/board/bio/regenerate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function sendBioFeedback(payload: {
  bio_generated_at: string;
  kind: BioFeedbackKind;
}): Promise<BioFeedbackResult> {
  const res = await fetch("/api/board/bio/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export function useAchievements() {
  return useQuery({
    queryKey: ["board", "achievements"],
    queryFn: fetchAchievements,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

export function useBio() {
  return useQuery({
    queryKey: ["board", "bio"],
    queryFn: fetchBio,
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
  });
}

export function useBioRegenerate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { memory_text?: string; soul_text?: string } = {}) =>
      triggerBioRegen(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["board", "bio"] });
    },
  });
}

/**
 * Reaction click under the AI profile. Three kinds: "trifft" / "trifft_nicht" (i18n-allow: API contract values)
 * / "haerter" (i18n-allow: API contract values). The signal calibrates the
 * NEXT bio generation; no immediate regenerate (Brainstorm-Decision 2026-05-02).
 */
export function useBioFeedback() {
  return useMutation({
    mutationFn: (payload: {
      bio_generated_at: string;
      kind: BioFeedbackKind;
    }) => sendBioFeedback(payload),
  });
}
