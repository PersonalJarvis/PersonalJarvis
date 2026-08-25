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
  it("opens the run's insight page from the row and keeps the chevron for the inline peek", () => {
    const opened: string[] = [];
    // A finished run with tool calls: it has a drilldown but is not
    // auto-expanded (only running rows are), so the chevron's effect is visible.
    const agent = node({
      trace_id: "m-live",
      status: "completed",
      utterance: "Live mission",
      tool_calls: [
        { tool_name: "Read", args_preview: "x.py", started_ns: 1, status: "completed" },
      ],
    });
    render(<DepartureBoard agents={[agent]} onOpen={(a) => opened.push(a.trace_id)} />);

    const row = screen.getByRole("row", { name: "Live mission" });
    expect(screen.queryByText("x.py")).toBeNull();
    fireEvent.click(row);
    expect(opened).toEqual(["m-live"]);
    expect(screen.queryByText("x.py")).toBeNull();

    // The chevron is its own control: it expands the row in place and does
    // NOT navigate — clicking it must not add a second "opened" entry.
    fireEvent.click(within(row).getByRole("button", { name: /expand row/i }));
    expect(opened).toEqual(["m-live"]);
    expect(screen.getByText("x.py")).toBeTruthy();
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

describe("DepartureBoard drilldown", () => {
  it("opens a running agent's tool calls and details inline", () => {
    render(
      <DepartureBoard
        agents={[
          node({
            trace_id: "m-running",
            status: "running",
            utterance: "Audit the release notes",
            duration_ms: null,
            error: null,
            context_hints: ["docs/CHANGELOG.md"],
            tool_calls: [
              {
                tool_name: "Grep",
                args_preview: "pattern=BUG-",
                started_ns: 1,
                output_preview: "",
                status: "completed",
                duration_ms: 1200,
              },
            ],
          }),
        ]}
      />,
    );

    // A running agent with tool calls opens itself — the operator should not
    // have to click to see what is happening right now.
    expect(screen.getByText("Grep")).toBeTruthy();
    expect(screen.getByText("pattern=BUG-")).toBeTruthy();
    expect(screen.getByText("docs/CHANGELOG.md")).toBeTruthy();
    expect(screen.getByText(/trace m-running/)).toBeTruthy();

    // The drilldown is a table row, so the grid it sits in stays valid.
    const rows = screen.getAllByRole("row");
    expect(rows.length).toBeGreaterThan(2);
  });

  it("leaves an agent with nothing to show collapsed and unclickable", () => {
    render(
      <DepartureBoard
        agents={[
          node({
            trace_id: "m-bare",
            status: "completed",
            utterance: "Nothing to drill into",
            error: null,
            prompts: [],
            tool_calls: [],
          }),
        ]}
      />,
    );

    const row = screen.getByRole("row", { name: /nothing to drill into/i });
    expect(row.getAttribute("tabindex")).toBeNull();
    expect(screen.queryByText(/trace m-bare/)).toBeNull();
  });
});
