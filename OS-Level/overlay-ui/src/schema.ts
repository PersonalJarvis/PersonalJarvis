// Zod-Schemas, gespiegelt zu den Pydantic-v2-Modellen in
// OS-Level/src/overlay/schema.py. Symmetrie ist Plan AD-15. Phase 9.3
// braucht nur den State-Slice; weitere Envelopes wandern hierher mit
// 9.4ff.

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

// (old, new, reason) — passend zur Signatur von StateBridge.stateChanged
// auf der Python-Seite (window_glow.py).
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
