/**
 * Pure model for the Automations section — types mirroring the tasks API and
 * the small derivations the cards need (schedule line, countdown, template
 * pairing, result extraction). Kept free of React so the parts that are easy
 * to get wrong (interval → "Daily at 07:30", the run-history ordering) are
 * unit-testable in isolation.
 *
 * Mirrors `jarvis/tasks/schema.py` (`TASK_STATES`) and
 * `jarvis/tasks/templates/__init__.py` (`AutomationTemplate.to_api`).
 */

export type TaskState =
  | "pending"
  | "scheduled"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type TriggerType = "after_delay" | "at_time" | "on_event" | "every";

export interface TaskSummary {
  id: string;
  title: string;
  state: TaskState;
  trigger_type: TriggerType;
  due_at_ns: number | null;
  created_at_ns: number | null;
  started_at_ns: number | null;
  finished_at_ns: number | null;
  attempts: number;
  last_error: string | null;
  // Added with the automations campaign — older backends omit them, so every
  // field is optional and the view degrades to the pre-campaign behaviour.
  tags?: string[];
  created_by?: string | null;
  interval_seconds?: number | null;
  next_due_at_ns?: number | null;
  last_run_state?: TaskState | null;
  last_result?: string | null;
}

export interface TaskStep {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  timestamp_ns: number;
}

export interface TaskDetail extends TaskSummary {
  spec: Record<string, unknown> | null;
  steps: TaskStep[];
}

export interface TasksListResponse {
  tasks: TaskSummary[];
  total: number;
}

export type TemplateCategory = "news" | "productivity" | "finance" | "research" | "developer";

/** Catalogue section order — mirrors `CATEGORIES` in the template package. */
export const TEMPLATE_CATEGORIES: readonly TemplateCategory[] = [
  "news",
  "productivity",
  "finance",
  "research",
  "developer",
];

export type ScheduleKind = "hourly" | "daily" | "weekly";

export interface TemplateSchedule {
  kind: ScheduleKind;
  time: string; // "HH:MM"
  weekday: number; // 0 = Monday … 6 = Sunday (Python weekday())
}

export interface TemplateInput {
  key: string;
  label: string;
  placeholder: string;
  default: string;
  required: boolean;
}

export interface AutomationTemplate {
  key: string;
  category: TemplateCategory;
  icon: string;
  name: string;
  description: string;
  schedule: TemplateSchedule;
  schedule_label: string;
  plugin_grants: { plugin_id: string; scope: string }[];
  requires: string[];
  missing: string[];
  ready: boolean;
  inputs: TemplateInput[];
  model_tier: "fast" | "deep" | "auto";
  tags: string[];
  prompt: string;
}

export interface TemplatesResponse {
  templates: AutomationTemplate[];
  categories: TemplateCategory[];
}

export const TEMPLATE_TAG_PREFIX = "template:";

export const TERMINAL_STATES: readonly TaskState[] = [
  "completed",
  "failed",
  "cancelled",
  "interrupted",
];

export const ACTIVE_STATES: readonly TaskState[] = ["pending", "scheduled", "running", "paused"];

export function isTerminal(state: TaskState): boolean {
  return TERMINAL_STATES.includes(state);
}

export function isActive(state: TaskState): boolean {
  return ACTIVE_STATES.includes(state);
}

/** A recurring task (an "automation") — the `every` / `on_event` triggers. */
export function isRecurringTrigger(trigger: TriggerType): boolean {
  return trigger === "every" || trigger === "on_event";
}

/** A one-off timed task (a "schedule") — the `after_delay` / `at_time` triggers. */
export function isOneShotTrigger(trigger: TriggerType): boolean {
  return trigger === "after_delay" || trigger === "at_time";
}

/** The template key a task was created from (`template:<key>` tag), if any. */
export function templateKeyOf(task: Pick<TaskSummary, "tags">): string | null {
  for (const tag of task.tags ?? []) {
    if (tag.startsWith(TEMPLATE_TAG_PREFIX)) return tag.slice(TEMPLATE_TAG_PREFIX.length);
  }
  return null;
}

/**
 * The user's automations: every recurring task that is not finished. Newest
 * first so a freshly added one lands at the top of the grid.
 */
export function selectAutomations(tasks: TaskSummary[]): TaskSummary[] {
  return tasks
    .filter((t) => isRecurringTrigger(t.trigger_type) && !isTerminal(t.state))
    .sort((a, b) => (b.created_at_ns ?? 0) - (a.created_at_ns ?? 0));
}

/**
 * The user's schedules: one-off timed tasks that are still waiting for their
 * moment. Soonest first — a schedule is read as "what happens next", not as
 * "what happened last", so this list is the only one sorted forwards.
 *
 * A one-shot that is already running belongs to the run history instead; the
 * three selections here are deliberately disjoint so no task shows up on two
 * tabs and gets deleted twice.
 */
