import { describe, expect, it } from "vitest";

import type { SocietyQuestRow } from "@/lib/societyApi";

import { ageOf, boardScrolls, groupQuests, takerKind } from "./questBoard";

function row(partial: Partial<SocietyQuestRow> & Pick<SocietyQuestRow, "quest_id" | "state">): SocietyQuestRow {
  return {
    title: partial.quest_id,
    text: partial.quest_id,
    created_by: "user",
    agent_id: null,
    trace_id: `quest:${partial.quest_id}`,
    assign_event_id: null,
    run_id: "",
    routing: {},
    result: {},
    created_ms: 0,
    updated_ms: 0,
    done_ms: null,
    ...partial,
  };
}

describe("questBoard", () => {
  it("groups active first, running before assigned before open, newest first within a state", () => {
    const rows = [
      row({ quest_id: "a", state: "open", created_ms: 1 }),
      row({ quest_id: "b", state: "running", created_ms: 2 }),
      row({ quest_id: "c", state: "assigned", created_ms: 3 }),
      row({ quest_id: "d", state: "running", created_ms: 4 }),
      row({ quest_id: "e", state: "done", created_ms: 5, done_ms: 50 }),
      row({ quest_id: "f", state: "done", created_ms: 6, done_ms: 60 }),
      row({ quest_id: "g", state: "failed", created_ms: 7 }),
      row({ quest_id: "h", state: "cancelled", created_ms: 8 }),
    ];
    const g = groupQuests(rows);
    expect(g.active.map((r) => r.quest_id)).toEqual(["d", "b", "c", "a"]);
    expect(g.done.map((r) => r.quest_id)).toEqual(["f", "e"]);
    expect(g.failed.map((r) => r.quest_id)).toEqual(["g"]);
    expect(g.cancelled.map((r) => r.quest_id)).toEqual(["h"]);
  });

  it("puts only active quests on the monument, oldest first, capped", () => {
    const rows = Array.from({ length: 12 }, (_, i) =>
      row({ quest_id: `q${i}`, state: i % 4 === 0 ? "done" : "open", created_ms: 100 - i }),
    );
    const scrolls = boardScrolls(rows, 5);
    expect(scrolls).toHaveLength(5);
    expect(scrolls.every((r) => r.state === "open")).toBe(true);
    expect(scrolls[0].created_ms).toBeLessThan(scrolls[4].created_ms);
  });

  it("ages coarsely", () => {
    expect(ageOf(1000, 1000 + 30_000)).toEqual({ unit: "now", n: 0 });
    expect(ageOf(0, 5 * 60_000)).toEqual({ unit: "min", n: 5 });
    expect(ageOf(0, 3 * 3_600_000)).toEqual({ unit: "h", n: 3 });
    expect(ageOf(0, 72 * 3_600_000)).toEqual({ unit: "d", n: 3 });
  });

  it("names the taker kind from the routing record", () => {
    expect(takerKind(row({ quest_id: "x", state: "open" }))).toBe("none");
    expect(takerKind(row({ quest_id: "x", state: "running", agent_id: "mailbox" }))).toBe("taken");
    expect(takerKind(row({ quest_id: "x", state: "running", agent_id: "mailbox", routing: { forged: true } }))).toBe(
      "forged",
    );
  });
});
