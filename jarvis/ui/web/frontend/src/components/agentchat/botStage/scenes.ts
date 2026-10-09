/**
 * Which expression the bot plays while it works.
 *
 * A turn's presence ("thinking", "searching", …) maps to a family of scenes.
 * The face carries every scene — its eyes look around, squint, widen, wink,
 * smile — and a tool scene adds at most one tiny prop beside it. The scene
 * inside a family is picked from the turn id and the phase, so two turns
 * rarely look alike, and a long think rotates through the family instead of
 * looping one expression. Pure: the stage only draws what this returns.
 */

import type { PetState } from "@/lib/petStates";
import type { Presence } from "../messengerPresence";

export type Scene =
  // thinking: the face alone
  | "ponder" | "glance" | "squint" | "spark" | "wink" | "roll" | "drowsy" | "beam" | "skyread" | "doubt"
  // work: the face plus one tiny prop
  | "note" | "jot" | "scan" | "flip" | "recall" | "browser" | "setup" | "tune" | "command" | "juggle" | "build"
  // conversation
  | "typing" | "reading" | "done";

export const SCENES: Record<Presence, readonly Scene[]> = {
  thinking: ["ponder", "glance", "squint", "spark", "wink", "roll", "drowsy", "beam", "skyread", "doubt"],
  writing: ["note", "jot"],
  searching: ["scan", "flip"],
  recall: ["recall"],
  browsing: ["browser"],
  setting_up: ["setup", "tune"],
  command: ["command"],
  working: ["juggle", "build"],
  typing: ["typing"],
  reading: ["reading"],
  done: ["done"],
};

/**
 * The row a pet plays for each scene when the agent wears a pet instead of a
 * shape: it has no eyes for the stage to move, so its own animation carries
 * the moment. A pet without a row borrows the nearest one (STATE_FALLBACKS).
 */
export const PET_STATE: Record<Scene, PetState> = {
  ponder: "thinking", glance: "thinking", squint: "thinking", spark: "thinking", wink: "thinking",
  roll: "thinking", drowsy: "thinking", beam: "thinking", skyread: "thinking", doubt: "thinking",
  note: "working", jot: "working", scan: "searching", flip: "searching", recall: "thinking",
  browser: "searching", setup: "working", tune: "working", command: "working", juggle: "working", build: "working",
  typing: "talking", reading: "listening", done: "success",
};

/** How long one thinking expression plays before the next one takes over. */
export const THINK_ROTATE_MS = 4200;
/** No scene is cut short before this: a call that lasts 80 ms must not flash a scene. */
export const MIN_SCENE_MS = 1400;
/** How long the finished turn's smile stays before the stage clears. */
export const DONE_MS = 1700;

function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619) >>> 0;
  return h;
}

/**
 * The scene for a presence: deterministic per turn and phase. `step` advances
 * through the family (a long think rotates); consecutive steps never repeat
 * a scene when the family has more than one.
 */
export function sceneFor(presence: Presence, seed: string, step = 0): Scene {
  const family = SCENES[presence];
  if (family.length === 1) return family[0];
  const start = hash(`${seed}:${presence}`) % family.length;
  return family[(start + step) % family.length];
}
