/**
 * The week-chart math: Monday-start weeks from the first active one, a
 * running week flagged instead of read as a drop, a shared axis across
 * series, and figures that never invent a comparison.
 */
import { describe, expect, it } from "vitest";

import type { InsightsDay } from "@/hooks/useBoardInsights";
import { niceCeil, summarize, weekDelta } from "@/views/profile/activity";

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

/** `count` consecutive days from 2026-09-14 (a Monday). */
function span(count: number, fill: (i: number) => Partial<InsightsDay>): InsightsDay[] {
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(2026, 8, 14 + i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return day(iso, fill(i));
  });
}

describe("summarize", () => {
  it("starts at the Monday of the first active week and flags the running week", () => {
    // Nothing for nine days, then one chat a day from Wednesday 2026-09-23.
    const days = span(22, (i) => (i >= 9 ? { chat_messages: 1 } : {}));
    const s = summarize(days, "all");

    expect(s.weeks).toHaveLength(3);
    expect(s.weeks[0].start.getDate()).toBe(21);
    expect(s.weeks[0].total).toBe(5);
    expect(s.weeks[0].partial).toBe(false);
    expect(s.weeks[1].total).toBe(7);
    expect(s.weeks[2].total).toBe(1);
    expect(s.weeks[2].partial).toBe(true);
  });

  it("keeps the axis when the series changes", () => {
    const days = span(14, (i) => (i < 7 ? { agent_sessions: 3 } : { chat_messages: 2 }));
    expect(summarize(days, "chats").weeks).toHaveLength(2);
    expect(summarize(days, "agents").weeks).toHaveLength(2);
    expect(summarize(days, "chats").weeks[0].total).toBe(0);
  });

  it("counts voice as sessions plus dictations", () => {
    const days = span(7, () => ({ voice_sessions: 1, dictations: 2, chat_messages: 5 }));
    expect(summarize(days, "voice").last7).toBe(21);
    expect(summarize(days, "all").last7).toBe(56);
  });

  it("averages finished weeks only and names the busiest weekday", () => {
    const days = span(15, (i) => ({ chat_messages: i % 7 === 2 ? 10 : 1 }));
    const s = summarize(days, "chats");
    expect(s.avgWeek).toBe(16);
    expect(s.topWeekday).toBe(2);
  });

  it("has no figures for an empty history", () => {
    const s = summarize(span(10, () => ({})), "all");
    expect(s.weeks).toHaveLength(0);
    expect(s.avgWeek).toBeNull();
    expect(s.topWeekday).toBeNull();
  });
});

describe("niceCeil", () => {
  it("rounds up to 1, 2, 2.5 or 5 times a power of ten", () => {
    expect(niceCeil(0)).toBe(1);
    expect(niceCeil(7)).toBe(10);
    expect(niceCeil(18)).toBe(20);
    expect(niceCeil(230)).toBe(250);
    expect(niceCeil(431)).toBe(500);
    expect(niceCeil(1001)).toBe(2000);
  });
});

describe("weekDelta", () => {
  it("is a whole percent, and null without a week to compare", () => {
    expect(weekDelta(9, 7)).toBe(29);
    expect(weekDelta(5, 10)).toBe(-50);
    expect(weekDelta(4, 0)).toBeNull();
  });
});
