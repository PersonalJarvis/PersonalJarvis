import { describe, expect, it } from "vitest";

import { mostUsedAgents, SIDEBAR_AGENTS_MAX } from "./SidebarAgents";
import type { SocietyAgent } from "@/components/society/data";

function agent(id: string, runs: number, extra: Partial<SocietyAgent> = {}): SocietyAgent {
  return {
    agentId: id,
    name: id,
    tier: "member",
    lifecycle: "active",
    createdMs: 0,
    stats: { runs, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: null },
    ...extra,
  } as SocietyAgent;
}

describe("mostUsedAgents", () => {
  it("lists the most-run agents first and stops at the cap", () => {
    const rows = mostUsedAgents([agent("a", 2), agent("b", 9), agent("c", 5), agent("d", 1)]);
    expect(rows.map((r) => r.agentId)).toEqual(["b", "c", "a"].slice(0, SIDEBAR_AGENTS_MAX));
  });

  it("leaves out Jarvis itself and archived agents", () => {
    const rows = mostUsedAgents([
      agent("lead", 99, { tier: "lead" }),
      agent("gone", 50, { lifecycle: "archived" }),
      agent("kept", 1),
    ]);
    expect(rows.map((r) => r.agentId)).toEqual(["kept"]);
  });

  it("breaks a tie by the most recent activity", () => {
    const rows = mostUsedAgents([
      agent("old", 3, { stats: { runs: 3, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: 10 } }),
      agent("new", 3, { stats: { runs: 3, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: 20 } }),
    ]);
    expect(rows.map((r) => r.agentId)).toEqual(["new", "old"]);
  });
});
