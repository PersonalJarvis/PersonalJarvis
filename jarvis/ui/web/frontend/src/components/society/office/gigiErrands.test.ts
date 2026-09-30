import { afterEach, describe, expect, it } from "vitest";
import { deliverySpot, errandFrom, ERRAND_STALE_MS, MAX_QUEUED, useGigiErrands, type Errand } from "./gigiErrands";

const agents = new Map([
  ["jarvis", { tier: "lead" as const, name: "Jarvis" }],
  ["nora", { tier: "specialist" as const, name: "Nora" }],
  ["max", { tier: "specialist" as const, name: "Max" }],
]);

const assign = (patch: Record<string, unknown> = {}) => ({
  event_id: "e1", msg_type: "ASSIGN", from_agent: "jarvis", to_agent: "nora", room_id: "", text: "Check the inbox", ...patch,
});

function errand(id: string, queuedMs = Date.now()): Errand {
  return { id, to: "nora", toName: "Nora", text: id, msgType: "ASSIGN", queuedMs };
}

afterEach(() => useGigiErrands.getState().reset());

describe("errandFrom", () => {
  it("turns the lead's task for an agent into an errand", () => {
    expect(errandFrom(assign(), agents, 5)).toEqual({ id: "e1", to: "nora", toName: "Nora", text: "Check the inbox", msgType: "ASSIGN", queuedMs: 5 });
  });

  it("ignores lines that are not the lead handing something to a present agent", () => {
    expect(errandFrom(assign({ from_agent: "max" }), agents, 0)).toBeNull();
    expect(errandFrom(assign({ to_agent: "ghost" }), agents, 0)).toBeNull();
    expect(errandFrom(assign({ to_agent: "jarvis" }), agents, 0)).toBeNull();
    expect(errandFrom(assign({ room_id: "r1" }), agents, 0)).toBeNull();
    expect(errandFrom(assign({ msg_type: "ANSWER" }), agents, 0)).toBeNull();
    expect(errandFrom(null, agents, 0)).toBeNull();
  });
});

describe("deliverySpot", () => {
  it("hovers next to the recipient on the side Gigi comes from", () => {
    expect(deliverySpot({ x: 0, z: 0 }, { x: 10, z: 0 }, 1)).toEqual({ x: 1, z: 0 });
    expect(deliverySpot({ x: 2, z: 2 }, { x: 2, z: 2 }, 1)).toEqual({ x: 3, z: 2 });
  });
});

describe("errand queue", () => {
  it("flies, delivers, then takes the next errand", () => {
    const store = useGigiErrands.getState();
    store.enqueue(errand("a"));
    store.enqueue(errand("b"));
    store.enqueue(errand("a"));
    expect(useGigiErrands.getState().current).toMatchObject({ id: "a", phase: "fly" });
    expect(useGigiErrands.getState().queue.map((e) => e.id)).toEqual(["b"]);
    store.arrive("a");
    expect(useGigiErrands.getState().current).toMatchObject({ id: "a", phase: "deliver" });
    store.finish("a");
    expect(useGigiErrands.getState().current).toMatchObject({ id: "b", phase: "fly" });
    store.finish("b");
    expect(useGigiErrands.getState().current).toBeNull();
  });

  it("caps the queue and skips errands whose moment has passed", () => {
    const store = useGigiErrands.getState();
    store.enqueue(errand("now"));
    store.enqueue(errand("old", Date.now() - ERRAND_STALE_MS - 1));
    for (let i = 0; i < MAX_QUEUED + 2; i += 1) store.enqueue(errand(`q${i}`));
    expect(useGigiErrands.getState().queue).toHaveLength(MAX_QUEUED);
    store.reset();
    store.enqueue(errand("first"));
    store.enqueue(errand("stale", Date.now() - ERRAND_STALE_MS - 1));
    store.enqueue(errand("fresh"));
    store.finish("first");
    expect(useGigiErrands.getState().current?.id).toBe("fresh");
  });
});
