import { describe, expect, it } from "vitest";
import {
  extractAgentResult,
  filterRuns,
  firstLine,
  formatDuration,
  groupTemplates,
  humanizeMissing,
  scheduleLineForTask,
  selectAutomations,
  selectRuns,
  templateKeyOf,
  type AutomationTemplate,
  type ScheduleWords,
  type TaskSummary,
} from "./automationsModel";

const WORDS: ScheduleWords = {
  hourly: "Every hour",
  daily: "Daily at {time}",
  weekly: "{day} at {time}",
  everyMinutes: "Every {n} min",
  everyHours: "Every {n} h",
  onEvent: "When an event fires",
  weekdays: ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"],
};

function task(over: Partial<TaskSummary>): TaskSummary {
  return {
    id: "t",
    title: "T",
    state: "scheduled",
    trigger_type: "every",
    due_at_ns: null,
    created_at_ns: 1,
    started_at_ns: null,
    finished_at_ns: null,
    attempts: 0,
    last_error: null,
    ...over,
  };
}

/** ns timestamp of a local wall-clock moment. */
function localNs(y: number, m: number, d: number, hh: number, mm: number): number {
  return new Date(y, m - 1, d, hh, mm, 0, 0).getTime() * 1e6;
}

describe("scheduleLineForTask", () => {
  it("derives the daily time from the next due moment", () => {
    const line = scheduleLineForTask(
      task({ interval_seconds: 86_400, next_due_at_ns: localNs(2026, 8, 25, 7, 30) }),
      WORDS,
    );
    expect(line).toBe("Daily at 07:30");
  });

  it("names the weekday for a weekly interval (Monday-based)", () => {
    // 2026-08-24 is a Monday.
    const line = scheduleLineForTask(
      task({ interval_seconds: 7 * 86_400, next_due_at_ns: localNs(2026, 8, 24, 9, 0) }),
      WORDS,
    );
    expect(line).toBe("Mondays at 09:00");
  });

  it("handles hourly, custom and event triggers", () => {
    expect(scheduleLineForTask(task({ interval_seconds: 3600 }), WORDS)).toBe("Every hour");
    expect(scheduleLineForTask(task({ interval_seconds: 1800 }), WORDS)).toBe("Every 30 min");
    expect(scheduleLineForTask(task({ interval_seconds: 7200 }), WORDS)).toBe("Every 2 h");
    expect(scheduleLineForTask(task({ trigger_type: "on_event" }), WORDS)).toBe("When an event fires");
  });

  it("falls back to the legacy due_at_ns when next_due_at_ns is absent (old backend)", () => {
    const line = scheduleLineForTask(
      task({ interval_seconds: 86_400, due_at_ns: localNs(2026, 8, 25, 18, 5) }),
      WORDS,
    );
    expect(line).toBe("Daily at 18:05");
  });
});

describe("selection", () => {
  const recurring = task({ id: "a", trigger_type: "every", state: "scheduled", created_at_ns: 5 });
  const paused = task({ id: "b", trigger_type: "every", state: "paused", created_at_ns: 9 });
  const finishedRecurring = task({ id: "c", trigger_type: "every", state: "cancelled", finished_at_ns: 3 });
  const oneShotPending = task({ id: "d", trigger_type: "at_time", state: "scheduled", due_at_ns: 2 });
  const oneShotDone = task({ id: "e", trigger_type: "after_delay", state: "completed", finished_at_ns: 8 });
  const running = task({ id: "f", trigger_type: "every", state: "running", started_at_ns: 7 });
  const all = [recurring, paused, finishedRecurring, oneShotPending, oneShotDone, running];

  it("automations = active recurring tasks, newest first", () => {
    expect(selectAutomations(all).map((t) => t.id)).toEqual(["b", "a", "f"]);
  });

  it("runs = ran/running + pending one-shots, newest activity first", () => {
    expect(selectRuns(all).map((t) => t.id)).toEqual(["e", "f", "c", "d"]);
  });

  it("filters runs by chip", () => {
    const runs = selectRuns(all);
    expect(filterRuns(runs, "done").map((t) => t.id)).toEqual(["e"]);
    expect(filterRuns(runs, "problems").map((t) => t.id)).toEqual(["c"]);
    expect(filterRuns(runs, "running").map((t) => t.id)).toEqual(["f", "d"]);
  });
});

describe("helpers", () => {
  it("reads the template key from the tag", () => {
    expect(templateKeyOf({ tags: ["x", "template:morning_briefing"] })).toBe("morning_briefing");
    expect(templateKeyOf({ tags: [] })).toBeNull();
    expect(templateKeyOf({})).toBeNull();
  });

  it("extracts the latest agent_result step text", () => {
    expect(
      extractAgentResult([
        { seq: 1, kind: "log", payload: { event: "agent_result", text: "old" }, timestamp_ns: 1 },
        { seq: 2, kind: "log", payload: { event: "tool", text: "x" }, timestamp_ns: 2 },
        { seq: 3, kind: "log", payload: { event: "agent_result", text: "new" }, timestamp_ns: 3 },
      ]),
    ).toBe("new");
    expect(extractAgentResult([])).toBeNull();
  });

  it("formats durations and first lines", () => {
    expect(formatDuration(0, 42e9)).toBe("42s");
    expect(formatDuration(0, 125e9)).toBe("2m 5s");
    expect(formatDuration(null, 5)).toBeNull();
    expect(firstLine("\n\n  Summarise my day.\nSecond line")).toBe("Summarise my day.");
    expect(firstLine("a".repeat(200), 10)).toHaveLength(10);
  });

  it("humanizes missing tools", () => {
    expect(humanizeMissing(["gmail", "google_calendar", "github"])).toBe("Gmail, Google Calendar, GitHub");
    expect(humanizeMissing(["some_new_tool"])).toBe("Some New Tool");
  });

  it("groups templates in catalogue order and keeps unknown categories", () => {
    const tpl = (key: string, category: string) =>
      ({ key, category } as unknown as AutomationTemplate);
    const groups = groupTemplates([tpl("a", "developer"), tpl("b", "news"), tpl("c", "future")]);
    expect(groups.map((g) => g.category)).toEqual(["news", "developer", "future"]);
  });
});
