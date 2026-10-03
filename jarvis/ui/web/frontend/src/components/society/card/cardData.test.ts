import { afterEach, describe, expect, test, vi } from "vitest";

import {
  describeTrigger,
  displayRoutineTitle,
  fetchTaskEventCatalog,
  promoteAgentSkillForReview,
  routineScheduleLine,
} from "@/components/society/cardData";

const phrases: Record<string, string> = {
  "society.card.sched_every_day": "Every day",
  "society.card.sched_every_day_at": "Every day at {0}",
  "society.card.sched_every_days": "Every {0} days",
  "society.card.sched_every_hour": "Every hour",
  "society.card.sched_every_hours": "Every {0} hours",
  "society.card.sched_every_minute": "Every minute",
  "society.card.sched_every_minutes": "Every {0} minutes",
  "society.card.sched_every_seconds": "Every {0} seconds",
  "society.card.sched_once_in": "Once, in {0}",
  "society.card.sched_on": "On {0}",
  "society.card.sched_at": "At {0}",
  "society.card.sched_at_fixed": "At a fixed time",
};

const t = (key: string) => phrases[key] ?? key;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("displayRoutineTitle", () => {
  test("strips the scheduler's agent prefix", () => {
    expect(displayRoutineTitle("[agent:Mailbox] Morning inbox")).toBe("Morning inbox");
  });

  test("leaves a bare title alone", () => {
    expect(displayRoutineTitle("Morning inbox")).toBe("Morning inbox");
  });
});

describe("describeTrigger", () => {
  test("every five hours", () => {
    expect(describeTrigger({ kind: "every", interval_seconds: 18_000 }, t)).toBe("Every 5 hours");
  });

  test("every hour is singular", () => {
    expect(describeTrigger({ type: "every", interval_seconds: 3_600 }, t)).toBe("Every hour");
  });

  test("every day", () => {
    expect(describeTrigger({ kind: "every", interval_seconds: 86_400 }, t)).toBe("Every day");
  });

  test("on an event", () => {
    expect(describeTrigger({ kind: "on_event", event_name: "pr.merged" }, t)).toBe("On pr.merged");
  });
});

describe("routineScheduleLine", () => {
  test("prefers the live trigger over a leftover sample phrase", () => {
    expect(
      routineScheduleLine({ trigger: { kind: "every", interval_seconds: 3_600 }, schedule: "daily 07:00" }, t),
    ).toBe("Every hour");
  });

  test("falls back to the sample phrase when there is no trigger", () => {
    expect(routineScheduleLine({ trigger: null, schedule: "daily 07:00" }, t)).toBe("daily 07:00");
  });
});


test("calendar display preserves the saved zone instead of converting to the viewer zone", () => {
  expect(describeTrigger({ type: "calendar", local_time: "08:00", timezone: "America/Los_Angeles" }, t))
    .toBe("Every day at 08:00 · America/Los_Angeles");
});


describe("fetchTaskEventCatalog", () => {
  test("reads the backend event catalogue used by on-event routines", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      events: [
        { name: "PullRequestMerged", fields: ["repository", "number"] },
        { name: "AnnouncementRequested", fields: ["text"] },
      ],
    }), { status: 200 })));

    await expect(fetchTaskEventCatalog()).resolves.toEqual([
      { name: "PullRequestMerged", fields: ["repository", "number"] },
      { name: "AnnouncementRequested", fields: ["text"] },
    ]);
  });

  test("rejects an unavailable catalogue so the builder can fall back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    await expect(fetchTaskEventCatalog()).rejects.toThrow("HTTP 503");
  });
});

describe("promoteAgentSkillForReview", () => {
  test("copies the learned skill through the governed draft endpoint", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ state: "draft" }), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);

    await promoteAgentSkillForReview("Scout Agent", "thumbnail-style");

    expect(fetcher).toHaveBeenCalledWith(
      "/api/society/agents/Scout%20Agent/skills/thumbnail-style/promote",
      { method: "POST" },
    );
  });

  test("rejects a promotion response that bypasses the draft state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ state: "active" }), { status: 200 })),
    );
    await expect(promoteAgentSkillForReview("scout", "thumbnail-style")).rejects.toThrow(
      "must remain draft",
    );
  });

  test("treats an existing promoted draft as ready for review", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 409 })));
    await expect(promoteAgentSkillForReview("scout", "thumbnail-style")).resolves.toBeUndefined();
  });

  test("surfaces a real promotion failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: { reason: "target_unknown" } }),
      { status: 404 },
    )));
    await expect(promoteAgentSkillForReview("scout", "missing")).rejects.toThrow("target_unknown");
  });
});
