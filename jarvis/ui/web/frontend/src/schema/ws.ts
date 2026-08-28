// Stays on zod v3, deliberately. These schemas sit in the STARTUP chunk (the
// socket opens as the app mounts), so their cost is paid before the first
// paint and `zod/v4` looked like free savings. Measured on the real bundle it
// is the opposite: v4's core added ~30 KB here. Re-measure before trying again.
import { z } from "zod";

/** Envelope emitted by server over /ws for every bus-event.
 *
 * Field-names match `jarvis.ui.web.schema.WSEventEnvelope` (pydantic, server-side).
 * Keep the two in sync — otherwise the zod parser silently drops events.
 */
export const WSEventEnvelope = z.object({
  type: z.literal("event"),
  event_name: z.string(),
  source_layer: z.string().default(""),
  timestamp_ns: z.number(),
  trace_id: z.string(),
  payload: z.record(z.unknown()).default({}),
});
export type WSEventEnvelopeT = z.infer<typeof WSEventEnvelope>;

/** Welcome frame — sent once after WS handshake completes. */
export const WSWelcome = z.object({
  type: z.literal("welcome"),
  session_id: z.string(),
  version: z.string().optional(),
});
export type WSWelcomeT = z.infer<typeof WSWelcome>;

/** Ephemeral normalized native voice levels for canvas animation — the
 *  microphone and the assistant's own output. `output` is optional so a page
 *  talking to an older backend still parses the frame; absent means "this
 *  server cannot tell us", which is NOT the same as a measured zero. */
export const WSAudioLevel = z.object({
  type: z.literal("audio.level"),
  input: z.number().min(0).max(1),
  output: z.number().min(0).max(1).optional(),
});
export type WSAudioLevelT = z.infer<typeof WSAudioLevel>;

/** Free-form user text / voice message heading inward. */
export const WSMessageIn = z.object({
  type: z.literal("message"),
  kind: z.enum(["text", "voice", "system", "action"]),
  content: z.string(),
  metadata: z.record(z.unknown()).optional(),
});
export type WSMessageInT = z.infer<typeof WSMessageIn>;

/** Structured commands (switch provider, pause, terminal control, etc.). */
export const WSCommand = z.object({
  type: z.literal("command"),
  action: z.enum([
    "ping",
    "provider_switch",
    "test_event",
    "set_state",
    "terminal.spawn",
    "terminal.input",
    "terminal.resize",
    "terminal.close",
    // Chat mic-dictation: payload {mode:"start"|"stop"} — transcribe-only.
    "stt_dictate",
    // Drag-drop a mission card onto the Jarvis dock — pulls the sub-agent
    // task into the live conversation context.
    "mission.inject",
  ]),
  payload: z.record(z.unknown()).default({}),
});
export type WSCommandT = z.infer<typeof WSCommand>;

/** Direct response to a terminal.spawn — carries the assigned terminal_id. */
export const WSTerminalSpawned = z.object({
  type: z.literal("terminal.spawned"),
  payload: z.object({
    terminal_id: z.string(),
    shell_id: z.string(),
    pid: z.number(),
  }),
});
export type WSTerminalSpawnedT = z.infer<typeof WSTerminalSpawned>;

export const WSInbound = z.discriminatedUnion("type", [WSMessageIn, WSCommand]);
export const WSOutbound = z.discriminatedUnion("type", [
  WSEventEnvelope,
  WSWelcome,
  WSAudioLevel,
  WSTerminalSpawned,
]);
