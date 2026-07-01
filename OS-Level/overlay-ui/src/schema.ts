// Zod schemas, mirrored to the Pydantic v2 models in
// OS-Level/src/overlay/schema.py. Symmetry is Plan AD-15. Phase 9.3
// only needs the state slice; more envelopes will move here in
// 9.4 and beyond.

import { z } from "zod";

export const StateNameSchema = z.enum([
  "idle",
  "listening",
  "thinking",
  "typing",
  "clicking",
  "speaking",
  "error",
  "hidden",
]);

export type StateName = z.infer<typeof StateNameSchema>;

// (old, new, reason) — matches the signature of StateBridge.stateChanged
// on the Python side (window_glow.py).
export const StateChangeSchema = z.object({
  old: StateNameSchema,
  next: StateNameSchema,
  reason: z.enum(["wakeword", "user", "tool", "timeout", "error", ""]),
});

export type StateChange = z.infer<typeof StateChangeSchema>;

export const GLOW_ACTIVE_STATES: ReadonlySet<StateName> = new Set<StateName>([
  "typing",
  "clicking",
]);

export function isGlowActive(state: StateName): boolean {
  return GLOW_ACTIVE_STATES.has(state);
}
