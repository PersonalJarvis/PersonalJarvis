/**
 * The lead office's dog: a black-and-tan rottweiler with a day of its own.
 * It sleeps in its basket in the corner, gets up, roams the lead office,
 * sniffs, sits and lies down, and goes back to bed. The person can pet it
 * (E nearby, or click it): it sits, wags, shows hearts and then follows the
 * person around for a while before trotting home.
 *
 * Pure schedule + a small store; OfficeDog.tsx moves and animates the figure.
 * Like the agents' idle life, all of it is client-side and costs nothing.
 */
import { create } from "zustand";

export type DogActivity = "sleep" | "stretch" | "roam" | "sniff" | "sit" | "lie" | "home" | "petted" | "follow";

/** The body pose an activity shows; walking poses come from movement, not from here. */
export type DogPose = "sleep" | "stand" | "sit" | "lie" | "sniff";

export const DOG_POSE: Record<DogActivity, DogPose> = {
  sleep: "sleep", stretch: "stand", roam: "stand", sniff: "sniff", sit: "sit", lie: "lie", home: "stand", petted: "sit", follow: "stand",
};

/** Petting reach, metres from the person to the dog. */
export const DOG_PET_RANGE = 1.4;
/** How long the dog enjoys being petted, and then follows the person. */
export const DOG_PETTED_MS = 3200;
export const DOG_FOLLOW_MS = 30000;
/** Following: trot after the person beyond this distance, stop inside the lower one. */
export const DOG_FOLLOW_FAR = 1.7;
export const DOG_FOLLOW_NEAR = 1.15;
export const DOG_WALK_SPEED = 1.5;
export const DOG_RUN_SPEED = 3.6;

export interface DogStep { activity: DogActivity; durationMs: number }

function between(rng: () => number, min: number, max: number): number {
  return min + rng() * (max - min);
}

/**
 * What the dog does after `current` ends. `roamsLeft` counts the outings
 * before it heads home; the schedule always ends up back in the basket.
 */
export function nextDogStep(current: DogActivity, roamsLeft: number, rng: () => number): DogStep & { roamsLeft: number } {
  switch (current) {
    case "sleep":
      return { activity: "stretch", durationMs: 1500, roamsLeft: 2 + Math.floor(rng() * 3) };
    case "stretch":
      return { activity: "roam", durationMs: Infinity, roamsLeft };
    case "roam": {
      // Arrived somewhere: have a look around.
      const r = rng();
      if (r < 0.45) return { activity: "sniff", durationMs: between(rng, 2000, 4000), roamsLeft };
      if (r < 0.8) return { activity: "sit", durationMs: between(rng, 4000, 8000), roamsLeft };
      return { activity: "lie", durationMs: between(rng, 6000, 12000), roamsLeft };
    }
    case "sniff":
    case "sit":
    case "lie":
      return roamsLeft > 1
        ? { activity: "roam", durationMs: Infinity, roamsLeft: roamsLeft - 1 }
        : { activity: "home", durationMs: Infinity, roamsLeft: 0 };
    case "petted":
      return { activity: "follow", durationMs: DOG_FOLLOW_MS, roamsLeft: 0 };
    case "follow":
      return { activity: "home", durationMs: Infinity, roamsLeft: 0 };
    case "home":
      return { activity: "sleep", durationMs: between(rng, 20000, 50000), roamsLeft: 0 };
  }
}

/** Where to stand while following: a little behind the person, on the side the dog comes from. */
export function followPoint(person: { x: number; z: number; heading: number }, dog: { x: number; z: number }): { x: number; z: number } {
  const bx = person.x - Math.sin(person.heading) * DOG_FOLLOW_NEAR;
  const bz = person.z - Math.cos(person.heading) * DOG_FOLLOW_NEAR;
  // Blend towards the dog's own side so it does not cut across the person's feet.
  const dx = dog.x - person.x, dz = dog.z - person.z;
  const d = Math.hypot(dx, dz) || 1;
  return { x: (bx + person.x + (dx / d) * DOG_FOLLOW_NEAR) / 2, z: (bz + person.z + (dz / d) * DOG_FOLLOW_NEAR) / 2 };
}

interface OfficeDogState {
  /** The person stands within petting reach. */
  near: boolean;
  /** Bumped on every pet; the dog reacts to each new value once. */
  petSeq: number;
  /** The dog was clicked from afar: pet it once the walk there brings it within reach. */
  pending: boolean;
  set: (patch: Partial<Pick<OfficeDogState, "near" | "pending">>) => void;
  pet: () => void;
}

export const useOfficeDog = create<OfficeDogState>((set) => ({
  near: false,
  petSeq: 0,
  pending: false,
  set: (patch) => set((s) => (Object.entries(patch).every(([k, v]) => s[k as keyof typeof patch] === v) ? s : patch)),
  pet: () => set((s) => ({ petSeq: s.petSeq + 1, pending: false })),
}));
