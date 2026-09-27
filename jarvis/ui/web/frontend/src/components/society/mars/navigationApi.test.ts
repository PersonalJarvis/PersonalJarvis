import { describe, expect, it } from "vitest";
import { latestNavigationRecords, navigationSnapshotSchema } from "./navigationApi";

const row = (command: string, state = "moving", presence = "placed") => ({
  command_id: command, request_id: command, agent_id: "one", trace_id: command,
  world_id: "mars:ordinary", station_id: "communications-console", mode: "pedestrian",
  graph_version: 1, graph_signature: "a".repeat(64), state, presence,
  position: [268, 58, 68], current_node: "outpost-arrival", edge_id: "route-03", next_node: "console-approach", edge_progress: 0.3, reason: "",
});
const snapshot = (commands: unknown[]) => ({ world_id: "mars:ordinary", schema_version: 1,
  graph_version: 1, graph_signature: "a".repeat(64), seq: 5, commands, occupancies: [] });

describe("navigation client contract", () => {
  it("uses durable receipt order for the latest actor location, including stopped bodies", () => {
    const data = navigationSnapshotSchema.parse(snapshot([row("older"), row("newer", "canceled")]));
    expect(latestNavigationRecords(data)).toHaveLength(1);
    expect(latestNavigationRecords(data)[0].command_id).toBe("newer");
    expect(latestNavigationRecords(data)[0].presence).toBe("placed");
  });
  it("retains explicit nonphysical spawn queues without inventing placed actors", () => {
    const data = navigationSnapshotSchema.parse(snapshot([row("queued", "queueing", "spawn_queue")]));
    expect(latestNavigationRecords(data).filter((record) => record.presence === "placed")).toEqual([]);
  });
  it("rejects wrong worlds/graphs, oversized snapshots and nonfinite locations", () => {
    for (const body of [
      { ...snapshot([]), world_id: "mars:swarm:other" },
      { ...snapshot([]), graph_version: 2 },
      snapshot(Array.from({ length: 321 }, () => row("too-many"))),
      snapshot([{ ...row("invalid"), position: [Infinity, 0, 0] }]),
      snapshot([{ ...row("invalid"), state: "completed" }]),
    ]) expect(navigationSnapshotSchema.safeParse(body).success).toBe(false);
  });
});
