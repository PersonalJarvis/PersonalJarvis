import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EMPTY_TIMELINE, reduceEvents } from "@/components/agentchat/reduce";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import { DelegationActivity, assignmentOf } from "./ChatActivity";

let seq = 0;
function notice(payload: Record<string, unknown>): AgentChatEvent {
  seq += 1;
  return { seq, ts_ms: 1_000 + seq, kind: "notice", payload };
}

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, fill: (text: string) => text }));
afterEach(cleanup);

const FRAMED = [
  "[assignment from jarvis]",
  "Find the best VPS under 10 EUR.",
  "When you are done, end with a handoff: what is done, where the output is, ...",
  "Message id: 42; sender id: jarvis",
].join("\n");

describe("one chat per agent", () => {
  it("reads a framed assignment as a delegation, not as the person's message", () => {
    expect(assignmentOf(FRAMED)).toEqual({ sender: "jarvis", task: "Find the best VPS under 10 EUR." });
    expect(assignmentOf("Find me a VPS")).toBeNull();
    expect(assignmentOf("Please read: [assignment from jarvis]\nx")).toBeNull();
  });

  it("folds the delegation card and opens the task on demand", () => {
    render(<DelegationActivity sender="jarvis" task="Find the best VPS under 10 EUR." roster={[]} />);
    const toggle = screen.getByRole("button");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent).toContain("Find the best VPS");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("shows a waiting message only while it waits", () => {
    const waiting = reduceEvents(EMPTY_TIMELINE, [
      notice({ kind: "message_queued", queue_id: "q1", text: "and this" }),
    ]);
    expect(waiting.items.map((item) => item.type === "notice" && item.kind)).toEqual(["message_queued"]);
    const sent = reduceEvents(waiting, [
      notice({ kind: "message_dequeued", queue_id: "q1", status: "sent" }),
    ]);
    expect(sent.items).toHaveLength(0);
    const failed = reduceEvents(waiting, [
      notice({ kind: "message_dequeued", queue_id: "q1", status: "failed", text: "refused" }),
    ]);
    expect(failed.items.map((item) => item.type === "notice" && item.kind)).toEqual(["message_dequeued"]);
  });
});
