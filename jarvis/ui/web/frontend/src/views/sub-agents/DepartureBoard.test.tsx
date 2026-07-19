import type { ComponentProps } from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DepartureBoard } from "./DepartureBoard";

afterEach(cleanup);

type JarvisAgentNode = NonNullable<
  ComponentProps<typeof DepartureBoard>["agents"]
>[number];

function cancelledNode(): JarvisAgentNode {
  return {
    trace_id: "mission-cancelled",
    kind: "jarvis_agent",
    name: "Assistant-Agent",
    status: "cancelled",
    parent_trace_id: null,
    started_ns: 1,
    completed_ns: 2,
    duration_ms: 1,
    cost_usd: 0,
    tokens_in: 0,
    tokens_out: 0,
    utterance: "Cancelled mission",
    context_hints: [],
    prompts: [],
    tool_calls: [],
    children_trace_ids: [],
    error: "cancelled: user_cancelled",
    error_class: null,
    review_iterations: 0,
    depth: 0,
    ui_appeared_at: 1,
  };
}

describe("DepartureBoard cancellation status", () => {
  it("renders cancellation distinctly and does not count it as failed", () => {
    render(<DepartureBoard agents={[cancelledNode()]} />);

    const row = screen.getByRole("button", { name: /cancelled mission/i });
    expect(within(row).getByText("CANCELLED")).toBeTruthy();
    expect(within(row).queryByText("FAILED")).toBeNull();

    const failedMetric = screen.getByText("Failed").parentElement;
    expect(failedMetric).not.toBeNull();
    expect(within(failedMetric as HTMLElement).getByText("0")).toBeTruthy();
  });
});
