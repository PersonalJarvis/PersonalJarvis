import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { SocietyEnvelope } from "@/lib/societyApi";
import { PairConversation, PairConversationBoundary, pairMessages } from "./PairConversation";
import { InternalMessageBubble } from "./InternalMessageBubble";
import { EMPTY_TIMELINE, reduceEvent } from "./reduce";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/hooks/useStickToBottom", () => ({ useStickToBottom: () => ({ rootRef: { current: null }, contentRef: { current: null }, atEnd: true, jumpToEnd: () => {}, follow: () => {} }) }));
function envelope(seq: number, from: string, to: string | null, text = `Message ${seq}`): SocietyEnvelope {
  return { seq, event_id: `m${seq}`, msg_type: "SAY", from_agent: from, to_agent: to, trace_id: "trace", parent_event_id: null, ts_ms: seq * 1000, cost_usd: 0, payload: { text } };
}
const pair: [{ id: string; name: string }, { id: string; name: string }] = [{ id: "jarvis", name: "Jarvis" }, { id: "nala", name: "Nala" }];
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it("isolates a pair in both directions, excluding broadcasts and third agents", () => {
  const events = [envelope(1, "jarvis", "nala"), envelope(2, "nala", "gmail"), envelope(3, "nala", "jarvis"), envelope(4, "jarvis", null), envelope(5, "user", "nala"), envelope(6, "nala", "jarvis", "")];
  expect(pairMessages(events, "jarvis", "nala").map((e) => e.seq)).toEqual([1, 3]);
  expect(pairMessages(events, "nala", "jarvis")).toEqual(pairMessages(events, "jarvis", "nala"));
});
it("pages the complete board and renders only the selected conversation", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    return { ok: true, json: async () => ({ events: url.includes("after_seq=0&") ? Array.from({ length: 1000 }, (_, i) => envelope(i + 1, "nala", i === 0 ? "jarvis" : "gmail")) : [envelope(1001, "jarvis", "nala", "The reply")] }) };
  });
  const close = vi.fn();
  render(<PairConversation pair={pair} onClose={close} />);
  await screen.findByText("The reply");
  expect(screen.getByText("Message 1")).toBeTruthy();
  expect(screen.queryByText("Message 2")).toBeNull();
  expect(calls).toEqual(["/api/society/events?after_seq=0&limit=1000", "/api/society/events?after_seq=1000&limit=1000"]);
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.getByText("agent_chat.pair_read_only")).toBeTruthy();
  fireEvent.keyDown(screen.getByTestId("agent-pair-conversation"), { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});
it("opens even a short message in place and restores the original draft on close", async () => {
  vi.stubGlobal("fetch", async () => ({ ok: true, json: async () => ({ events: [envelope(1, "jarvis", "nala", "Hello")] }) }));
  const item = reduceEvent(EMPTY_TIMELINE, { seq: 1, ts_ms: 1, kind: "agent_message", payload: { message_id: "m1", sender_id: "jarvis", sender_name: "Jarvis", sender_kind: "jarvis", text: "Hello" } }).items[0];
  if (item.type !== "internal") throw new Error("Expected internal message");
  render(<PairConversationBoundary recipient={{ id: "nala", name: "Nala" }}><InternalMessageBubble item={item} recipientName="Nala" /><input aria-label="Draft" defaultValue="Keep this draft" /></PairConversationBoundary>);
  fireEvent.click(screen.getByRole("button", { name: "agent_chat.pair_open" }));
  await waitFor(() => expect(screen.queryByText("agent_chat.pair_loading")).toBeNull());
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getAllByRole("button", { name: "agent_chat.pair_close" })[0]);
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Keep this draft");
  expect(screen.queryByTestId("agent-pair-conversation")).toBeNull();
});
it("reports a load failure without pretending the history is empty", async () => {
  vi.stubGlobal("fetch", async () => ({ ok: false, status: 503 }));
  render(<PairConversation pair={pair} onClose={() => {}} />);
  await screen.findByRole("alert");
  expect(screen.queryByText("agent_chat.pair_empty")).toBeNull();
});

