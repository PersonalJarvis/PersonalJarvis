import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import { AgentTimeline } from "./AgentTimeline";
import { EMPTY_TIMELINE, reduceEvents } from "./reduce";

/** A finished, answered turn folds its work behind "Thought for …"; open every fold. */
const openWork = () => {
  for (const toggle of Array.from(document.querySelectorAll<HTMLElement>("[data-testid='conversation-work-fold'][data-open='false'] > button"))) fireEvent.click(toggle);
};
import { attachTurnMessages } from "./turnMessages";

let seq = 0;
const ev = (kind: string, payload: Record<string, unknown>, tsMs: number): AgentChatEvent =>
  ({ seq: ++seq, ts_ms: tsMs, kind, payload }) as AgentChatEvent;
afterEach(() => { cleanup(); seq = 0; });

/** The shape of the 2026-10-03 audit turn: each reply arrived as an echo notice
 *  plus the message, a wrap-up repeated them, and the answer came last. */
function auditTurn(): AgentChatEvent[] {
  return [
    ev("user_message", { text: "Run a new audit" }, 1000),
    ev("turn_started", { turn_id: "t1", provider: "claude-api", model: "m", runner: "claude-cli", surface: "jarvis" }, 1001),
    ev("tool_call", { turn_id: "t1", call_id: "c1", name: "mcp__jarvis__society_status", input: {} }, 1100),
    ev("tool_result", { turn_id: "t1", call_id: "c1", output: "{}" }, 1200),
    ev("notice", { kind: "society_message", agent_name: "X markring", status: "done", text: "Reply for Ruben, as of 3 Oct 2026" }, 2000),
    ev("agent_message", { message_id: "m1", sender_name: "X markring", sender_kind: "agent", text: "Reply for Ruben, as of 3 Oct 2026", status: "queued" }, 2001),
    ev("agent_message_status", { message_id: "m1", status: "delivered" }, 2002),
    ev("assistant_text", { turn_id: "t1", message_id: "a1", text: "Two agents have answered so far." }, 3000),
    ev("notice", { kind: "delegation_report", turn_id: "t1", agent_name: "X markring", status: "done", text: "Reply for Ruben, as of 3 Oct 2026" }, 4000),
    ev("notice", { kind: "delegation_report", turn_id: "t1", agent_name: "Jarvis-Scout", status: "done", text: "Scout audit, read-only" }, 4001),
    ev("assistant_text", { turn_id: "t1", message_id: "a2", text: "All agents have answered." }, 5000),
    ev("turn_finished", { turn_id: "t1", status: "done", duration_ms: 4100, usage: {} }, 5100),
  ];
}

describe("agent messages inside the turn that asked for them", () => {
  it("moves each answer into its turn once, dropping the echo notices", () => {
    const { items, messagesByTurn } = attachTurnMessages(reduceEvents(EMPTY_TIMELINE, auditTurn()).items);
    expect(items.map((item) => item.type)).toEqual(["user", "turn"]);
    expect(messagesByTurn.get(items[1].id)?.map((m) => m.name)).toEqual(["X markring", "Jarvis-Scout"]);
  });

  it("keeps a message outside any turn where it is, still without its echo", () => {
    const { items } = attachTurnMessages(reduceEvents(EMPTY_TIMELINE, [
      ev("notice", { kind: "society_message", agent_name: "Scout", status: "done", text: "Hi" }, 100),
      ev("agent_message", { message_id: "m9", sender_name: "Scout", sender_kind: "agent", text: "Hi", status: "delivered" }, 101),
    ]).items);
    expect(items.map((item) => item.type)).toEqual(["internal"]);
  });

  it("draws the answers as quiet trace lines and keeps Jarvis's reply last", () => {
    const { container } = render(<AgentTimeline items={reduceEvents(EMPTY_TIMELINE, auditTurn()).items} assistantName="Jarvis"
      providerLabel={(id) => id} onDecide={() => undefined} bubbles traceLook="rail" />);
    openWork();
    // No full-text cards: one line per answer, inside the turn.
    const lines = Array.from(container.querySelectorAll<HTMLElement>("[data-trace-entry='message']"));
    expect(lines.map((line) => line.textContent)).toEqual([
      expect.stringContaining("X markring"), expect.stringContaining("Jarvis-Scout"),
    ]);
    expect(container.querySelector("[data-testid='agent-turn'] [data-trace-entry='message']")).toBeTruthy();
    // The reply is the last words of the turn.
    const turnText = container.querySelector("[data-testid='agent-turn']")!.textContent!;
    expect(turnText.lastIndexOf("All agents have answered.")).toBeGreaterThan(turnText.lastIndexOf("Jarvis-Scout"));
    // The whole message is one tap away.
    fireEvent.click(lines[1].querySelector("button")!);
    expect(lines[1].querySelector(".prose")?.textContent).toContain("Scout audit, read-only");
  });
});
