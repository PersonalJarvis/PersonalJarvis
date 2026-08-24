import { describe, expect, it } from "vitest";

import type { MissionSummary } from "@/types/missions";
import type { SubAgentNode } from "@/store/jarvisAgents";
import { mergeBoardRows, missionToNode, normalizeTraceId } from "./missionRows";

function mission(over: Partial<MissionSummary> = {}): MissionSummary {
  return {
    id: "01a02ec4-a894-7360-a3d8-8a7c09ec0eda",
    prompt: "Audit the release notes",
    state: "APPROVED",
    language: "de",
    created_ms: 1_787_491_035_284,
    updated_ms: 1_787_491_261_559,
    iteration: 0,
    cost_usd: 0.42,
    ...over,
  } as MissionSummary;
}

function liveNode(over: Partial<SubAgentNode> = {}): SubAgentNode {
  return {
    trace_id: "aaaa",
    kind: "jarvis_agent",
    name: "Assistant-Agent",
    status: "running",
    parent_trace_id: null,
    started_ns: 5_000_000_000_000_000,
    completed_ns: null,
    duration_ms: null,
    cost_usd: 0,
    tokens_in: 0,
    tokens_out: 0,
    utterance: "Live run",
    context_hints: [],
    prompts: [],
    tool_calls: [],
    children_trace_ids: [],
    error: null,
    error_class: null,
    review_iterations: 0,
    depth: 0,
    ui_appeared_at: 1,
    ...over,
  };
}

describe("missionToNode", () => {
  it("carries the task, the cost and the wall-clock duration", () => {
    const node = missionToNode(mission());
    expect(node.utterance).toBe("Audit the release notes");
    expect(node.cost_usd).toBe(0.42);
    expect(node.duration_ms).toBe(1_787_491_261_559 - 1_787_491_035_284);
    expect(node.started_ns).toBe(1_787_491_035_284 * 1_000_000);
  });

  it.each([
    ["PENDING", "running"],
    ["RUNNING", "running"],
    ["CRITIQUING", "running"],
    ["LOOPING", "running"],
    ["APPROVED", "completed"],
    ["FAILED", "failed"],
    ["TIMED_OUT", "failed"],
    ["CANCELLED", "cancelled"],
  ])("maps mission state %s to board status %s", (state, expected) => {
    expect(missionToNode(mission({ state: state as MissionSummary["state"] })).status).toBe(
      expected,
    );
  });

  it("gives a still-running mission no duration, so the board counts up itself", () => {
    const node = missionToNode(mission({ state: "RUNNING" as MissionSummary["state"] }));
    expect(node.duration_ms).toBeNull();
    expect(node.completed_ns).toBeNull();
  });

  it("strips dashes so the id matches a registry trace id", () => {
    expect(missionToNode(mission()).trace_id).toBe("01a02ec4a8947360a3d88a7c09ec0eda");
    expect(normalizeTraceId("a-b-c")).toBe("abc");
  });
});

describe("mergeBoardRows", () => {
  it("keeps the live node for a run that is in both sources", () => {
    const live = liveNode({
      trace_id: "01a02ec4a8947360a3d88a7c09ec0eda",
      tool_calls: [
        { tool_name: "Grep", args_preview: "x", started_ns: 1, status: "completed" },
      ],
    });

    const rows = mergeBoardRows([live], [mission()]);

    expect(rows).toHaveLength(1);
    // The live one wins: only it carries the tool calls the drilldown needs.
    expect(rows[0].tool_calls).toHaveLength(1);
    expect(rows[0].status).toBe("running");
  });

  it("adds past runs the live registry has already forgotten", () => {
    const rows = mergeBoardRows(
      [],
      [
        mission({ id: "11111111-1111-1111-1111-111111111111", created_ms: 1000 }),
        mission({ id: "22222222-2222-2222-2222-222222222222", created_ms: 2000 }),
      ],
    );

    expect(rows).toHaveLength(2);
    // Newest first, across both sources.
    expect(rows[0].started_ns).toBeGreaterThan(rows[1].started_ns);
  });

  it("sorts a fresh live run above an older past one", () => {
    const rows = mergeBoardRows(
      [liveNode({ trace_id: "live", started_ns: 9_000_000_000_000_000 })],
      [mission({ created_ms: 1000 })],
    );
    expect(rows[0].trace_id).toBe("live");
  });

  it("caps the history so the board stays a board, not an archive", () => {
    const many = Array.from({ length: 120 }, (_, i) =>
      mission({ id: `${i}`.padStart(8, "0") + "-0000-0000-0000-000000000000", created_ms: i }),
    );
    expect(mergeBoardRows([], many, 50)).toHaveLength(50);
  });

  it("renders nothing when neither source has anything", () => {
    expect(mergeBoardRows([], [])).toEqual([]);
  });
});
