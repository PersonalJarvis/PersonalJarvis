import { describe, expect, test } from "vitest";

import { formatChatDay, groupChatRowsByDay, type ChatRow } from "@/components/home/chatRows";

const at = (y: number, m: number, d: number, h = 12, min = 0) => new Date(y, m - 1, d, h, min).getTime();
const row = (id: string, updatedMs: number): ChatRow => ({
  kind: "agent", id, title: id, preview: "", updatedMs, messageCount: 1, provider: "", raw: null,
});

describe("groupChatRowsByDay", () => {
  const now = at(2026, 10, 9, 18);

  test("one heading per calendar day, newest day and newest chat first", () => {
    const groups = groupChatRowsByDay(
      [row("oct7-am", at(2026, 10, 7, 9)), row("today", at(2026, 10, 9, 8)), row("oct7-pm", at(2026, 10, 7, 21)),
        row("yesterday", at(2026, 10, 8, 23, 50)), row("oct3", at(2026, 10, 3))],
      now,
    );
    expect(groups.map((g) => g.relative)).toEqual(["today", "yesterday", null, null]);
    expect(groups.map((g) => g.rows.map((r) => r.id))).toEqual([["today"], ["yesterday"], ["oct7-pm", "oct7-am"], ["oct3"]]);
  });

  test("a chat just before midnight and one just after land on different days", () => {
    const groups = groupChatRowsByDay([row("late", at(2026, 10, 8, 23, 59)), row("early", at(2026, 10, 9, 0, 1))], now);
    expect(groups.map((g) => g.rows[0].id)).toEqual(["early", "late"]);
  });

  test("the date carries the year only when it is not this year", () => {
    expect(formatChatDay(at(2026, 10, 7, 0), "en-US", now)).toBe("Oct 7");
    expect(formatChatDay(at(2025, 10, 7, 0), "en-US", now)).toBe("Oct 7, 2025");
  });
});
