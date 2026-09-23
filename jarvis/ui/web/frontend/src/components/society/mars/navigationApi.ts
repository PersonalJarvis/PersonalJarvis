import { z } from "zod";
import { MarsApiError, WORLD_ID } from "./api";
import { WORLD } from "./world";

export const NAVIGATION_STATES = ["queueing", "moving", "temporarily_blocked", "rerouting", "arrived", "unreachable", "canceled"] as const;
const identity = z.string().min(1).max(128);
const position = z.tuple([z.number().finite(), z.number().finite(), z.number().finite()]);
const travelRecordSchema = z.object({
  command_id: identity, request_id: identity, trace_id: identity,
  world_id: z.literal(WORLD_ID), station_id: identity, mode: z.enum(["pedestrian", "rover"]),
  graph_version: z.literal(WORLD.navigation.version), graph_signature: z.string().regex(/^[0-9a-f]{64}$/),
  state: z.enum(NAVIGATION_STATES), presence: z.enum(["spawn_queue", "placed"]),
  position, current_node: identity, edge_id: identity.nullable(), next_node: identity.nullable(),
  edge_progress: z.number().min(0).max(1), reason: z.string().max(200),
});
export const navigationRecordSchema = travelRecordSchema.extend({ agent_id: identity });
export type NavigationRecord = z.infer<typeof navigationRecordSchema>;
export const vehicleRecordSchema = travelRecordSchema.extend({ vehicle_id: identity, ride_id: identity.nullable() });
export type VehicleRecord = z.infer<typeof vehicleRecordSchema>;
export const RIDE_STATES = ["approaching", "ready_to_board", "boarded", "traveling", "arrived", "stopped", "exit_blocked", "completed", "canceled"] as const;
export const roverRideSchema = z.object({
  ride_id: identity, request_id: identity, agent_id: identity, vehicle_id: identity, trace_id: identity,
  origin_dock_id: identity, destination_dock_id: identity.nullable(), approach_command_id: identity,
  state: z.enum(RIDE_STATES), attached: z.boolean(),
  created_ms: z.number().int().nonnegative(), updated_ms: z.number().int().nonnegative(), deadline_ms: z.number().int().nonnegative(),
  reason: z.string().max(200),
});
export type RoverRideRecord = z.infer<typeof roverRideSchema>;
export const navigationSnapshotSchema = z.object({
  world_id: z.literal(WORLD_ID), schema_version: z.literal(1),
  graph_version: z.literal(WORLD.navigation.version), graph_signature: z.string().regex(/^[0-9a-f]{64}$/),
  seq: z.number().int().nonnegative(), commands: z.array(navigationRecordSchema).max(320),
  occupancies: z.array(z.object({ resource_id: identity, command_id: identity, agent_id: identity.nullable(),
    actor: z.object({ kind: z.enum(["agent", "vehicle"]), id: identity }).nullable().optional(),
    position, graph_signature: z.string().length(64) })).max(1024),
  vehicles: z.array(vehicleRecordSchema).max(64).default([]),
  rides: z.array(roverRideSchema).max(320).default([]),
});
export type NavigationSnapshot = z.infer<typeof navigationSnapshotSchema>;
const attemptSchema = z.object({ request_id: z.string().uuid(), agent_id: identity, station_id: identity });
export type MoveAttempt = z.infer<typeof attemptSchema>;
const ATTEMPT_KEY = `jarvis.${WORLD_ID}.pending-move.v1`;

export function readMoveAttempt(): MoveAttempt | null {
  try {
    const parsed = attemptSchema.safeParse(JSON.parse(sessionStorage.getItem(ATTEMPT_KEY) ?? "null"));
    return parsed.success ? parsed.data : null;
  } catch { return null; } // Storage denial/corruption only loses refresh retry recovery.
}
export function saveMoveAttempt(attempt: MoveAttempt): void {
  try { sessionStorage.setItem(ATTEMPT_KEY, JSON.stringify(attempt)); }
  catch { /* In-memory retries retain the same request identity. */ }
}
export function clearMoveAttempt(requestId: string): void {
  try {
    if (readMoveAttempt()?.request_id === requestId) sessionStorage.removeItem(ATTEMPT_KEY);
  } catch { /* A later retry is idempotent even if storage remains unavailable. */ }
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`/api/society/mars${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new MarsApiError(response.status);
  return response.json();
}
export async function fetchNavigationSnapshot(signal?: AbortSignal): Promise<NavigationSnapshot> {
  return navigationSnapshotSchema.parse(await request("/navigation/snapshot", { signal }));
}
export async function submitMove(attempt: MoveAttempt): Promise<NavigationRecord> {
  return navigationRecordSchema.parse(await request(`/agents/${encodeURIComponent(attempt.agent_id)}/moves`, {
    method: "POST", body: JSON.stringify({ request_id: attempt.request_id, station_id: attempt.station_id,
      world_id: WORLD_ID, schema_version: 1, layout_version: WORLD.layout_version,
      graph_version: WORLD.navigation.version, mode: "pedestrian" }),
  }));
}
export async function cancelMove(record: NavigationRecord): Promise<NavigationRecord> {
  return navigationRecordSchema.parse(await request(`/agents/${encodeURIComponent(record.agent_id)}/moves/${encodeURIComponent(record.command_id)}/cancel`, { method: "POST" }));
}

/** The API orders receipts by durable ordinal, including each actor's latest receipt. */
export function latestNavigationRecords(snapshot?: NavigationSnapshot): NavigationRecord[] {
  const latest = new Map<string, NavigationRecord>();
  for (const record of snapshot?.commands ?? []) latest.set(record.agent_id, record);
  return [...latest.values()];
}

export function currentRoverRide(snapshot: NavigationSnapshot | undefined, agentId: string): RoverRideRecord | undefined {
  return snapshot?.rides.find((ride) => ride.agent_id === agentId && !["completed", "canceled"].includes(ride.state));
}

/** Attached riders have no separate pedestrian body, even if the vehicle is unavailable. */
export function pedestrianNavigationRecords(snapshot?: NavigationSnapshot): NavigationRecord[] {
  const attached = new Set(snapshot?.rides.filter((ride) => ride.attached).map((ride) => ride.agent_id));
  return latestNavigationRecords(snapshot).filter((record) => !attached.has(record.agent_id));
}

/** A display-only attachment projection from one atomic server snapshot. */
export function agentFollowRecords(snapshot?: NavigationSnapshot): NavigationRecord[] {
  const records = latestNavigationRecords(snapshot);
  const rides = new Map(snapshot?.rides.filter((ride) => ride.attached).map((ride) => [ride.agent_id, ride]));
  const vehicles = new Map(snapshot?.vehicles.map((vehicle) => [vehicle.vehicle_id, vehicle]));
  return records.flatMap((record) => {
    const ride = rides.get(record.agent_id);
    if (!ride) return [record];
    const vehicle = vehicles.get(ride.vehicle_id);
    if (!vehicle || vehicle.ride_id !== ride.ride_id || vehicle.presence !== "placed") return [];
    return [{ ...record, position: vehicle.position, presence: vehicle.presence, state: vehicle.state,
      mode: vehicle.mode, current_node: vehicle.current_node, edge_id: vehicle.edge_id,
      next_node: vehicle.next_node, edge_progress: vehicle.edge_progress }];
  });
}
