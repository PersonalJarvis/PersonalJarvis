/**
 * The office dog: a black-and-tan rottweiler with a day of its own. It has a
 * basket in several rooms (lead office, team room, break room) and lives in
 * the room of its current basket: it sleeps, gets up, roams that room,
 * sniffs, sits and lies down, and goes back to bed. Now and then it walks —
 * never jumps — over to another basket and settles in there.
 *
 * The person can pet it (E nearby, or a click): it sits, wags, shows hearts
 * and follows the person around inside its room for a while. Easter egg: a
 * treat jar in the break room hands out a bone; give it to the dog and it
 * begs, catches it, spins twice in the air and carries the bone to its basket
 * to chew on it.
 *
 * Pure schedule + a small store; OfficeDog.tsx moves and animates the figure.
 * Like the agents' idle life, all of it is client-side and costs nothing.
 */
import { create } from "zustand";
import type { Point, Rect } from "./officeLayout";

export type DogActivity =
  | "sleep" | "stretch" | "roam" | "sniff" | "sit" | "lie" | "home" | "move"
  | "petted" | "follow" | "trick" | "chew";

/** The body pose an activity shows; walking poses come from movement, not from here. */
export type DogPose = "sleep" | "stand" | "sit" | "lie" | "sniff" | "beg";

export const DOG_POSE: Record<DogActivity, DogPose> = {
  sleep: "sleep", stretch: "stand", roam: "stand", sniff: "sniff", sit: "sit", lie: "lie", home: "stand", move: "stand",
  petted: "sit", follow: "stand", trick: "beg", chew: "lie",
};

/** Petting reach, metres from the person to the dog. */
export const DOG_PET_RANGE = 1.4;
/** Reach to take a bone from the treat jar. */
export const TREAT_JAR_RANGE = 1.3;
/** How long the dog enjoys being petted, and then follows the person. */
export const DOG_PETTED_MS = 3200;
export const DOG_FOLLOW_MS = 30000;
/** The treat trick (beg, catch, two spins) and the chewing afterwards. */
export const DOG_TRICK_MS = 3600;
export const DOG_CHEW_MS = 18000;
/** Following: trot after the person beyond this distance, stop inside the lower one. */
export const DOG_FOLLOW_FAR = 1.7;
export const DOG_FOLLOW_NEAR = 1.15;
export const DOG_WALK_SPEED = 1.5;
export const DOG_RUN_SPEED = 3.6;
/** Chance that waking up sends the dog over to another basket instead of roaming. */
export const DOG_MOVE_CHANCE = 0.35;

export interface DogStep { activity: DogActivity; durationMs: number }

function between(rng: () => number, min: number, max: number): number {
  return min + rng() * (max - min);
}

/**
 * What the dog does after `current` ends. `roamsLeft` counts the outings
 * before it heads home; `baskets` is how many baskets it could move between.
 * The schedule always ends up back in a basket.
 */
export function nextDogStep(current: DogActivity, roamsLeft: number, rng: () => number, baskets = 1): DogStep & { roamsLeft: number } {
  switch (current) {
    case "sleep":
      if (baskets > 1 && rng() < DOG_MOVE_CHANCE) return { activity: "move", durationMs: Infinity, roamsLeft: 0 };
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
    case "trick":
      // Off to the basket with the bone, then chew there.
      return { activity: "home", durationMs: Infinity, roamsLeft: 0 };
    case "chew":
      return { activity: "sleep", durationMs: between(rng, 20000, 40000), roamsLeft: 0 };
    case "home":
    case "move":
      return { activity: "sleep", durationMs: between(rng, 20000, 50000), roamsLeft: 0 };
  }
}

/** Another basket to move to (never the current one), or the current one when it is the only one. */
export function pickOtherBasket(current: number, count: number, rng: () => number): number {
  if (count <= 1) return current;
  const offset = 1 + Math.floor(rng() * (count - 1));
  return (current + offset) % count;
}

/** Is `p` inside `room`, allowing a margin (a doorway step still counts as inside)? */
export function insideRoom(room: Rect, p: Point, margin = 0.3): boolean {
  return p.x >= room.minX - margin && p.x <= room.maxX + margin && p.z >= room.minZ - margin && p.z <= room.maxZ + margin;
}

/** Where to stand while following: a little behind the person, on the side the dog comes from. */
export function followPoint(person: { x: number; z: number; heading: number }, dog: Point): Point {
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
  /** The person stands at the treat jar. */
  nearJar: boolean;
  /** The person carries a bone from the treat jar. */
  hasBone: boolean;
  /** Bumped on every pet / every bone given; the dog reacts to each new value once. */
  petSeq: number;
  treatSeq: number;
  /** Clicked from afar: pet the dog / take a bone once the walk there brings the person within reach. */
  pending: "pet" | "jar" | null;
  set: (patch: Partial<Pick<OfficeDogState, "near" | "nearJar" | "pending">>) => void;
  pet: () => void;
  takeBone: () => void;
  giveTreat: () => void;
  /** E next to the dog: give the bone when carrying one, else pet it. Next to the jar: take a bone. */
  interact: () => boolean;
}

export const useOfficeDog = create<OfficeDogState>((set, get) => ({
  near: false,
  nearJar: false,
  hasBone: false,
  petSeq: 0,
  treatSeq: 0,
  pending: null,
  set: (patch) => set((s) => (Object.entries(patch).every(([k, v]) => s[k as keyof typeof patch] === v) ? s : patch)),
  pet: () => set((s) => ({ petSeq: s.petSeq + 1, pending: null })),
  takeBone: () => set({ hasBone: true, pending: null }),
  giveTreat: () => set((s) => (s.hasBone ? { hasBone: false, treatSeq: s.treatSeq + 1, pending: null } : s)),
  interact: () => {
    const s = get();
    if (s.near) { if (s.hasBone) s.giveTreat(); else s.pet(); return true; }
    if (s.nearJar && !s.hasBone) { s.takeBone(); return true; }
    return false;
  },
}));
