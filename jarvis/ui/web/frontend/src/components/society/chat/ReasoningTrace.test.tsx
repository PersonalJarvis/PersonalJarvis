import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ReasoningTrace } from "@/components/society/chat/AgentChatPanel";
import type { ReasoningBlock } from "@/components/agentchat/reduce";

/**
 * The agent card's reasoning trace — what the maintainer asked for on
 * 2026-09-02: the bare `<details>` summary over a wall of pre-wrapped text
 * was replaced by the Claude/ChatGPT shape — a centred live line, a centred
 * "Thought for 8s" pill, and the thought itself as prose.
 *
 * The rules this file holds: the trace is CENTRED, it renders Markdown as
 * prose rather than as marks, it stays open while the turn still works and
 * folds when the turn lands, and a redacted thought still says it happened
 * without pretending there is something to open.
 */

function block(over: Partial<ReasoningBlock> = {}): ReasoningBlock {
  return {
    kind: "reasoning",
    id: "r1",
    text: "**Plan:** add the minutes first, then the two stops.",
    durationMs: 8_000,
    live: false,
    startedMs: 1_000,
    ...over,
  };
}

afterEach(cleanup);

describe("society reasoning trace", () => {
  it("centres itself instead of hanging off the bubble's edge", () => {
    render(<ReasoningTrace block={block()} turnLive={false} />);
    expect(screen.getByTestId("society-reasoning").className).toContain("items-center");
  });

  it("shows the live core and a running clock while the thought streams", () => {
    // `startedMs` is a wall clock, so a live block has to start about now.
    render(<ReasoningTrace block={block({ live: true, durationMs: null, startedMs: Date.now() })} turnLive />);
    const trace = screen.getByTestId("society-reasoning");
    expect(trace.dataset.state).toBe("live");
    expect(screen.getByTestId("live-core")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/Thinking for \d+s/);
    // A live thought is open, and anchored to its newest lines.
    expect(screen.getByTestId("society-reasoning-tail")).toBeTruthy();
  });

  it("reads the thought as prose, not as Markdown marks", () => {
    render(<ReasoningTrace block={block()} turnLive />);
    expect(screen.getByText("Plan:").tagName).toBe("STRONG");
    expect(screen.queryByText(/\*\*Plan/)).toBeNull();
  });

  it("stays open while the turn still works and folds once it lands", () => {
    const { rerender } = render(<ReasoningTrace block={block()} turnLive />);
    expect(screen.getByTestId("society-reasoning").dataset.state).toBe("open");
    rerender(<ReasoningTrace block={block()} turnLive={false} />);
    expect(screen.getByTestId("society-reasoning").dataset.state).toBe("folded");
  });

  it("names the duration on the folded pill and opens on a click", () => {
    render(<ReasoningTrace block={block()} turnLive={false} />);
    const pill = screen.getByRole("button", { name: /Thought for 8s/ });
    expect(pill.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(pill);
    expect(screen.getByTestId("society-reasoning").dataset.state).toBe("open");
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
  });

  it("says a redacted thought happened without offering a fold", () => {
    render(<ReasoningTrace block={block({ text: "" })} turnLive={false} />);
    const trace = screen.getByTestId("society-reasoning");
    expect(trace.dataset.state).toBe("silent");
    const pill = screen.getByRole("button", { name: /Thought for 8s/ });
    expect((pill as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(pill);
    expect(screen.getByTestId("society-reasoning").dataset.state).toBe("silent");
  });

  it("falls back to a bare label when the vendor sent no duration", () => {
    render(<ReasoningTrace block={block({ durationMs: null, text: "" })} turnLive={false} />);
    expect(screen.getByRole("button").textContent).toBe("Thought");
  });
});
