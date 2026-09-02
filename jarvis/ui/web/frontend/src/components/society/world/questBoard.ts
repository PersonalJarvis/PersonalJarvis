/**
 * The Quest Board's pure helpers — grouping, ordering and ages, kept out of
 * the drawer so `questBoard.test.ts` pins them without React or a canvas.
 */
import type { QuestState, SocietyQuestRow } from "@/lib/societyApi";

/** States a quest can still move out of — what the board shows first. */
export const ACTIVE_STATES: ReadonlySet<QuestState> = new Set(["open", "assigned", "running"]);

export type QuestGroup = "active" | "done" | "failed" | "cancelled";

export function groupOf(state: QuestState): QuestGroup {
  if (ACTIVE_STATES.has(state)) return "active";
  if (state === "done") return "done";
  if (state === "failed") return "failed";
  return "cancelled";
}

/** Display order: active first (running before assigned before open, newest first), then the rest newest first. */
export function groupQuests(rows: readonly SocietyQuestRow[]): Record<QuestGroup, SocietyQuestRow[]> {
  const out: Record<QuestGroup, SocietyQuestRow[]> = { active: [], done: [], failed: [], cancelled: [] };
  for (const row of rows) out[groupOf(row.state)].push(row);
  const rank: Record<QuestState, number> = { running: 0, assigned: 1, open: 2, done: 3, failed: 3, cancelled: 3 };
  out.active.sort((a, b) => rank[a.state] - rank[b.state] || b.created_ms - a.created_ms);
  for (const key of ["done", "failed", "cancelled"] as const) {
    out[key].sort((a, b) => (b.done_ms ?? b.updated_ms) - (a.done_ms ?? a.updated_ms));
  }
  return out;
}

/** Quests the monument shows as floating scrolls: the active ones, oldest first, capped. */
export function boardScrolls(rows: readonly SocietyQuestRow[], cap = 8): SocietyQuestRow[] {
  return rows
    .filter((r) => ACTIVE_STATES.has(r.state))
    .sort((a, b) => a.created_ms - b.created_ms)
    .slice(0, cap);
}

export type AgeUnit = "now" | "min" | "h" | "d";

/** A coarse age for a row: the unit and the count, for the locale to word. */
export function ageOf(fromMs: number, nowMs: number): { unit: AgeUnit; n: number } {
  const s = Math.max(0, Math.floor((nowMs - fromMs) / 1000));
  if (s < 60) return { unit: "now", n: 0 };
  const m = Math.floor(s / 60);
  if (m < 60) return { unit: "min", n: m };
  const h = Math.floor(m / 60);
  if (h < 48) return { unit: "h", n: h };
  return { unit: "d", n: Math.floor(h / 24) };
}

/** The routing line's shape: who took it and whether it was forged for this quest. */
export function takerKind(row: SocietyQuestRow): "forged" | "taken" | "none" {
  if (!row.agent_id) return "none";
  return row.routing?.forged ? "forged" : "taken";
}
