import { describe, expect, it } from "vitest";
import { selectTaskRows } from "./rows";
import type { SubAgentNode } from "@/store/subAgents";

function node(
  partial: Partial<SubAgentNode> & { trace_id: string },
): SubAgentNode {
  return {
    kind: "openclaw",
    name: "Sub-Agent",
    status: "running",
    parent_trace_id: null,
    started_ns: 0,
    cost_usd: 0,
    tokens_in: 0,
    tokens_out: 0,
    context_hints: [],
    prompts: [],
    tool_calls: [],
    children_trace_ids: [],
    review_iterations: 0,
    depth: 0,
    ui_appeared_at: 0,
    ...partial,
  };
}

describe("selectTaskRows", () => {
  it("collapses a mission and its worker into a single row", () => {
    // Regression for the 'one task spawns two rows' board bug: the store holds
    // a mission node ("Sub-Agent") plus its worker child ("Worker").
    const all = {
      m1: node({ trace_id: "m1", name: "Sub-Agent", started_ns: 100 }),
      w1: node({
        trace_id: "w1",
        name: "Worker",
        kind: "harness",
        parent_trace_id: "m1",
        started_ns: 200,
      }),
    };
    const rows = selectTaskRows(all);
    expect(rows).toHaveLength(1);
    expect(rows[0].trace_id).toBe("m1");
    expect(rows[0].name).toBe("Sub-Agent");
  });

  it("keeps an orphaned worker whose mission already faded out", () => {
    const all = {
      w1: node({
        trace_id: "w1",
        name: "Worker",
        kind: "harness",
        parent_trace_id: "m-removed",
        started_ns: 200,
      }),
    };
    expect(selectTaskRows(all)).toHaveLength(1);
  });

  it("shows independent tasks as separate rows, newest first", () => {
    const all = {
      m1: node({ trace_id: "m1", started_ns: 100 }),
      m2: node({ trace_id: "m2", started_ns: 300 }),
      w1: node({
        trace_id: "w1",
        kind: "harness",
        parent_trace_id: "m1",
        started_ns: 150,
      }),
    };
    expect(selectTaskRows(all).map((r) => r.trace_id)).toEqual(["m2", "m1"]);
  });

  it("handles null, undefined and empty input", () => {
    expect(selectTaskRows(null)).toEqual([]);
    expect(selectTaskRows(undefined)).toEqual([]);
    expect(selectTaskRows({})).toEqual([]);
  });
});
