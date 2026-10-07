import { describe, expect, test } from "vitest";
import type { SocietyAgentRow } from "@/lib/societyApi";
import { rowToAgent } from "./data";

function row(avatar: Record<string, unknown> | null, extra: Partial<SocietyAgentRow> = {}): SocietyAgentRow {
  return {
    agent_id: "agent-1", name: "Lumi", title: "", description: "", tier: "specialist",
    parent_agent_id: null, state: "active", avatar, checkpoint: "home", provider: "openai",
    model: "", effort: "", account_id: "", grant_mode: "all", grants: [], focus: [], denies: [],
    skills: null, workspace_dir: "", wiki_namespace: "", knowledge_scope: "shared",
    permission_ceiling: "ask", approval_mode: "bypass",
    approval_rules: { require_approval: [], always_allow: [] }, daily_budget_usd: 0,
    max_concurrent_runs: 1, browser_mode: "own", browser_allowed_domains: [], computer_id: null,
    runtime: "openclaw", session_id: "society:agent-1", created_ms: 0, updated_ms: 0,
    stats: { runs: 0, total_cost_usd: 0, last_active_ms: null },
    ...extra,
  } as SocietyAgentRow;
}

describe("rowToAgent", () => {
  test("a companion-only avatar keeps the chosen companion", () => {
    const companion = { shape: "triangle", color: "#b7cb78", eyes: "lines", enabled: true, sizeM: 0.5, followDistanceM: 1 };
    const agent = rowToAgent(row({ companion }));
    expect(agent.figure?.companion).toEqual(companion);
    expect(agent.figure?.contract).toBe(1);
  });

  test("the runtime comes through and defaults to Jarvis", () => {
    expect(rowToAgent(row(null)).runtime).toBe("openclaw");
    expect(rowToAgent(row(null, { runtime: undefined as never })).runtime).toBe("jarvis");
  });
});
