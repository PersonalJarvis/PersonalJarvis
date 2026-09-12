import { z } from "zod";
import { MarsApiError, WORLD_ID } from "./api";
import { WORLD } from "./world";

export const NAVIGATION_STATES = ["queueing", "moving", "temporarily_blocked", "rerouting", "arrived", "unreachable", "canceled"] as const;
const identity = z.string().min(1).max(128);
const position = z.tuple([z.number().finite(), z.number().finite(), z.number().finite()]);
export const navigationRecordSchema = z.object({
  command_id: identity, request_id: identity, agent_id: identity, trace_id: identity,
  world_id: z.literal(WORLD_ID), station_id: identity, mode: z.enum(["pedestrian", "rover"]),
  graph_version: z.literal(WORLD.navigation.version), graph_signature: z.string().regex(/^[0-9a-f]{64}$/),
  state: z.enum(NAVIGATION_STATES), presence: z.enum(["spawn_queue", "placed"]),
  position, current_node: identity, edge_id: identity.nullable(), next_node: identity.nullable(),
  edge_progress: z.number().min(0).max(1), reason: z.string().max(200),
});
export type NavigationRecord = z.infer<typeof navigationRecordSchema>;
export const navigationSnapshotSchema = z.object({
  world_id: z.literal(WORLD_ID), schema_version: z.literal(1),
  graph_version: z.literal(WORLD.navigation.version), graph_signature: z.string().regex(/^[0-9a-f]{64}$/),
  seq: z.number().int().nonnegative(), commands: z.array(navigationRecordSchema).max(320),
  occupancies: z.array(z.object({ resource_id: identity, command_id: identity, agent_id: identity, position, graph_signature: z.string().length(64) })).max(1024),
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
