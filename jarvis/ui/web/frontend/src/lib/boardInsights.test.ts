import { describe, expect, it } from "vitest";

import type { InsightsDay } from "@/hooks/useBoardInsights";
import {
  actionsOf,
  calendarWeeks,
  intensityLevels,
  milestoneProgress,
  minutesSaved,
  peakSlot,
  trendPercent,
  weeklyTotals,
} from "@/lib/boardInsights";

function day(date: string, over: Partial<InsightsDay> = {}): InsightsDay {
  return {
    date,
    dictations: 0,
    dictation_words: 0,
    voice_sessions: 0,
    voice_words: 0,
    chat_messages: 0,
    agent_sessions: 0,
    agent_turns: 0,
    ...over,
  };
}

describe("board insights maths", () => {
  it("counts every started thing once per day", () => {
    expect(
      actionsOf(day("2026-10-01", { dictations: 3, voice_sessions: 1, chat_messages: 2, agent_sessions: 4, agent_turns: 99 })),
    ).toBe(10);
  });

  it("splits non-zero values into quartile levels and keeps zero at level 0", () => {
    const level = intensityLevels([0, 1, 2, 3, 4, 100]);
    expect(level(0)).toBe(0);
    expect(level(1)).toBe(1);
    expect(level(100)).toBe(4);
  });

  it("lays days into Monday-first weeks with month starts", () => {
    // 2026-09-30 is a Wednesday.
    // The API always sends a gap-free series.
    const dates = ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"];
    const weeks = calendarWeeks(dates.map((d) => day(d)));
    expect(weeks[0].days.slice(0, 2)).toEqual([null, null]);
    expect(weeks[0].days[2]?.date).toBe("2026-09-30");
    // The week holding the 1st of October is labelled October.
    expect(weeks[0].monthStart).toBe(9);
    expect(weeks[1].days[0]?.date).toBe("2026-10-05");
    expect(weeks[1].days.slice(1)).toEqual(Array(6).fill(null));
  });

  it("never reports negative time saved", () => {
    expect(minutesSaved(400, 60, 40)).toBe(9);
    expect(minutesSaved(10, 600, 40)).toBe(0);
  });

  it("has no trend without a previous period", () => {
    expect(trendPercent(50, 0)).toBeNull();
    expect(trendPercent(150, 100)).toBe(50);
  });

  it("finds the busiest weekday and hour", () => {
    const punch = Array.from({ length: 7 }, () => Array(24).fill(0));
    punch[2][10] = 7;
    expect(peakSlot(punch)).toEqual({ weekday: 2, hour: 10, value: 7 });
    expect(peakSlot(Array.from({ length: 7 }, () => Array(24).fill(0)))).toBeNull();
  });

  it("sums words per week", () => {
    const weeks = weeklyTotals(
      [day("2026-09-28", { dictation_words: 5 }), day("2026-09-29", { voice_words: 7 })],
      4,
    );
    expect(weeks).toEqual([{ start: "2026-09-28", words: 12, agentSessions: 0 }]);
  });

  it("brackets the word count between milestones", () => {
    expect(milestoneProgress(261_917)).toEqual({ previous: 250_000, next: 500_000 });
    expect(milestoneProgress(0)).toEqual({ previous: 0, next: 1_000 });
  });
});
