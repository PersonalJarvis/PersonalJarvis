import { afterEach, describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useEventStore } from "@/store/events";
import type { SocietyAgent } from "../data";
import { useErrandFeed, useGigiErrands } from "./gigiErrands";

const agents = new Map([
  ["jarvis", { agentId: "jarvis", name: "Jarvis", tier: "lead" } as SocietyAgent],
  ["nora", { agentId: "nora", name: "Nora", tier: "specialist" } as SocietyAgent],
]);

afterEach(() => useGigiErrands.getState().reset());

describe("useErrandFeed", () => {
  it("queues Gigi's errand when the lead hands an agent a task", () => {
    renderHook(() => useErrandFeed(agents, true));
    act(() => {
      useEventStore.getState().pushEvent({
        id: "e-1", name: "SocietyMessageSent", layer: "society", ts: Date.now(),
        payload: { event_id: "a1", msg_type: "ASSIGN", from_agent: "jarvis", to_agent: "nora", room_id: "", text: "Do it" },
      });
    });
    expect(useGigiErrands.getState().current).toMatchObject({ id: "a1", to: "nora", toName: "Nora", phase: "fly" });
  });
});