export function selectSchedules(tasks: TaskSummary[]): TaskSummary[] {
  return tasks
    .filter(
      (t) =>
        isOneShotTrigger(t.trigger_type) &&
        (t.state === "pending" || t.state === "scheduled" || t.state === "paused"),
    )
    .sort((a, b) => (a.due_at_ns ?? Number.MAX_SAFE_INTEGER) - (b.due_at_ns ?? Number.MAX_SAFE_INTEGER));
}

/**
 * The state a run row should show.
 *
 * A recurring automation goes straight back to `scheduled` after a run, so its
 * own `state` says nothing about how that run went — `last_run_state` does.
 * One value, derived in one place, so the dot, the label and the "problems"
 * filter can never disagree about the same row.
 */
export function runStateOf(task: TaskSummary): TaskState {
  if (isTerminal(task.state) || task.state === "running") return task.state;
  return task.last_run_state ?? task.state;
}

/**
 * The run history: everything that has actually run, newest activity first.
 *
 * A recurring automation belongs here once it has a run behind it — the task
 * row carries only its LATEST one, which is why the tab is worded as the last
 * run of each rather than a full log. Without this the tab was permanently
 * empty for anyone whose automations are all recurring, which is everyone.
 *
 * A still-waiting one-shot is NOT a run — it lives on the Schedules tab until
 * it fires.
 */
export function selectRuns(tasks: TaskSummary[]): TaskSummary[] {
  return tasks
    .filter((t) => isTerminal(t.state) || t.state === "running" || Boolean(t.last_run_state))
    .sort((a, b) => activityNs(b) - activityNs(a));
}

function activityNs(t: TaskSummary): number {
  return t.finished_at_ns ?? t.started_at_ns ?? t.due_at_ns ?? t.created_at_ns ?? 0;
}

/** The four headline numbers above the tabs. */
export interface AutomationStats {
  /** Automations that are armed (everything except a paused one). */
  active: number;
  paused: number;
  /** The soonest upcoming moment across armed automations and schedules. */
  nextDueNs: number | null;
  /** Title of the task that owns `nextDueNs` ("" when nothing is due). */
  nextTitle: string;
  /** One-off schedules still waiting to fire. */
  schedules: number;
  /** Automations whose last run failed or was interrupted. */
  problems: number;
  /** Title of one such automation, for the tile's second line. */
  problemTitle: string;
}

/**
 * Derives the headline numbers from the task list.
 *
 * Deliberately NOT "runs in the last 7 days": a recurring task carries only
 * its latest run, so a per-week count would silently undercount and read as a
 * broken number. Every figure here is one the list can actually answer.
 */
export function automationStats(tasks: TaskSummary[]): AutomationStats {
  const automations = selectAutomations(tasks);
  const schedules = selectSchedules(tasks);

  let nextDueNs: number | null = null;
  let nextTitle = "";
  for (const task of [...automations, ...schedules]) {
    if (task.state === "paused") continue;
    const due = task.next_due_at_ns ?? task.due_at_ns ?? null;
    if (due == null) continue;
    if (nextDueNs == null || due < nextDueNs) {
      nextDueNs = due;
      nextTitle = task.title;
    }
  }

  const broken = automations.filter(
    (t) => t.last_run_state === "failed" || t.last_run_state === "interrupted",
  );

  return {
    active: automations.filter((t) => t.state !== "paused").length,
    paused: automations.filter((t) => t.state === "paused").length,
    nextDueNs,
    nextTitle,
    schedules: schedules.length,
    problems: broken.length,
    problemTitle: broken[0]?.title ?? "",
  };
}

export type RunFilter = "all" | "running" | "done" | "problems";

export function filterRuns(runs: TaskSummary[], filter: RunFilter): TaskSummary[] {
  switch (filter) {
    case "running":
      return runs.filter((r) => r.state === "running");
    case "done":
      return runs.filter((r) => runStateOf(r) === "completed");
    case "problems":
      return runs.filter((r) => {
        const state = runStateOf(r);
        return state === "failed" || state === "interrupted" || state === "cancelled";
      });
    default:
      return runs;
  }
}

/** The kind of schedule an interval encodes; `null` for odd custom intervals. */
export function scheduleKindOf(intervalSeconds: number | null | undefined): ScheduleKind | null {
  if (!intervalSeconds) return null;
  if (intervalSeconds === 3600) return "hourly";
  if (intervalSeconds === 86_400) return "daily";
  if (intervalSeconds === 7 * 86_400) return "weekly";
  return null;
}

