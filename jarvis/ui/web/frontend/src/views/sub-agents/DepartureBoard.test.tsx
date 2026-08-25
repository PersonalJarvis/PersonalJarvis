import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DepartureBoard } from "./DepartureBoard";

afterEach(cleanup);

type JarvisAgentNode = NonNullable<
  ComponentProps<typeof DepartureBoard>["agents"]
>[number];

function node(over: Partial<JarvisAgentNode> = {}): JarvisAgentNode {
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
    ...over,
  };
}

/**
 * The headline tile carrying `label`, so its number can be asserted.
 *
 * Addressed by its group role, not by its text: "Failed" is both a tile label
 * and a row status, so a plain text lookup matches two nodes as soon as one
 * agent actually failed.
 */
function tile(label: string): HTMLElement {
  return screen.getByRole("group", { name: label });
}

describe("DepartureBoard cancellation status", () => {
  it("renders cancellation distinctly and does not count it as failed", () => {
    render(<DepartureBoard agents={[node()]} />);

    const row = screen.getByRole("row", { name: /cancelled mission/i });
    expect(within(row).getByText("Cancelled")).toBeTruthy();
    expect(within(row).queryByText("Failed")).toBeNull();

    // A cancellation is a decision, not a fault: the Failed tile stays at 0
    // and the Done tile does not absorb it either.
    expect(within(tile("Failed")).getByText("0")).toBeTruthy();
    expect(within(tile("Done")).getByText("0")).toBeTruthy();
  });

  it("counts a real failure in the Failed tile", () => {
    render(
      <DepartureBoard
        agents={[node({ trace_id: "m-failed", status: "failed", utterance: "Broken mission" })]}
      />,
    );

    expect(within(tile("Failed")).getByText("1")).toBeTruthy();
    const row = screen.getByRole("row", { name: /broken mission/i });
    expect(within(row).getByText("Failed")).toBeTruthy();
  });
});

describe("DepartureBoard row click", () => {
  it("opens the run's insight page from EVERY row, with or without tool calls", () => {
    const opened: string[] = [];
    const withTools = node({
      trace_id: "m-tools",
      status: "completed",
      utterance: "Tools mission",
      tool_calls: [{ tool_name: "Read", args_preview: "x.py", started_ns: 2, status: "completed" }],
    });
    const bare = node({ trace_id: "m-bare", status: "completed", utterance: "Bare mission", started_ns: 1 });
    render(<DepartureBoard agents={[withTools, bare]} onOpen={(a) => opened.push(a.trace_id)} />);

    fireEvent.click(screen.getByRole("row", { name: "Tools mission" }));
    fireEvent.click(screen.getByRole("row", { name: "Bare mission" }));
    expect(opened).toEqual(["m-tools", "m-bare"]);

    // Nothing expands in place any more — the inline peek that only SOME
    // rows had is gone, so no tool-call detail leaks into the board.
    expect(screen.queryByText("x.py")).toBeNull();
    expect(screen.queryByRole("button", { name: /expand row/i })).toBeNull();
  });

  it("shows the archived terminal reason in the result column of a failed row", () => {
    render(
      <DepartureBoard
        agents={[
          node({
            trace_id: "m-quota",
            status: "failed",
            utterance: "Quota mission",
            error: null,
            outcome_reason: "review_time_budget_exhausted",
          }),
        ]}
      />,
    );
    const row = screen.getByRole("row", { name: /quota mission/i });
    expect(within(row).getByText("The review ran out of time")).toBeTruthy();
  });
});

describe("DepartureBoard empty state", () => {
  it("says nothing is running rather than showing an empty table", () => {
    render(<DepartureBoard agents={[]} />);

    expect(screen.getByText(/no .*-agents are running right now/i)).toBeTruthy();
    expect(screen.queryByRole("row", { name: /mission/i })).toBeNull();
  });
});

describe("DepartureBoard without a page to open", () => {
  it("keeps every row plain and unclickable, running ones included", () => {
    render(
      <DepartureBoard
        agents={[
          node({
            trace_id: "m-running",
            status: "running",
            utterance: "Audit the release notes",
            duration_ms: null,
            error: null,
            tool_calls: [
              { tool_name: "Grep", args_preview: "pattern=BUG-", started_ns: 1, status: "completed", duration_ms: 1200 },
            ],
          }),
        ]}
      />,
    );

    // The board no longer expands anything in place: a running agent's tool
    // calls live on its insight page, and without `onOpen` there is no page.
    const row = screen.getByRole("row", { name: /audit the release notes/i });
    expect(row.getAttribute("tabindex")).toBeNull();
    expect(screen.queryByText("pattern=BUG-")).toBeNull();
    expect(screen.getAllByRole("row")).toHaveLength(2); // header + the one agent
  });
});
