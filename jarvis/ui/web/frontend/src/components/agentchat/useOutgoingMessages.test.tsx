import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { SocietyEnvelope } from "@/lib/societyApi";
import { collectOutgoing, mergeOutgoingMessages, useOutgoingMessages } from "./useOutgoingMessages";
import { InternalMessageBubble } from "./InternalMessageBubble";
import { PairConversationBoundary } from "./PairConversation";
import type { InternalMessageItem, TimelineItem } from "./reduce";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/hooks/useStickToBottom", () => ({ useStickToBottom: () => ({ rootRef: { current: null }, contentRef: { current: null }, atEnd: true, jumpToEnd: () => {}, follow: () => {} }) }));
const names = new Map([["jarvis", "Jarvis"], ["nala", "Nala"], ["scout", "Scout"]]);
function row(seq: number, from = "nala", to: string | null = "jarvis", kind: SocietyEnvelope["msg_type"] = "ANSWER"): SocietyEnvelope {
  return { seq, event_id: `m${seq}`, msg_type: kind, from_agent: from, to_agent: to,
    trace_id: "t", parent_event_id: null, ts_ms: seq * 1000, cost_usd: 0, payload: { text: `Reply ${seq}` } };
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each(["nala", "scout"])("shows %s's outgoing replies in chronological order without duplicates", (id) => {
  const original: TimelineItem[] = [{ type: "user", id: "u", text: "Hello", tsMs: 500, attachments: [] },
    { type: "notice", id: "n", kind: "done", text: "Done", agentId: id, agentName: id, status: "done", tsMs: 3000, data: {}, resolved: "" }];
  const events = collectOutgoing([], [row(1, id), row(2, id, "jarvis", "SAY"), row(3, "unrelated"), row(4, id, null), row(5, id, "jarvis", "RESULT")], id);
  const items = mergeOutgoingMessages(original, events, id, names.get(id)!, names);
  expect(items.map((item) => item.id)).toEqual(["u", "m1", "m2", "n"]);
  expect((items[1] as InternalMessageItem).outgoing).toEqual({ recipientId: "jarvis", recipientName: "Jarvis" });
  expect(mergeOutgoingMessages(items, events, id, id, names)).toBe(items);
  expect(original).toHaveLength(2);
});

it("updates an outgoing receipt on a delivery veto without inventing delivered status", () => {
  const sent = collectOutgoing([], [row(1)], "nala");
  const veto = { ...row(2, "scheduler", "nala", "VETO"), parent_event_id: "m1", payload: { text: "Target paused" } };
  const events = collectOutgoing(sent, [veto], "nala");
  const item = mergeOutgoingMessages([], events, "nala", "Nala", names)[0] as InternalMessageItem;
  expect(item.message.status).toBe("failed");
  render(<InternalMessageBubble item={item} />);
  expect(screen.getByText("Target paused")).toBeTruthy();
  expect(screen.getByText("agent_chat.delivery_failed")).toBeTruthy();
});

it("opens the recipient conversation from an outgoing card and preserves the draft", async () => {
  vi.stubGlobal("fetch", async () => ({ ok: true, json: async () => ({ events: [row(1)] }) }));
  const item = mergeOutgoingMessages([], [row(1)], "nala", "Nala", names)[0] as InternalMessageItem;
  render(<PairConversationBoundary recipient={{ id: "nala", name: "Nala" }}>
    <InternalMessageBubble item={item} /><input aria-label="Draft" defaultValue="Keep this" />
  </PairConversationBoundary>);
  expect(screen.getByText("Nala")).toBeTruthy();
  expect(screen.getByText("Jarvis")).toBeTruthy();
  expect(screen.getByText("agent_chat.delivery_sent")).toBeTruthy();
  expect(screen.queryByText("agent_chat.delivery_delivered")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "agent_chat.pair_open" }));
  const conversation = await screen.findByTestId("agent-pair-conversation");
  await within(conversation).findByText("Reply 1");
  fireEvent.click(screen.getAllByRole("button", { name: "agent_chat.pair_close" })[0]);
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Keep this");
});

it("pages the archive, polls new replies, and ignores late data from a previous agent", async () => {
  const calls: string[] = [];
  let late: ((value: unknown) => void) | undefined;
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    if (calls.length === 3) return new Promise((resolve) => { late = resolve; });
    return { ok: true, json: async () => ({ events: url.includes("after_seq=0&")
      ? Array.from({ length: 1000 }, (_, index) => row(index + 1, index === 0 ? "nala" : "other"))
      : [row(1001)] }) };
  });
  const { result, rerender } = renderHook(({ id }) => useOutgoingMessages(id), { initialProps: { id: "nala" } });
  await waitFor(() => expect(result.current).toHaveLength(2));
  expect(calls[1]).toContain("after_seq=1000&");
  vi.useFakeTimers();
  // The first poll was scheduled with real timers; changing agent starts a new read.
  rerender({ id: "scout" });
  expect(result.current).toEqual([]);
  rerender({ id: "nala" });
  await act(async () => {
    late?.({ ok: true, json: async () => ({ events: [row(2000, "scout")] }) });
  });
  expect(result.current.every((event) => event.from_agent === "nala")).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(calls.at(-1)).toContain("after_seq=1001&");
  expect(result.current.map((event) => event.event_id)).toEqual(["m1", "m1001"]);
});