export interface ScheduleWords {
  hourly: string; // "Every hour"
  daily: string; // "Daily at {time}"
  weekly: string; // "{day} at {time}"
  everyMinutes: string; // "Every {n} min"
  everyHours: string; // "Every {n} h"
  onEvent: string; // "When an event fires"
  weekdays: string[]; // Monday … Sunday (plural forms, 7 entries)
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function localTimeHHMM(ns: number): string {
  const d = new Date(ns / 1e6);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** Monday-based weekday (Python `weekday()`), from a JS Date. */
export function mondayWeekday(d: Date): number {
  return (d.getDay() + 6) % 7;
}

/**
 * "Daily at 07:30" for a task, derived from its interval and the next due
 * moment (the wall-clock time of the next occurrence IS the schedule time).
 */
export function scheduleLineForTask(
  task: Pick<TaskSummary, "trigger_type" | "interval_seconds" | "next_due_at_ns" | "due_at_ns">,
  words: ScheduleWords,
): string {
  if (task.trigger_type === "on_event") return words.onEvent;
  const interval = task.interval_seconds ?? null;
  const dueNs = task.next_due_at_ns ?? task.due_at_ns ?? null;
  const kind = scheduleKindOf(interval);
  if (kind === "hourly") return words.hourly;
  if (kind === "daily") {
    return dueNs ? words.daily.replace("{time}", localTimeHHMM(dueNs)) : words.daily.replace("{time}", "—");
  }
  if (kind === "weekly") {
    if (!dueNs) return words.weekly.replace("{day}", "—").replace("{time}", "—");
    const d = new Date(dueNs / 1e6);
    const day = words.weekdays[mondayWeekday(d)] ?? "";
    return words.weekly.replace("{day}", day).replace("{time}", localTimeHHMM(dueNs));
  }
  if (interval) {
    if (interval % 3600 === 0) return words.everyHours.replace("{n}", String(interval / 3600));
    return words.everyMinutes.replace("{n}", String(Math.max(1, Math.round(interval / 60))));
  }
  return "—";
}

/** Compact countdown: "42s", "7min", "3h", "2d". Never negative. */
export function formatDelta(ns: number): string {
  const sec = Math.max(0, Math.round(ns / 1e9));
  if (sec < 60) return `${sec}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}min`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr}h`;
  const day = Math.round(hr / 24);
  return `${day}d`;
}

/** Duration between two ns timestamps, "1m 12s" style; `null` if unknown. */
export function formatDuration(startNs: number | null, endNs: number | null): string | null {
  if (startNs == null || endNs == null || endNs < startNs) return null;
  const sec = Math.round((endNs - startNs) / 1e9);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const rest = sec % 60;
  if (min < 60) return rest ? `${min}m ${rest}s` : `${min}m`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m`;
}

/** The first non-empty line of a prompt, trimmed to a card-sized preview. */
export function firstLine(text: string | null | undefined, max = 140): string {
  const line = (text ?? "")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  if (!line) return "";
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** The text of the latest `agent_result` log step, if the run produced one. */
export function extractAgentResult(steps: TaskStep[] | undefined): string | null {
  if (!steps) return null;
  for (let i = steps.length - 1; i >= 0; i--) {
    const p = steps[i].payload;
    if (p && p.event === "agent_result" && typeof p.text === "string") return p.text;
  }
  return null;
}

/** The prompt of an agent action from a task spec, when present. */
export function promptOfSpec(spec: Record<string, unknown> | null | undefined): string {
  const action = spec?.action as Record<string, unknown> | undefined;
  const prompt = action?.prompt;
  return typeof prompt === "string" ? prompt : "";
}

/** "gmail" → "Gmail", "google_calendar" → "Google Calendar", "github" → "GitHub". */
export function humanizeToolName(name: string): string {
  const special: Record<string, string> = {
    gmail: "Gmail",
    github: "GitHub",
    google_calendar: "Google Calendar",
    google_drive: "Google Drive",
    search_web: "Web search",
    youtube_music: "YouTube Music",
    linear: "Linear",
    "wiki-recall": "Wiki",
    "notebooklm-mcp": "NotebookLM",
    run_shell: "Shell",
  };
  if (special[name]) return special[name];
  return name
    .split(/[_\-/]/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export function humanizeMissing(missing: string[]): string {
  return missing.map(humanizeToolName).join(", ");
}

/** Summarise a step payload for the timeline row. */
export function summarizePayload(p: Record<string, unknown>): string {
  try {
    if (p && typeof p.event === "string") {
      const text = typeof p.text === "string" ? p.text : "";
      const rest = text ? `${p.event}: ${text}` : String(p.event);
      return rest.length > 160 ? `${rest.slice(0, 159)}…` : rest;
    }
    const s = JSON.stringify(p);
    return s.length > 160 ? `${s.slice(0, 159)}…` : s;
  } catch {
    return "(unreadable)";
  }
}

/** Group templates by category in catalogue order; empty categories are skipped. */
export function groupTemplates(
  templates: AutomationTemplate[],
): { category: TemplateCategory; templates: AutomationTemplate[] }[] {
  const out: { category: TemplateCategory; templates: AutomationTemplate[] }[] = [];
  for (const category of TEMPLATE_CATEGORIES) {
    const items = templates.filter((t) => t.category === category);
    if (items.length) out.push({ category, templates: items });
  }
  // Unknown categories (a future backend) land at the end rather than vanish.
  const known = new Set<string>(TEMPLATE_CATEGORIES);
  const other = templates.filter((t) => !known.has(t.category));
  if (other.length) out.push({ category: other[0].category, templates: other });
  return out;
}
