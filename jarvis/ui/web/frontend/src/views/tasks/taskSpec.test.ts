import { describe, it, expect } from "vitest";
import {
  buildTaskSpec,
  defaultAtTimeLocal,
  formatWatermarkDate,
  nextDailyOccurrence,
  type TaskDraft,
} from "./taskSpec";

function baseDraft(over: Partial<TaskDraft> = {}): TaskDraft {
  return {
    title: "My Task",
    prompt: "Do the thing.",
    triggerMode: "schedule",
    scheduleMode: "once",
    onceMode: "delay",
    delaySeconds: 3600,
    atTimeLocal: "",
    recurringMode: "hourly",
    customIntervalSeconds: 1800,
    dailyTime: "07:00",
    modelTier: "auto",
    grants: [],
    whenKey: "mission_succeeded",
    thenKind: "computer_use",
    cuPrompt: "",
    announceText: "",
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
    const action = spec.action;
    expect(action.kind).toBe("agent");
    if (action.kind !== "agent") return; // narrow the union
    expect(action.prompt).toBe("Summarize my inbox.");
    expect(action.model_tier).toBe("deep");
    expect(action.plugin_grants).toEqual([
      { plugin_id: "gmail", scope: "read" },
      { plugin_id: "buffer", scope: "write" },
    ]);
  });

  it("carries the title through", () => {
    const spec = buildTaskSpec(baseDraft({ title: "Morning Briefing" }));
    expect(spec.title).toBe("Morning Briefing");
  });
});

describe("buildTaskSpec — When-Then event mapping", () => {
  it("maps a 'mission succeeds → Computer-Use' rule", () => {
    const spec = buildTaskSpec(
      baseDraft({
        triggerMode: "event",
        whenKey: "mission_succeeded",
        thenKind: "computer_use",
        cuPrompt: "Open {result_uri} in the browser",
        announceText: "Your mission is ready.",
      }),
    );
    expect(spec.trigger).toEqual({
      type: "on_event",
      event_name: "MissionCompleted",
      filter_expr: "status == 'approved'",
      max_firings: null,
    });
    const action = spec.action;
    expect(action.kind).toBe("harness_dispatch");
    if (action.kind !== "harness_dispatch") return;
    expect(action.harness).toBe("screenshot");
    expect(action.prompt).toBe("Open {result_uri} in the browser");
    expect(action.allow_computer_use).toBe(true);
    // CU/agent actions carry the announcement as a separate readback field.
    expect(spec.announce_on_success).toBe("Your mission is ready.");
  });

  it("maps the failed/cancelled when-keys to the right filter", () => {
    const failed = buildTaskSpec(baseDraft({ triggerMode: "event", whenKey: "mission_failed" }));
    expect((failed.trigger as { filter_expr: string }).filter_expr).toBe("status == 'failed'");
    const cancelled = buildTaskSpec(
      baseDraft({ triggerMode: "event", whenKey: "mission_cancelled" }),
    );
    expect((cancelled.trigger as { filter_expr: string }).filter_expr).toBe(
      "status == 'cancelled'",
    );
  });

  it("a 'just notify' rule is a speak action with no separate announce field", () => {
    const spec = buildTaskSpec(
      baseDraft({
        triggerMode: "event",
        thenKind: "notify",
        announceText: "Mission done.",
      }),
    );
    const action = spec.action;
    expect(action.kind).toBe("speak");
    if (action.kind !== "speak") return;
    expect(action.text).toBe("Mission done.");
    expect(spec.announce_on_success).toBeUndefined();
  });
});

describe("defaultAtTimeLocal", () => {
  it("returns a local datetime-local string one hour ahead, rounded up to the next quarter hour", () => {
    // 13:07 -> +1h = 14:07 -> round up to 14:15
    expect(defaultAtTimeLocal(new Date(2026, 5, 24, 13, 7, 0))).toBe("2026-06-24T14:15");
  });

  it("rolls the hour over when rounding crosses the top of the hour", () => {
    // 13:52 -> +1h = 14:52 -> round up to 15:00
    expect(defaultAtTimeLocal(new Date(2026, 5, 24, 13, 52, 0))).toBe("2026-06-24T15:00");
  });

  it("leaves a value already on a quarter hour unchanged", () => {
    // 13:00 -> +1h = 14:00 -> already aligned
    expect(defaultAtTimeLocal(new Date(2026, 5, 24, 13, 0, 0))).toBe("2026-06-24T14:00");
  });

  it("always emits a full, non-empty datetime-local shape (never the native placeholder)", () => {
    const out = defaultAtTimeLocal(new Date(2026, 5, 24, 13, 7));
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    expect(out.length).toBe(16);
  });
});

describe("formatWatermarkDate", () => {
  it("writes the selected date long-form in English", () => {
    const out = formatWatermarkDate("2026-06-24T15:30", "en");
    expect(out).toContain("June");
    expect(out).toContain("24");
  });

  it("localizes the same date to German", () => {
    const out = formatWatermarkDate("2026-06-24T15:30", "de");
    expect(out).toContain("Juni");
    expect(out).toContain("24");
  });

  it("falls back to `now` for an empty / unparseable field value", () => {
    const out = formatWatermarkDate("", "en", new Date(2026, 5, 24, 9, 0));
    expect(out).toContain("June");
    expect(out).toContain("24");
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
