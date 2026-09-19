import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSwarmWorld } from "./useSwarmWorld";
import { snapshotFixture, SwarmSocketFake } from "./testFixtures";

beforeEach(() => {
  SwarmSocketFake.all = [];
  vi.stubGlobal("WebSocket", SwarmSocketFake);
  vi.stubGlobal("fetch", async (path: string) => {
    const id = /\/teams\/([^/]+)/.exec(String(path))?.[1] ?? "alpha";
    return new Response(JSON.stringify(snapshotFixture(id)), { headers: { "Content-Type": "application/json" } });
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("team-scoped live worlds", () => {
  it("accepts a restored storage generation while rejecting same-generation rollback", async () => {
    const hook = renderHook(() => useSwarmWorld("alpha", "", 0, true));
    await waitFor(() => expect(SwarmSocketFake.all.length).toBeGreaterThan(0));
    const socket = SwarmSocketFake.all[0];
    const current = snapshotFixture("alpha"); current.revision = "100";
    current.team.storage_generation = "before";
    act(() => socket.emit({ team_id: "alpha", type: "snapshot", snapshot: current }));
    await waitFor(() => expect(hook.result.current.snapshot?.revision).toBe("100"));
    const stale = structuredClone(current); stale.revision = "50";
    act(() => socket.emit({ team_id: "alpha", type: "snapshot", snapshot: stale }));
    expect(hook.result.current.snapshot?.revision).toBe("100");
    const restored = snapshotFixture("alpha"); restored.revision = "3";
    restored.team.storage_generation = "restored";
    act(() => socket.emit({ team_id: "alpha", type: "snapshot", snapshot: restored }));
    await waitFor(() => expect(hook.result.current.snapshot?.revision).toBe("3"));
    expect(hook.result.current.snapshot?.team.storage_generation).toBe("restored");
  });
  it("isolates simultaneous worlds and releases every subscription", async () => {
    const a = renderHook(() => useSwarmWorld("alpha", "", 0, true));
    const b = renderHook(() => useSwarmWorld("beta", "", 0, true));
    await waitFor(() => expect(SwarmSocketFake.all).toHaveLength(2));
    await waitFor(() => expect(a.result.current.snapshot?.team.id).toBe("alpha"));
    await waitFor(() => expect(b.result.current.snapshot?.team.id).toBe("beta"));
    const alphaSocket = SwarmSocketFake.all.find(socket => socket.url.pathname.includes("alpha"))!;
    const next = snapshotFixture("alpha"); next.revision = "2"; next.team.tokens_used = "98765432101";
    act(() => alphaSocket.emit({ team_id: "alpha", type: "snapshot", snapshot: next }));
    await waitFor(() => expect(a.result.current.snapshot?.team.tokens_used).toBe("98765432101"));
    expect(b.result.current.snapshot?.team.tokens_used).toBe("10000000000");
    a.unmount(); b.unmount();
    expect(SwarmSocketFake.all.every(socket => socket.closed)).toBe(true);
  });
  it("rejects cross-team and oversized frames without merging their contents", async () => {
    const hook = renderHook(() => useSwarmWorld("alpha", "", 0, true));
    await waitFor(() => expect(SwarmSocketFake.all.length).toBeGreaterThan(0));
    await waitFor(() => expect(hook.result.current.snapshot?.team.id).toBe("alpha"));
    const socket = SwarmSocketFake.all[0];
    act(() => socket.emit({ team_id: "beta", type: "snapshot", snapshot: snapshotFixture("beta") }));
    expect(socket.closed).toBe(true);
    expect(hook.result.current.snapshot?.team.id).toBe("alpha");
  });
  it("retires sockets on terminal state and keeps the historical snapshot", async () => {
    const hook = renderHook(() => useSwarmWorld("alpha", "", 0, true));
    await waitFor(() => expect(SwarmSocketFake.all.length).toBeGreaterThan(0));
    const snapshot = snapshotFixture(); snapshot.revision = "2"; snapshot.team.state = "succeeded";
    act(() => SwarmSocketFake.all[0].emit({ team_id: "alpha", type: "snapshot", snapshot }));
    await waitFor(() => expect(hook.result.current.connection).toBe("history"));
    expect(SwarmSocketFake.all[0].closed).toBe(true);
    expect(hook.result.current.snapshot?.revision).toBe("2");
  });
  it("replaces from durable snapshot after replay gap and suspends while hidden", async () => {
    const hook = renderHook(({ awake }) => useSwarmWorld("alpha", "", 0, awake), { initialProps: { awake: true } });
    await waitFor(() => expect(SwarmSocketFake.all.length).toBeGreaterThan(0));
    act(() => SwarmSocketFake.all[0].emit({ team_id: "alpha", type: "gap" }));
    await waitFor(() => expect(SwarmSocketFake.all.length).toBeGreaterThan(1), { timeout: 3000 });
    await waitFor(() => expect(hook.result.current.snapshot?.team.id).toBe("alpha"));
    hook.rerender({ awake: false });
    expect(SwarmSocketFake.all.every(socket => socket.closed)).toBe(true);
    expect(hook.result.current.connection).toBe("background");
    expect(hook.result.current.snapshot?.team.id).toBe("alpha");
  });
});
