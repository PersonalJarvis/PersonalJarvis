import { describe, expect, test } from "vitest";

import {
  describeTrigger,
  displayRoutineTitle,
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
