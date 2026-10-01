/**
 * The desktop pet's state vocabulary, mirrored from `jarvis/ui/pets/states.py`.
 *
 * A pet state is one animation row of a sprite sheet. The value crosses
 * Python, the manifest on disk, the REST payload, this file and the
 * `pets.states.*` labels, so `tests/unit/ui/pets/test_pet_state_parity.py`
 * pins the tuple, its ORDER (the sheet's row order) and the fallbacks to the
 * Python module. Change them there first.
 */

/** Every animation state a pet can show, in sprite-sheet row order. */
export const PET_STATES = [
  "idle",
  "listening",
  "thinking",
  "talking",
  "success",
  "error",
  "sleeping",
] as const;

export type PetState = (typeof PET_STATES)[number];

/**
 * Which row to borrow when a manifest has no animation for a state. Every
 * chain ends at `idle`, the one state a manifest must provide.
 */
export const STATE_FALLBACKS: Readonly<Record<Exclude<PetState, "idle">, PetState>> = {
  listening: "idle",
  thinking: "listening",
  talking: "listening",
  success: "idle",
  error: "idle",
  sleeping: "idle",
};

/** The pet id that shows the control strip with no figure ("None" in the UI). */
export const NO_PET_ID = "none";

/** Frame edge lengths a sprite sheet may use, in source pixels. */
export const FRAME_SIZES = [32, 48, 64] as const;

/** Upper bound of frames in one animation row. */
export const MAX_FRAMES_PER_STATE = 8;

/** One animation row as the REST payload describes it. */
export interface PetAnimation {
  row: number;
  frames: number;
  fps: number;
  loop: boolean;
  /** The row's last cells are an accent (a blink) shown once every `accent_every` loops. */
  accent_frames?: number;
  accent_every?: number;
}

/**
 * The animation that plays for `state`: its own row, or the first row found
 * along the fallback chain. `null` only for a manifest without `idle`, which
 * the backend loader rejects, so a caller treats it as "draw nothing".
 */
export function resolvePetAnimation(
  animations: Partial<Record<string, PetAnimation>>,
  state: PetState,
): PetAnimation | null {
  let current: PetState | undefined = state;
  // The chain is at most three hops long; the bound only guards a malformed
  // table from looping forever.
  for (let hop = 0; current && hop <= PET_STATES.length; hop += 1) {
    const animation = animations[current];
    if (animation) return animation;
    current = current === "idle" ? undefined : STATE_FALLBACKS[current];
  }
  return null;
}
