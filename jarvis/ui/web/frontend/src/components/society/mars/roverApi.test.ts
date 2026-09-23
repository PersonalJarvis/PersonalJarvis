import { afterEach, describe, expect, it, vi } from "vitest";
import { agentFollowRecords, navigationSnapshotSchema, pedestrianNavigationRecords } from "./navigationApi";
import { clearRoverAttempt, readRoverAttempt, saveRoverAttempt, submitRoverAction, type RoverAttempt } from "./roverApi";
import { roverRide, roverSnapshot, roverVehicle } from "./roverFixtures.test-support";

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("rover atomic snapshot and retry contract", () => {
  it("suppresses the separate body and follows only the matching confirmed vehicle", () => {
    const ride = { ...roverRide, state: "traveling" as const, attached: true };
    const vehicle = { ...roverVehicle, ride_id: ride.ride_id, position: [300, 58.006, 84] as [number, number, number], state: "moving" as const };
    const snapshot = roverSnapshot([ride], [vehicle]);
    expect(pedestrianNavigationRecords(snapshot)).toEqual([]);
    expect(agentFollowRecords(snapshot)[0].position).toEqual(vehicle.position);
    expect(snapshot.commands[0].position).not.toEqual(vehicle.position);
    for (const vehicles of [[], [{ ...vehicle, ride_id: "other-ride" }], [{ ...vehicle, presence: "spawn_queue" as const }]]) {
      expect(agentFollowRecords(roverSnapshot([ride], vehicles))).toEqual([]);
      expect(pedestrianNavigationRecords(roverSnapshot([ride], vehicles))).toEqual([]);
    }
    expect(pedestrianNavigationRecords(roverSnapshot([{ ...ride, state: "completed", attached: false }], [vehicle]))).toHaveLength(1);
  });

  it("accepts typed vehicle occupancy without inventing an ordinary agent", () => {
    const snapshot = roverSnapshot();
    const data = navigationSnapshotSchema.parse({ ...snapshot, occupancies: [{ resource_id: "node:rover-a-dock",
      command_id: roverVehicle.command_id, agent_id: null, actor: { kind: "vehicle", id: "outpost-rover" },
      position: roverVehicle.position, graph_signature: "a".repeat(64) }] });
    expect(data.occupancies[0].agent_id).toBeNull();
    expect(data.occupancies[0].actor?.kind).toBe("vehicle");
    expect(navigationSnapshotSchema.safeParse({ ...snapshot, rides: [{ ...roverRide, state: "boarding_complete_by_animation" }] }).success).toBe(false);
    expect(navigationSnapshotSchema.safeParse({ ...snapshot, vehicles: [{ ...roverVehicle, position: [NaN, 58, 70] }] }).success).toBe(false);
  });

  it("saves the exact action payload through refresh and clears only its own attempt", async () => {
    const attempt: RoverAttempt = { request_id: crypto.randomUUID(), agent_id: "agent/one", ride_id: "ride/one", action: "travel", destination_dock_id: "outpost-b" };
    saveRoverAttempt(attempt);
    expect(readRoverAttempt()).toEqual(attempt);
    clearRoverAttempt("different"); expect(readRoverAttempt()).toEqual(attempt);
    const fetcher = vi.fn(async () => new Response(JSON.stringify(roverRide), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    await submitRoverAction(readRoverAttempt()!); await submitRoverAction(readRoverAttempt()!);
    expect(fetcher.mock.calls[0]).toEqual(fetcher.mock.calls[1]);
    const [path, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/society/mars/agents/agent%2Fone/rides/ride%2Fone/travel");
    expect(JSON.parse(init.body as string)).toEqual({ request_id: attempt.request_id, destination_dock_id: "outpost-b" });
    clearRoverAttempt(attempt.request_id); expect(readRoverAttempt()).toBeNull();
  });
});
