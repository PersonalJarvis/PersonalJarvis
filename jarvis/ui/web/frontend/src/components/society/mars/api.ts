import { z } from "zod";
import { containsCredential } from "./credentialInput";

export const COMMAND_STATES = ["queued", "active", "completed", "failed", "canceled", "interrupted", "unknown"] as const;
export type CommandState = typeof COMMAND_STATES[number];
export const WORLD_ID = "mars:ordinary" as const;

const commandSchema = z.object({
  command_id: z.string(), agent_id: z.string(), request_id: z.string(), trace_id: z.string(),
  world_id: z.literal(WORLD_ID), state: z.enum(COMMAND_STATES),
  task_ref: z.string().nullable(), result_ref: z.string().nullable(),
  reason: z.string(), cancel_requested: z.boolean(),
});
export type MarsCommand = z.infer<typeof commandSchema>;
const snapshotSchema = z.object({
  world_id: z.literal(WORLD_ID), schema_version: z.literal(1), layout_version: z.literal(1),
  seq: z.number().int().nonnegative(), commands: z.array(commandSchema).max(200),
});
export type MarsSnapshot = z.infer<typeof snapshotSchema>;
const rosterSchema = z.object({
  agents: z.array(z.object({
    agent_id: z.string(), name: z.string(), state: z.string(),
  })).max(2000),
});
export interface DraftAttempt { request_id: string; draft: string; agent_id: string }
const ATTEMPT_KEY = "jarvis.mars:ordinary.pending-draft.v1";
export function readDraftAttempt(): DraftAttempt | null {
  try {
    const raw = sessionStorage.getItem(ATTEMPT_KEY);
    if (!raw) return null;
    const value = z.object({
      request_id: z.string().uuid(), draft: z.string().min(1).max(4000),
      agent_id: z.string().min(1).max(128),
    }).strict().safeParse(JSON.parse(raw));
    return value.success ? value.data : null;
  } catch {
    // Storage denial costs refresh recovery only; in-memory retries retain their ID.
    return null;
  }
}
export function saveDraftAttempt(value: DraftAttempt | null): void {
  if (value && containsCredential(value.draft)) return;
  try {
    if (value) sessionStorage.setItem(ATTEMPT_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(ATTEMPT_KEY);
  } catch {
    // Keep the current in-memory request on private/locked-down browser profiles.
  }
}
export function clearDraftAttempt(requestId: string): void {
  if (readDraftAttempt()?.request_id === requestId) saveDraftAttempt(null);
}
export class MarsApiError extends Error {
  constructor(readonly status: number, readonly credentialInput = false) { super("mars_request_failed"); }
}
async function request(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`/api/society${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let credentialInput = false;
    try {
      const error = await response.json();
      credentialInput = error?.detail?.reason === "credential_input_use_api_key_settings";
    } catch {
      // Non-JSON gateway errors are represented only by their status, never their body.
    }
    throw new MarsApiError(response.status, credentialInput);
  }
  return response.json();
}
export async function fetchMarsSnapshot(signal?: AbortSignal): Promise<MarsSnapshot> {
  return snapshotSchema.parse(await request("/mars/snapshot", { signal }));
}
export async function fetchMarsRoster(signal?: AbortSignal) {
  return rosterSchema.parse(await request("/agents", { signal })).agents;
}
export async function submitMarsDraft(attempt: DraftAttempt): Promise<MarsCommand> {
  return commandSchema.parse(await request(`/mars/agents/${encodeURIComponent(attempt.agent_id)}/commands`, {
    method: "POST",
    body: JSON.stringify({
      request_id: attempt.request_id, draft: attempt.draft,
      world_id: WORLD_ID, schema_version: 1, layout_version: 1,
    }),
  }));
}
export async function cancelMarsCommand(command: MarsCommand): Promise<MarsCommand> {
  return commandSchema.parse(await request(
    `/mars/agents/${encodeURIComponent(command.agent_id)}/commands/${encodeURIComponent(command.command_id)}/cancel`,
    { method: "POST" },
  ));
}
