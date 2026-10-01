import { describe, expect, it } from "vitest";

import { routineTitle, upcomingRoutines, SIDEBAR_SCHEDULED_MAX } from "./SidebarScheduled";
import type { TaskSummary } from "@/views/automations/automationsModel";

function task(id: string, next: number, extra: Partial<TaskSummary> = {}): TaskSummary {
  return {
    id,
    title: id,
    state: "scheduled",
    trigger_type: "calendar",
    due_at_ns: next,
    next_due_at_ns: next,
    created_at_ns: 1,
    started_at_ns: null,
    finished_at_ns: null,
    attempts: 0,
    last_error: null,
    ...extra,
  } as TaskSummary;
}

describe("SidebarScheduled", () => {
  it("names the job, not the agent that owns it", () => {
    expect(routineTitle("[agent:GitHub Issue sortieren.] Daily GitHub issue triage")).toBe(
      "Daily GitHub issue triage",
    );
    expect(routineTitle("Morning brief")).toBe("Morning brief");
  });

  it("lists the soonest runs first, skips finished and paused ones, and stops at the cap", () => {
    const rows = upcomingRoutines([
      task("late", 400),
      task("done", 100, { state: "completed" }),
      task("paused", 50, { state: "paused" }),
      task("soon", 200),
      task("mid", 300),
      task("later", 500),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["soon", "mid", "late"].slice(0, SIDEBAR_SCHEDULED_MAX));
  });
});
