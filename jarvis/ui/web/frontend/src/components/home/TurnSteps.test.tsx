import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TurnSteps, formatThoughtDuration, traceWorthShowing } from "@/components/home/TurnSteps";
import type { ThinkingStep } from "@/lib/thinkingSteps";

function step(over: Partial<ThinkingStep> & Pick<ThinkingStep, "id" | "kind">): ThinkingStep {
  return {
    labelKey: over.kind === "tool" ? "thinking.step_tool" : "thinking.step_brain",
    status: "done",
    startedTs: 0,
    durationMs: 1200,
    ...over,
  };
}

const FINISHED: ThinkingStep[] = [
  step({ id: "a", kind: "brain", detail: "openai · gpt-5" }),
  step({ id: "b", kind: "tool", detail: "gmail_search", durationMs: 820 }),
  step({ id: "c", kind: "tool", detail: "run_shell", status: "error", durationMs: 300 }),
  step({ id: "d", kind: "note", labelKey: "thinking.step_update", detail: "Found 3 mails" }),
];

describe("TurnSteps", () => {
  afterEach(() => cleanup());

  it("renders nothing for a finished turn without steps, but a live header with none", () => {
    const { container, rerender } = render(<TurnSteps steps={[]} />);
    expect(container.querySelector('[data-testid="turn-steps"]')).toBeNull();
    rerender(<TurnSteps steps={[]} live durationMs={3000} />);
    const root = screen.getByTestId("turn-steps");
    expect(root.getAttribute("data-live")).toBe("true");
    expect(screen.getByTestId("turn-steps-toggle").textContent).toContain("Thinking");
    expect(screen.getByTestId("turn-steps-toggle").textContent).toContain("3s");
  });

  it("starts folded when finished: header with the duration, tool rows still visible", () => {
    render(<TurnSteps steps={FINISHED} durationMs={10_400} />);
    const toggle = screen.getByTestId("turn-steps-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent).toContain("10s");

    const list = screen.getByTestId("turn-steps-list");
    expect(list.getAttribute("data-folded")).toBe("true");
    const rows = within(list).getAllByTestId("turn-step");
    expect(rows.map((r) => r.getAttribute("data-kind"))).toEqual(["tool", "tool"]);
    // Non-tool rows are behind the fold (labels resolve through the real EN locale).
    expect(screen.queryByText("Reasoning")).toBeNull();
  });

  it("unfolds to every row and folds back", () => {
    render(<TurnSteps steps={FINISHED} durationMs={10_400} />);
    fireEvent.click(screen.getByTestId("turn-steps-toggle"));
    expect(screen.getByTestId("turn-steps-toggle").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByTestId("turn-step")).toHaveLength(4);
    expect(screen.getByText("Reasoning")).toBeTruthy();
    expect(screen.getByText("Found 3 mails")).toBeTruthy();

    fireEvent.click(screen.getByTestId("turn-steps-toggle"));
    expect(screen.getAllByTestId("turn-step")).toHaveLength(2);
  });

  it("renders tool rows with the brand tile, the humanised label and the raw tool name", () => {
    render(<TurnSteps steps={FINISHED} durationMs={10_400} />);
    const [gmail, shell] = screen.getAllByTestId("turn-step");

    const gmailTile = within(gmail).getByTestId("turn-step-brand");
    expect(gmailTile.getAttribute("data-brand-tier")).toBe("logo");
    expect(gmailTile.querySelector("img")?.getAttribute("src")).toMatch(/svg/);
    expect(gmail.textContent).toContain("Gmail · search");
    expect(gmail.textContent).toContain("gmail_search");
    expect(gmail.textContent).toContain("0.8s");

    const shellTile = within(shell).getByTestId("turn-step-brand");
    expect(shellTile.getAttribute("data-brand-tier")).toBe("monogram");
    expect(shellTile.textContent).toBe("RS");
    expect(shell.getAttribute("data-status")).toBe("error");
    // The key may or may not be in the locale yet; both forms contain "failed".
    expect(shell.textContent).toMatch(/failed/i);
  });

  it("is open in live mode and spins on the active row", () => {
    const live: ThinkingStep[] = [
      step({ id: "a", kind: "brain", durationMs: 900 }),
      step({ id: "b", kind: "tool", detail: "github_issues", status: "active", durationMs: undefined }),
    ];
    render(<TurnSteps steps={live} live durationMs={2_500} />);
    expect(screen.getByTestId("turn-steps-toggle").getAttribute("aria-expanded")).toBe("true");
    const rows = screen.getAllByTestId("turn-step");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByTestId("turn-step-spinner")).toBeTruthy();
    expect(within(rows[0]).queryByTestId("turn-step-spinner")).toBeNull();
  });

  it("honours defaultOpen over the mode default", () => {
    render(<TurnSteps steps={FINISHED} durationMs={1000} defaultOpen />);
    expect(screen.getAllByTestId("turn-step")).toHaveLength(4);
  });
});

describe("formatThoughtDuration", () => {
  it("rounds to whole seconds and never shows 0s for a real turn", () => {
    expect(formatThoughtDuration(0)).toBe("0s");
    expect(formatThoughtDuration(400)).toBe("1s");
    expect(formatThoughtDuration(10_400)).toBe("10s");
    expect(formatThoughtDuration(65_000)).toBe("1m 05s");
  });
});

describe("traceWorthShowing", () => {
  const brain: ThinkingStep = {
    id: "b1", kind: "brain", labelKey: "thinking.step_brain", status: "done", startedTs: 0, durationMs: 200,
  };
  const tool: ThinkingStep = {
    id: "t1", kind: "tool", labelKey: "thinking.step_tool", detail: "gmail_search", status: "done", startedTs: 0, durationMs: 400,
  };

  it("hides a finished sub-second brain-only turn — no 'Thought for 0s'", () => {
    expect(traceWorthShowing([brain], 200, false)).toBe(false);
    expect(traceWorthShowing([], 0, false)).toBe(false);
    const { container } = render(<TurnSteps steps={[brain]} durationMs={200} />);
    expect(container.firstChild).toBeNull();
  });

  it("keeps turns with a tool, a real thinking time, or that are still live", () => {
    expect(traceWorthShowing([brain, tool], 200, false)).toBe(true);
    expect(traceWorthShowing([brain], 3200, false)).toBe(true);
    expect(traceWorthShowing([], 0, true)).toBe(true);
  });
});
