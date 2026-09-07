import type { SocietyAgentRow } from "@/lib/societyApi";
import { defaultFigureFor, type SocietyAgent } from "../data";
import type { FigureRecipe } from "../figures/figureRecipe";

export interface StudioDraft {
  title: string;
  description: string;
  dailyBudget: string;
  concurrentRuns: string;
  ceiling: SocietyAgent["permissionCeiling"];
  figure: FigureRecipe;
}

export function studioDraft(agent: SocietyAgent): StudioDraft {
  return {
    title: agent.title,
    description: agent.description,
    dailyBudget: String(agent.dailyBudgetUsd),
    concurrentRuns: String(agent.maxConcurrentRuns),
    ceiling: agent.permissionCeiling,
    figure: agent.figure ?? defaultFigureFor(agent.agentId, agent.tier),
  };
}

/** Only edited fields cross the wire: unrelated saves cannot re-derive focus. */
export function studioPatch(base: StudioDraft, draft: StudioDraft) {
  const patch: Partial<Pick<SocietyAgentRow,
    "title" | "description" | "daily_budget_usd" | "max_concurrent_runs" | "permission_ceiling" | "avatar"
  >> = {};
  if (base.title !== draft.title) patch.title = draft.title.trim();
  if (base.description !== draft.description) patch.description = draft.description;
  if (base.dailyBudget !== draft.dailyBudget) patch.daily_budget_usd = Number(draft.dailyBudget);
  if (base.concurrentRuns !== draft.concurrentRuns) patch.max_concurrent_runs = Number(draft.concurrentRuns);
  if (base.ceiling !== draft.ceiling) patch.permission_ceiling = draft.ceiling;
  if (JSON.stringify(base.figure) !== JSON.stringify(draft.figure)) patch.avatar = { ...draft.figure };
  return patch;
}

export function validStudioLimits(draft: StudioDraft): boolean {
  const budget = Number(draft.dailyBudget);
  const runs = Number(draft.concurrentRuns);
  return draft.dailyBudget.trim() !== "" && Number.isFinite(budget) && budget >= 0
    && draft.concurrentRuns.trim() !== "" && Number.isSafeInteger(runs) && runs >= 1;
}

export async function saveStudioAgent(agentId: string, body: object, model = false): Promise<SocietyAgentRow> {
  const response = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}${model ? "/model" : ""}`, {
    method: model ? "POST" : "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json() as { agent?: SocietyAgentRow; detail?: unknown };
  if (!response.ok || !payload.agent) {
    const detail = payload.detail;
    throw new Error(typeof detail === "string" ? detail : `Save failed (HTTP ${response.status})`);
  }
  return payload.agent;
}
