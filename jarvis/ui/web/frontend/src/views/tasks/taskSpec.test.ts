import { describe, it, expect } from "vitest";
import {
  buildTaskSpec,
  nextDailyOccurrence,
  type TaskDraft,
} from "./taskSpec";

function baseDraft(over: Partial<TaskDraft> = {}): TaskDraft {
  return {
    title: "My Task",
    prompt: "Do the thing.",
    scheduleMode: "once",
    onceMode: "delay",
    delaySeconds: 3600,
    atTimeLocal: "",
    recurringMode: "hourly",
    customIntervalSeconds: 1800,
    dailyTime: "07:00",
    modelTier: "auto",
    grants: [],
    ...over,
  };
}

describe("buildTaskSpec — trigger mapping", () => {
  it("maps once+delay to after_delay", () => {
    const spec = buildTaskSpec(baseDraft({ scheduleMode: "once", onceMode: "delay", delaySeconds: 120 }));
    expect(spec.trigger).toEqual({ type: "after_delay", delay_seconds: 120 });
  });

  it("maps once+at_time to at_time with an ISO timestamp", () => {
    const spec = buildTaskSpec(
      baseDraft({ scheduleMode: "once", onceMode: "at_time", atTimeLocal: "2099-01-01T09:30" }),
    );
    expect(spec.trigger.type).toBe("at_time");
    // iso_timestamp must be a parseable absolute timestamp
    expect(new Date((spec.trigger as { iso_timestamp: string }).iso_timestamp).getFullYear()).toBe(2099);
  });

  it("maps recurring+hourly to every(3600)", () => {
    const spec = buildTaskSpec(baseDraft({ scheduleMode: "recurring", recurringMode: "hourly" }));
    expect(spec.trigger).toEqual({ type: "every", interval_seconds: 3600 });
  });

  it("maps recurring+daily to every(86400) anchored to the next HH:MM", () => {
    const spec = buildTaskSpec(
      baseDraft({ scheduleMode: "recurring", recurringMode: "daily", dailyTime: "07:00" }),
    );
    expect(spec.trigger.type).toBe("every");
    expect((spec.trigger as { interval_seconds: number }).interval_seconds).toBe(86400);
    expect((spec.trigger as { start_at?: string }).start_at).toBeTruthy();
  });

  it("maps recurring+custom to every(customIntervalSeconds)", () => {
    const spec = buildTaskSpec(
      baseDraft({ scheduleMode: "recurring", recurringMode: "custom", customIntervalSeconds: 900 }),
    );
    expect(spec.trigger).toEqual({ type: "every", interval_seconds: 900 });
  });
});

describe("buildTaskSpec — agent action mapping", () => {
  it("builds an agent action with prompt + grants + tier", () => {
    const spec = buildTaskSpec(
      baseDraft({
        prompt: "Summarize my inbox.",
        modelTier: "deep",
        grants: [
          { plugin_id: "gmail", scope: "read" },
          { plugin_id: "buffer", scope: "write" },
        ],
      }),
    );
    expect(spec.action.kind).toBe("agent");
    expect(spec.action.prompt).toBe("Summarize my inbox.");
    expect(spec.action.model_tier).toBe("deep");
    expect(spec.action.plugin_grants).toEqual([
      { plugin_id: "gmail", scope: "read" },
      { plugin_id: "buffer", scope: "write" },
    ]);
  });

  it("carries the title through", () => {
    const spec = buildTaskSpec(baseDraft({ title: "Morning Briefing" }));
    expect(spec.title).toBe("Morning Briefing");
  });
});

describe("nextDailyOccurrence", () => {
  it("returns today's time when it is still in the future", () => {
    const now = new Date("2026-06-17T05:00:00");
    const iso = nextDailyOccurrence("07:00", now);
    expect(new Date(iso).getTime()).toBeGreaterThan(now.getTime());
    expect(new Date(iso).getDate()).toBe(17);
  });

  it("rolls over to tomorrow when the time already passed", () => {
    const now = new Date("2026-06-17T09:00:00");
    const iso = nextDailyOccurrence("07:00", now);
    expect(new Date(iso).getDate()).toBe(18);
  });
});
