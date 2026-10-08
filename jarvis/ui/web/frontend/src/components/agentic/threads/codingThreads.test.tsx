import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EMPTY_TIMELINE, reduceEvents, type InternalMessageItem, type UserItem } from "@/components/agentchat/reduce";
import { AgentMessageActivity, CodingThreadActivity } from "@/components/society/chat/ChatActivity";
import { useEventStore } from "@/store/events";
import { useIdeThreadsStore } from "@/store/ideThreads";
import { codingThreadOf, foldRepeatedThreadStatus } from "./openCodingThread";
import { ThreadTimeline } from "./ThreadTimeline";

afterEach(cleanup);

describe("threads a Jarvis agent started", () => {
  it("keeps who wrote a message on the person's behalf", () => {
    const [item] = reduceEvents(EMPTY_TIMELINE, [
      { seq: 1, ts_ms: 1, kind: "user_message", payload: { text: "Add a login page", author: { agent_id: "nova", name: "Nova" } } },
    ]).items as UserItem[];
    expect(item.author).toEqual({ agentId: "nova", name: "Nova" });
    const [plain] = reduceEvents(EMPTY_TIMELINE, [
      { seq: 1, ts_ms: 1, kind: "user_message", payload: { text: "typed by the person" } },
    ]).items as UserItem[];
    expect(plain.author).toBeUndefined();
  });

  it("names the agent above its message in the thread", () => {
    const item: UserItem = { type: "user", id: "u1", text: "Add a login page", attachments: [], author: { agentId: "nova", name: "Nova" }, tsMs: 1 };
    render(<ThreadTimeline items={[item]} sessionId="s1" bottomInset={0} />);
    expect(screen.getByTestId("thread-message-author").textContent).toContain("Nova");
    expect(screen.getByText("Add a login page")).toBeTruthy();
  });

  it("opens the thread in the Agentic IDE from the agent's chat", () => {
    render(<CodingThreadActivity label="Started a Claude Code thread · Login page" threadId="thread-1" />);
    fireEvent.click(screen.getByTestId("coding-thread-activity"));
    expect(useIdeThreadsStore.getState().layout).toBe("threads");
    expect(useIdeThreadsStore.getState().selection.sessionId).toBe("thread-1");
    expect(useEventStore.getState().activeSection).toBe("agentic-ide");
  });

  it("shows a thread's report as a link to the thread, not as a teammate message", () => {
    const item: InternalMessageItem = {
      type: "internal", id: "m1", tsMs: 1,
      message: {
        message_id: "m1", sender_id: "coding-thread:thread-2", sender_name: "Anthropic Claude · Login page",
        sender_kind: "agent", text: "Anthropic Claude finished in “Login page”.", prompt: "", trace_id: "t",
        status: "delivered", turn_id: "", error: "",
      },
    };
    render(<AgentMessageActivity item={item} roster={[]} />);
    expect(screen.getByTestId("coding-thread-activity").textContent).toContain("finished");
    expect(codingThreadOf(item.message.sender_id)).toBe("thread-2");
  });

  it("shows an unchanged thread status once and keeps every new state", () => {
    const status = (id: string, thread: string, text: string): InternalMessageItem => ({
      type: "internal", id, tsMs: 1,
      message: {
        message_id: id, sender_id: `coding-thread:${thread}`, sender_name: "Claude Code · Traces",
        sender_kind: "agent", text, prompt: "", trace_id: "t", status: "delivered", turn_id: "", error: "",
      },
    });
    const waits = "Claude Code waits for an approval in “Traces”.";
    const person: UserItem = { type: "user", id: "u1", text: "Is it done?", attachments: [], tsMs: 2 };
    const items = [
      status("m1", "a", waits),
      person,
      status("m2", "a", waits),
      status("m3", "b", waits),
      status("m4", "a", "Claude Code finished in “Traces”."),
      status("m5", "a", waits),
    ];
    expect(foldRepeatedThreadStatus(items).map((item) => item.id)).toEqual(["m1", "u1", "m3", "m4", "m5"]);
    const plain = [status("m1", "a", waits), person];
    expect(foldRepeatedThreadStatus(plain)).toBe(plain);
  });
});
