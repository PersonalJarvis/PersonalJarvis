import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { navigationRecordSchema, type NavigationRecord } from "./navigationApi";
import { useAgentFollowTarget, type AgentFollowSnapshot } from "./useAgentFollowTarget";

const record = navigationRecordSchema.parse({
  command_id: "first", request_id: "request", agent_id: "one", trace_id: "trace",
  world_id: "mars:ordinary", station_id: "communications-console", mode: "pedestrian",
  graph_version: 1, graph_signature: "a".repeat(64), state: "moving", presence: "placed",
  position: [268, 58, 68], current_node: "outpost-arrival", edge_id: "route-03",
  next_node: "console-approach", edge_progress: 0.3, reason: "",
});
function snapshot(changes: Partial<AgentFollowSnapshot> = {}): AgentFollowSnapshot {
  return { records: [record], names: new Map([["one", "Worker"]]), navigationFresh: true, rosterFresh: true, offline: false, ...changes };
}

describe("confirmed agent-follow targets", () => {
  it("waits for both fresh queries before restoring a stored identity", () => {
    const hook = renderHook(({ data }) => useAgentFollowTarget("one", data), {
      initialProps: { data: snapshot({ navigationFresh: false, rosterFresh: false }) },
    });
    expect(hook.result.current).toEqual({ target: null, status: "waiting" });
    hook.rerender({ data: snapshot({ rosterFresh: false }) });
    expect(hook.result.current).toEqual({ target: null, status: "waiting" });
    hook.rerender({ data: snapshot() });
    expect(hook.result.current.target?.position).toEqual(record.position);
    expect(hook.result.current.status).toBe("live");
    hook.unmount();
  });

  it("follows the physical agent across replacement commands and preserves placed terminal states", () => {
    const hook = renderHook(({ data }) => useAgentFollowTarget("one", data), { initialProps: { data: snapshot() } });
    const replacement: NavigationRecord = { ...record, command_id: "replacement", position: [270, 58, 70] };
    hook.rerender({ data: snapshot({ records: [replacement] }) });
    expect(hook.result.current.target).toEqual({ agentId: "one", name: "Worker", position: replacement.position });
    for (const state of ["arrived", "canceled", "unreachable"] as const) {
      hook.rerender({ data: snapshot({ records: [{ ...replacement, state }] }) });
      expect(hook.result.current.status).toBe("live");
      expect(hook.result.current.target?.agentId).toBe("one");
    }
    hook.unmount();
  });

  it("holds the last confirmed target while offline and resumes only with fresh placement", () => {
    const hook = renderHook(({ data }) => useAgentFollowTarget("one", data), { initialProps: { data: snapshot() } });
    const confirmed = hook.result.current.target;
    hook.rerender({ data: snapshot({ records: [], navigationFresh: false, rosterFresh: false, offline: true }) });
    expect(hook.result.current).toEqual({ target: confirmed, status: "offline" });
    const position: NavigationRecord["position"] = [272, 58, 74];
    hook.rerender({ data: snapshot({ records: [{ ...record, position }] }) });
    expect(hook.result.current.target?.position).toEqual(position);
    expect(hook.result.current.status).toBe("live");
    hook.unmount();
  });

  it.each(["navigation", "roster", "spawn_queue"])("clears a target missing from successful %s evidence", (kind) => {
    const hook = renderHook(({ data }) => useAgentFollowTarget("one", data), { initialProps: { data: snapshot() } });
    hook.rerender({ data: snapshot(kind === "navigation" ? { records: [], rosterFresh: false, offline: true }
      : kind === "roster" ? { names: new Map(), navigationFresh: false, offline: true }
        : { records: [{ ...record, presence: "spawn_queue" }] }) });
    expect(hook.result.current).toEqual({ target: null, status: "missing" });
    hook.unmount();
  });

  it("never borrows the old target when changing identity or stopping", () => {
    const hook = renderHook(({ id, data }) => useAgentFollowTarget(id, data), {
      initialProps: { id: "one" as string | null, data: snapshot() },
    });
    hook.rerender({ id: "two", data: snapshot({ navigationFresh: false, rosterFresh: false, offline: true }) });
    expect(hook.result.current).toEqual({ target: null, status: "offline" });
    hook.rerender({ id: null, data: snapshot() });
    expect(hook.result.current).toEqual({ target: null, status: "idle" });
    hook.unmount();
  });
});
