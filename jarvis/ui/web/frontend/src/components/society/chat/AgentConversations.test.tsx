import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { SocietyEnvelope } from "@/lib/societyApi";
import { collectOutgoing } from "@/components/agentchat/useOutgoingMessages";
import { AgentConversationsBar } from "./AgentConversations";
import { useRoutineNavigation } from "./routineNavigation";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); useRoutineNavigation.getState().close(); });

function sent(seq: number, fromSession?: string): SocietyEnvelope {
  return { seq, event_id: `m${seq}`, msg_type: "ANSWER", from_agent: "scout", to_agent: "jarvis",
    trace_id: "t", parent_event_id: null, ts_ms: seq * 1000, cost_usd: 0,
    payload: { text: `Reply ${seq}`, ...(fromSession ? { from_session: fromSession } : {}) } };
}

it("keeps a reply written in a conversation chat out of the person's chat", () => {
  const rows = [sent(1), sent(2, "society:scout"), sent(3, "society:scout:with:jarvis")];
  expect(collectOutgoing([], rows, "scout", "society:scout").map((row) => row.event_id)).toEqual(["m1", "m2"]);
  expect(collectOutgoing([], rows, "scout").map((row) => row.event_id)).toEqual(["m1", "m2", "m3"]);
});

it("lists the agent's conversations and opens one as a full chat", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ conversations: [{
    session_id: "society:scout:with:jarvis", title: "Scout · Jarvis", updated_ms: 5000, running: true,
    owner_id: "scout", counterpart_id: "jarvis", counterpart_name: "Jarvis",
  }] }))));
  render(<AgentConversationsBar agentId="scout" agentName="Scout" displayName={(id, fallback) => id === "jarvis" ? "Hanna" : fallback} />);
  const chip = await screen.findByTestId("agent-conversation");
  expect(chip.textContent).toContain("society.conversations.with");
  expect(chip.dataset.state).toBe("running");
  fireEvent.click(chip);
  expect(useRoutineNavigation.getState().target).toEqual({
    agentId: "scout", sessionId: "society:scout:with:jarvis", kind: "conversation",
    title: "Scout · Hanna", timestamp: 5000,
  });
});

it("shows nothing while the agent has no conversations", async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ conversations: [] })));
  vi.stubGlobal("fetch", fetchMock);
  const { container } = render(<AgentConversationsBar agentId="scout" agentName="Scout" displayName={(_, f) => f} />);
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/society/agents/scout/conversations", expect.anything()));
  expect(container.innerHTML).toBe("");
});

it("marks a conversation that waits for the person", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ conversations: [{
    session_id: "society:scout:with:nova", title: "Scout · Nova", updated_ms: 1, running: true, waiting: true,
    owner_id: "scout", counterpart_id: "nova", counterpart_name: "Nova",
  }] }))));
  render(<AgentConversationsBar agentId="scout" agentName="Scout" displayName={(_, f) => f} />);
  const chip = await screen.findByTestId("agent-conversation");
  expect(chip.dataset.state).toBe("waiting");
  expect(chip.textContent).toContain("society.conversations.waiting");
});
