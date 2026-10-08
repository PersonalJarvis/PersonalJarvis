import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MessengerTurn } from "./MessengerTurn";
import { actionNoticeOf, callPresence, presenceOf } from "./messengerPresence";
import type { ReasoningBlock, TextBlock, ToolBlock, TurnBlock, TurnItem } from "./reduce";

afterEach(cleanup);

function turn(blocks: TurnBlock[], status: TurnItem["status"] = "running"): TurnItem {
  return {
    type: "turn", id: "t1", provider: "p", model: "", effort: "", runner: "api", status, blocks,
    startedMs: 1, durationMs: null, usage: null, liveUsage: null, costUsd: null, error: null,
  };
}

const text = (value: string, id = "x1"): TextBlock => ({ kind: "text", id, text: value });
const thought = (value: string): ReasoningBlock => ({ kind: "reasoning", id: "r1", text: value, durationMs: null, live: true, startedMs: 1 });
function call(name: string, input: unknown, output: string | null = null, extra: Partial<ToolBlock> = {}): ToolBlock {
  return { kind: "tool", callId: `c-${name}`, name, input, output, isError: false, durationMs: null, approval: null, startedMs: 1, ...extra };
}

describe("presenceOf", () => {
  it("thinks before anything happened and while a thought runs", () => {
    expect(presenceOf(turn([]))).toBe("thinking");
    expect(presenceOf(turn([thought("planning the schedule")]))).toBe("thinking");
  });

  it("types while a message streams", () => {
    expect(presenceOf(turn([text("Checking what is already connected.")]))).toBe("typing");
  });

  it("names the running call by what it does", () => {
    expect(presenceOf(turn([call("mcp__jarvis__society_wiki_note", { title: "x" })]))).toBe("writing");
    expect(presenceOf(turn([call("society_propose_change", { kind: "rule" })]))).toBe("writing");
    expect(presenceOf(turn([call("society_propose_change", { kind: "routine" })]))).toBe("setting_up");
    expect(presenceOf(turn([call("web_search", {})]))).toBe("searching");
    expect(presenceOf(turn([call("browser_navigate", {})]))).toBe("browsing");
    expect(presenceOf(turn([call("Bash", {})]))).toBe("working");
  });

  it("goes back to thinking between steps and says nothing once done", () => {
    expect(presenceOf(turn([call("web_search", {}, "ok")]))).toBe("thinking");
    expect(presenceOf(turn([text("Done.")], "done"))).toBeNull();
  });

  it("leaves a waiting card to speak for itself", () => {
    const waiting = call("society_ask_user", {}, null, { question: { closed: false } as ToolBlock["question"] });
    expect(presenceOf(turn([waiting]))).toBeNull();
  });
});

describe("actionNoticeOf", () => {
  const routine = (operation?: string) => call("society_propose_change", {
    kind: "routine", mode: "apply", payload: { title: "Daily contributor check", ...(operation ? { operation } : {}) },
  }, "{\"applied\": true}");

  it("turns an applied routine into a created or updated line", () => {
    expect(actionNoticeOf(routine())).toEqual({ key: "routine_created", subject: "Daily contributor check", icon: "routine" });
    expect(actionNoticeOf(routine("update"))?.key).toBe("routine_updated");
    expect(actionNoticeOf(routine("delete"))?.key).toBe("routine_deleted");
  });

  it("leaves no line for a failed, pending or unremarkable call", () => {
    expect(actionNoticeOf({ ...routine(), isError: true })).toBeNull();
    expect(actionNoticeOf({ ...routine(), output: "{\"status\": \"pending\"}" })).toBeNull();
    expect(actionNoticeOf({ ...routine(), output: null })).toBeNull();
    expect(actionNoticeOf(call("web_search", {}, "results"))).toBeNull();
    expect(actionNoticeOf(call("society_propose_change", { kind: "identity" }, "ok"))).toBeNull();
  });

  it("classifies a call even behind a transport prefix", () => {
    expect(callPresence(call("mcp__jarvis__society_memory_recall", {}))).toBe("searching");
  });
});

describe("MessengerTurn", () => {
  it("draws messages as bubbles and never shows a thought", () => {
    render(<MessengerTurn
      turn={turn([thought("secret reasoning"), text("Checking it now.", "a"), routineDone(), text("It runs every day at 10:04.", "b")], "done")}
      avatar={<span data-testid="face" />} onDecide={() => undefined} />);
    expect(screen.getAllByTestId("agent-message-bubble")).toHaveLength(2);
    expect(screen.queryByText("secret reasoning")).toBeNull();
    expect(screen.getByTestId("messenger-action").textContent).toContain("Daily contributor check");
    expect(screen.queryByTestId("messenger-presence")).toBeNull();
  });

  it("shows the agent's face with what it is doing while it works", () => {
    render(<MessengerTurn turn={turn([call("society_wiki_note", { title: "x" })])}
      avatar={<span data-testid="face" />} onDecide={() => undefined} />);
    const line = screen.getByTestId("messenger-presence");
    expect(line.dataset.presence).toBe("writing");
    expect(screen.getByTestId("face")).toBeTruthy();
  });
});

function routineDone(): ToolBlock {
  return call("society_propose_change", { kind: "routine", payload: { title: "Daily contributor check" } }, "{\"applied\": true}");
}
