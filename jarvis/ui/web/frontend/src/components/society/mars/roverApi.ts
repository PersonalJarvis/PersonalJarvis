import { z } from "zod";
import { MarsApiError, WORLD_ID } from "./api";
import { roverRideSchema, type RoverRideRecord } from "./navigationApi";
import { WORLD } from "./world";

const identity = z.string().min(1).max(128);
const base = { request_id: z.string().uuid(), agent_id: identity };
const ride = { ...base, ride_id: identity };
export const roverAttemptSchema = z.discriminatedUnion("action", [
  z.object({ ...base, action: z.literal("reserve"), vehicle_id: identity }),
  z.object({ ...ride, action: z.literal("board") }),
  z.object({ ...ride, action: z.literal("travel"), destination_dock_id: identity }),
  z.object({ ...ride, action: z.literal("cancel") }),
  z.object({ ...ride, action: z.literal("exit") }),
]);
export type RoverAttempt = z.infer<typeof roverAttemptSchema>;
const ATTEMPT_KEY = `jarvis.${WORLD_ID}.pending-rover.v1`;

export function readRoverAttempt(): RoverAttempt | null {
  try {
    const result = roverAttemptSchema.safeParse(JSON.parse(sessionStorage.getItem(ATTEMPT_KEY) ?? "null"));
    return result.success ? result.data : null;
  } catch { return null; } // Storage corruption or denial only loses refresh recovery.
}
export function saveRoverAttempt(attempt: RoverAttempt): void {
  try { sessionStorage.setItem(ATTEMPT_KEY, JSON.stringify(attempt)); }
  catch { /* The mounted panel retains the original request for safe retries. */ }
}
export function clearRoverAttempt(requestId: string): void {
  try {
    if (readRoverAttempt()?.request_id === requestId) sessionStorage.removeItem(ATTEMPT_KEY);
  } catch { /* Replaying the retained request remains idempotent. */ }
}

export async function submitRoverAction(attempt: RoverAttempt): Promise<RoverRideRecord> {
  const action = roverAttemptSchema.parse(attempt);
  const basePath = `/api/society/mars/agents/${encodeURIComponent(action.agent_id)}/rides`;
  const path = action.action === "reserve" ? basePath : `${basePath}/${encodeURIComponent(action.ride_id)}/${action.action}`;
  const body = action.action === "reserve"
    ? { request_id: action.request_id, vehicle_id: action.vehicle_id, world_id: WORLD_ID,
      schema_version: 1, layout_version: WORLD.layout_version, graph_version: WORLD.navigation.version }
    : action.action === "travel" ? { request_id: action.request_id, destination_dock_id: action.destination_dock_id }
      : { request_id: action.request_id };
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) throw new MarsApiError(response.status);
  return roverRideSchema.parse(await response.json());
}
