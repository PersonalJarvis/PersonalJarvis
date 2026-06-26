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

/** Curated "when" events for the When-Then builder. Each maps onto one bus
 * event class plus a filter_expr — the user never sees raw event names. */
export type WhenKey =
  | "mission_succeeded"
  | "mission_failed"
  | "mission_cancelled";

/** The "then" action a When-Then rule runs. */
export type ThenKind = "computer_use" | "agent" | "notify";

export interface TaskDraft {
  title: string;
  prompt: string;
  // Top-level: a time-based schedule vs. an event-driven When-Then rule.
  triggerMode: "schedule" | "event";
  scheduleMode: "once" | "recurring";
  onceMode: "delay" | "at_time";
  delaySeconds: number;
  atTimeLocal: string; // value of <input type="datetime-local">
  recurringMode: "hourly" | "daily" | "custom";
  customIntervalSeconds: number;
  dailyTime: string; // "HH:MM"
  modelTier: ModelTier;
  grants: DraftPluginGrant[];
  // When-Then fields (only meaningful when triggerMode === "event").
  whenKey: WhenKey;
  thenKind: ThenKind;
  cuPrompt: string; // Computer-Use goal, supports {result_uri} placeholders
  announceText: string; // spoken confirmation after the action finishes
}

export type TaskTrigger =
  | { type: "after_delay"; delay_seconds: number }
  | { type: "at_time"; iso_timestamp: string }
  | { type: "every"; interval_seconds: number; start_at?: string }
  | {
      type: "on_event";
      event_name: string;
      filter_expr?: string | null;
      max_firings?: number | null;
    };

export interface AgentActionPayload {
  kind: "agent";
  prompt: string;
  plugin_grants: DraftPluginGrant[];
  model_tier: ModelTier;
}

export interface HarnessDispatchActionPayload {
  kind: "harness_dispatch";
  harness: string;
  prompt: string;
  allow_computer_use: boolean;
}

export interface SpeakActionPayload {
  kind: "speak";
  text: string;
}

export type TaskActionPayload =
  | AgentActionPayload
  | HarnessDispatchActionPayload
  | SpeakActionPayload;

export interface TaskSpecPayload {
  title: string;
  trigger: TaskTrigger;
  action: TaskActionPayload;
  announce_on_success?: string;
  announce_on_failure?: string;
}

/** Curated "when" → (event class, filter) mapping. The single place that knows
 * a mission outcome is a `MissionCompleted` filtered by `status`. */
export const WHEN_FILTERS: Record<WhenKey, string> = {
  mission_succeeded: "status == 'approved'",
  mission_failed: "status == 'failed'",
  mission_cancelled: "status == 'cancelled'",
};

/** The CU harness name a Computer-Use "then" action dispatches to. */
export const CU_HARNESS = "screenshot";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * A sensible default for the "at date/time" picker, formatted for an
 * `<input type="datetime-local">` (local wall-clock "YYYY-MM-DDTHH:mm", never
 * UTC): one hour from now, rounded up to the next quarter hour.
 *
 * Pre-filling the field is what kills the browser's raw, locale-ugly empty
 * placeholder ("TT.mm.jjjj --:--" on a German system) — a freshly opened
 * picker now shows a real, editable value instead of dead grey hint text.
 */
export function defaultAtTimeLocal(now: Date = new Date()): string {
  const d = new Date(now.getTime() + 60 * 60 * 1000);
  // setMinutes carries overflow: 60 rolls into the next hour cleanly.
  d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}` +
    `T${pad2(d.getHours())}:${pad2(d.getMinutes())}`
  );
}

/**
 * The selected date written out long-form for the dialog's faint background
 * watermark — e.g. "Wednesday, 24 June" / "Mittwoch, 24. Juni". Locale-aware,
 * date only (the time already shows inside the input, so the watermark stays
 * calm). Falls back to `now` for an unparseable field value, and to the "en"
 * locale if the requested one is unavailable in the runtime's ICU data.
 */
export function formatWatermarkDate(
  atTimeLocal: string,
  locale: string,
  now: Date = new Date(),
): string {
  const parsed = atTimeLocal ? new Date(atTimeLocal) : now;
  const d = Number.isNaN(parsed.getTime()) ? now : parsed;
  const opts: Intl.DateTimeFormatOptions = {
    weekday: "long",
    day: "numeric",
    month: "long",
  };
  try {
    return new Intl.DateTimeFormat(locale, opts).format(d);
  } catch {
    return new Intl.DateTimeFormat("en", opts).format(d);
  }
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

/** A When-Then event trigger: a `MissionCompleted` filtered by the curated
 * `whenKey`. A standing rule (max_firings null) — it fires for every matching
 * mission until the user deletes it. */
export function buildEventTrigger(draft: TaskDraft): TaskTrigger {
  return {
    type: "on_event",
    event_name: "MissionCompleted",
    filter_expr: WHEN_FILTERS[draft.whenKey] ?? null,
    max_firings: null,
  };
}

export function buildTrigger(draft: TaskDraft, now: Date = new Date()): TaskTrigger {
  if (draft.triggerMode === "event") {
    return buildEventTrigger(draft);
  }
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

/** The "then" action for a When-Then rule, derived from the chosen `thenKind`.
 * Computer-Use dispatches to the CU harness; "agent" runs a brain turn; "notify"
 * speaks the announcement as its sole action. */
export function buildEventAction(draft: TaskDraft): TaskActionPayload {
  if (draft.thenKind === "computer_use") {
    return {
      kind: "harness_dispatch",
      harness: CU_HARNESS,
      prompt: draft.cuPrompt.trim() || "Open the mission result in the browser.",
      allow_computer_use: true,
    };
  }
  if (draft.thenKind === "agent") {
    return {
      kind: "agent",
      prompt: draft.prompt.trim(),
      plugin_grants: draft.grants,
      model_tier: draft.modelTier,
    };
  }
  // "notify" — the spoken confirmation IS the action.
  return { kind: "speak", text: draft.announceText.trim() || "Done." };
}

export function buildTaskSpec(draft: TaskDraft, now: Date = new Date()): TaskSpecPayload {
  if (draft.triggerMode === "event") {
    const spec: TaskSpecPayload = {
      title: draft.title.trim(),
      trigger: buildTrigger(draft, now),
      action: buildEventAction(draft),
    };
    // For CU/agent actions the announcement is a separate post-action readback;
    // for "notify" the speak action already says it, so no announce field.
    const announce = draft.announceText.trim();
    if (draft.thenKind !== "notify" && announce) {
      spec.announce_on_success = announce;
    }
    return spec;
  }
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
