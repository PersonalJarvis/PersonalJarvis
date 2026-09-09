import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import en from "@/i18n/locales/en.json";
import de from "@/i18n/locales/de.json";
import es from "@/i18n/locales/es.json";
import { InternalMessageBubble, internalNeedsFold, internalPreview } from "./InternalMessageBubble";
import { EMPTY_TIMELINE, reduceEvent, reduceEvents } from "./reduce";
import type { AgentChatEvent } from "@/lib/agentChatApi";

const locale = vi.hoisted(() => ({ strings: {} as Record<string, string> }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => locale.strings[key.split(".")[1]] ?? key }));

const incoming: AgentChatEvent = {
  seq: 1, ts_ms: 1000, kind: "agent_message",
  payload: {
    message_id: "m1", sender_id: "jarvis", sender_name: "Jarvis", sender_kind: "jarvis",
    text: "A test message", prompt: "[say from Jarvis]\nA test message", trace_id: "t1",
    status: "queued", turn_id: "", error: "",
  },
};
const delivered: AgentChatEvent = {
  seq: 2, ts_ms: 2000, kind: "agent_message_status",
  payload: { message_id: "m1", status: "delivered", turn_id: "turn1", error: "" },
};

afterEach(cleanup);

it.each([en, de, es])("shows the trusted sender and live delivery status in each locale", (resource) => {
  locale.strings = resource.agent_chat;
  let timeline = reduceEvent(EMPTY_TIMELINE, incoming);
  const item = timeline.items[0];
  if (item.type !== "internal") throw new Error("Expected an internal message");
  const view = render(<InternalMessageBubble item={item} />);
  expect(screen.getByText("Jarvis")).toBeTruthy();
  expect(screen.getByText(resource.agent_chat.delivery_queued)).toBeTruthy();
  expect(screen.getByText("A test message")).toBeTruthy();
  timeline = reduceEvent(timeline, delivered);
  const updated = timeline.items[0];
  if (updated.type !== "internal") throw new Error("Expected an internal message");
  view.rerender(<InternalMessageBubble item={updated} />);
  expect(screen.getByText(resource.agent_chat.delivery_delivered)).toBeTruthy();
  expect(screen.queryByText(resource.agent_chat.delivery_queued)).toBeNull();
});

it("reopening matches live state and duplicate envelopes remain one message", () => {
  const live = reduceEvent(reduceEvent(EMPTY_TIMELINE, incoming), delivered);
  const reopened = reduceEvents(EMPTY_TIMELINE, [incoming, delivered]);
  expect(reopened).toEqual(live);
  expect(reduceEvent(live, incoming).items).toHaveLength(1);
  expect(reduceEvent(EMPTY_TIMELINE, { ...incoming, kind: "user_message", payload: { text: "Old message" } }).items[0].type).toBe("user");
});

it("shows agent authors and a terminal failure without changing the message", () => {
  locale.strings = en.agent_chat;
  const timeline = reduceEvents(EMPTY_TIMELINE, [
    { ...incoming, payload: { ...incoming.payload, sender_id: "scout", sender_name: "Scout", sender_kind: "agent" } },
    { ...delivered, payload: { ...delivered.payload, status: "failed", error: "The recipient is paused" } },
  ]);
  const item = timeline.items[0];
  if (item.type !== "internal") throw new Error("Expected an internal message");
  render(<InternalMessageBubble item={item} />);
  expect(screen.getByText("Scout")).toBeTruthy();
  expect(screen.getByText("Failed")).toBeTruthy();
  expect(screen.getByText("The recipient is paused")).toBeTruthy();
});

it("folds a long message to one sentence and unfolds it on click", () => {
  locale.strings = en.agent_chat;
  const long = "First sentence here. Second sentence follows with details. Third one too.";
  const timeline = reduceEvent(EMPTY_TIMELINE, {
    ...incoming,
    payload: { ...incoming.payload, message_id: "m-long", text: long },
  });
  const item = timeline.items[0];
  if (item.type !== "internal") throw new Error("Expected an internal message");
  render(<InternalMessageBubble item={item} recipientName="Scout" />);
  const card = screen.getByTestId("agent-message-internal");
  expect(card.getAttribute("data-state")).toBe("folded");
  // Only the first sentence shows; the rest stays hidden until the click.
  expect(screen.getByTestId("agent-message-internal-preview").textContent).toContain("First sentence here.");
  expect(screen.queryByTestId("agent-message-internal-full")).toBeNull();
  expect(screen.getByText("Scout")).toBeTruthy();

  fireEvent.click(screen.getByTestId("agent-message-internal-toggle"));
  expect(card.getAttribute("data-state")).toBe("open");
  expect(screen.getByTestId("agent-message-internal-full").textContent).toContain("Third one too.");

  fireEvent.click(screen.getByTestId("agent-message-internal-toggle"));
  expect(card.getAttribute("data-state")).toBe("folded");
});

it("shows a short message whole with no toggle", () => {
  locale.strings = en.agent_chat;
  const timeline = reduceEvent(EMPTY_TIMELINE, incoming);
  const item = timeline.items[0];
  if (item.type !== "internal") throw new Error("Expected an internal message");
  render(<InternalMessageBubble item={item} recipientName="Scout" />);
  expect(screen.getByTestId("agent-message-internal").getAttribute("data-state")).toBe("whole");
  expect(screen.getByTestId("agent-message-internal-toggle").hasAttribute("disabled")).toBe(true);
  expect(screen.getByTestId("agent-message-internal-full").textContent).toContain("A test message");
});

it("derives a one-sentence preview", () => {
  expect(internalPreview("Hello world. More follows.")).toBe("Hello world.");
  expect(internalPreview("Short")).toBe("Short");
  expect(internalNeedsFold("Short")).toBe(false);
  expect(internalNeedsFold("First. Second.")).toBe(true);
});
