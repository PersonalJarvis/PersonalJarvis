/**
 * Pure mapping from the create-form draft to the backend TaskSpec payload.
 *
 * Kept free of React so the schedule/action translation (the part that is
 * easy to get wrong) is unit-testable in isolation. Mirrors the Pydantic
 * schema in jarvis/tasks/schema.py (trigger + agent action + plugin grants).
 */

export type ScopeValue = "read" | "write" | "full";
export type ModelTier = "fast" | "deep" | "auto";

export interface DraftPluginGrant {
  plugin_id: string;
  scope: ScopeValue;
}

export interface TaskDraft {
  title: string;
  prompt: string;
  scheduleMode: "once" | "recurring";
  onceMode: "delay" | "at_time";
  delaySeconds: number;
  atTimeLocal: string; // value of <input type="datetime-local">
  recurringMode: "hourly" | "daily" | "custom";
  customIntervalSeconds: number;
  dailyTime: string; // "HH:MM"
  modelTier: ModelTier;
  grants: DraftPluginGrant[];
}

export type TaskTrigger =
  | { type: "after_delay"; delay_seconds: number }
  | { type: "at_time"; iso_timestamp: string }
  | { type: "every"; interval_seconds: number; start_at?: string };

export interface AgentActionPayload {
  kind: "agent";
  prompt: string;
  plugin_grants: DraftPluginGrant[];
  model_tier: ModelTier;
}

export interface TaskSpecPayload {
  title: string;
  trigger: TaskTrigger;
  action: AgentActionPayload;
}

/** Next absolute occurrence of an "HH:MM" wall-clock time, as an ISO string. */
export function nextDailyOccurrence(timeHHMM: string, now: Date): string {
  const [h, m] = timeHHMM.split(":").map((x) => parseInt(x, 10));
  const d = new Date(now);
  d.setHours(Number.isFinite(h) ? h : 0, Number.isFinite(m) ? m : 0, 0, 0);
  if (d.getTime() <= now.getTime()) {
    d.setDate(d.getDate() + 1);
  }
  return d.toISOString();
}

export function buildTrigger(draft: TaskDraft, now: Date = new Date()): TaskTrigger {
  if (draft.scheduleMode === "once") {
    if (draft.onceMode === "at_time") {
      return { type: "at_time", iso_timestamp: new Date(draft.atTimeLocal).toISOString() };
    }
    return { type: "after_delay", delay_seconds: draft.delaySeconds };
  }
  // recurring
  if (draft.recurringMode === "hourly") {
    return { type: "every", interval_seconds: 3600 };
  }
  if (draft.recurringMode === "daily") {
    return {
      type: "every",
      interval_seconds: 86400,
      start_at: nextDailyOccurrence(draft.dailyTime, now),
    };
  }
  return { type: "every", interval_seconds: draft.customIntervalSeconds };
}

export function buildTaskSpec(draft: TaskDraft, now: Date = new Date()): TaskSpecPayload {
  return {
    title: draft.title.trim(),
    trigger: buildTrigger(draft, now),
    action: {
      kind: "agent",
      prompt: draft.prompt.trim(),
      plugin_grants: draft.grants,
      model_tier: draft.modelTier,
    },
  };
}
