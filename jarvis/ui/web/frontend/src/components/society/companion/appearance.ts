import { z } from "zod";
import { ACCESSORY_SLOTS, type AccessoryChoice } from "./accessories";

export const COMPANION_SHAPES = ["circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop"] as const;
export type SymbolShape = typeof COMPANION_SHAPES[number];
export const COMPANION_COLORS = [
  "#ffffff", "#875b36", "#dd2233", "#ff6801", "#ff9700", "#029858",
  "#00a591", "#1174da", "#804ee1", "#df2a87", "#777777",
  "#e8c89a", "#ff7a6b", "#ffd23f", "#9ccc3a", "#3cc4e8", "#b59cf0",
  "#222222", "#8c1c3a", "#6b7a1f", "#0f5c63", "#1f3a8a", "#f49ac1",
] as const;
/** Default looks hash over the first colours only, so a longer palette never recolours an agent. */
const DEFAULT_COLOR_COUNT = 11;
export const COMPANION_SIZE_M = 0.5;
export const COMPANION_FOLLOW_DISTANCE_M = 1;
/** `PetId` in `jarvis/society/companion.py` (built-in slug or a drawn pet's `u<hex>`). */
export const PET_ID = /^[a-z0-9][a-z0-9-]{0,31}$/;
export const companionSchema = z.object({
  shape: z.enum(COMPANION_SHAPES), color: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  eyes: z.enum(["dots", "lines"]).default("lines"), enabled: z.boolean().default(true),
  // One item id per slot. Ids stay open so a newer catalog never invalidates a saved look.
  accessories: z.record(z.enum(ACCESSORY_SLOTS), z.string().regex(/^[a-z0-9_]{1,40}$/)).default({}),
  // A pet from My Pets worn instead of the shape. Open like accessory ids: a pet
  // missing on this machine shows the shape, it never invalidates the look.
  pet: z.string().regex(PET_ID).optional(),
  // Accept earlier saved slider values, but every presentation uses one scale.
  sizeM: z.number().finite().min(0.25).max(0.8).default(COMPANION_SIZE_M).transform(() => COMPANION_SIZE_M),
  followDistanceM: z.number().finite().min(0.5).max(2).default(COMPANION_FOLLOW_DISTANCE_M).transform(() => COMPANION_FOLLOW_DISTANCE_M),
}).strict();
export type CompanionAppearance = Omit<z.infer<typeof companionSchema>, "accessories"> & { accessories: AccessoryChoice };

function identityHash(identity: string): number {
  let hash = 2166136261;
  for (const character of identity) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
  return hash;
}

export function defaultCompanion(identity: string): CompanionAppearance {
  return {
    shape: COMPANION_SHAPES[identityHash(`shape:${identity}`) % COMPANION_SHAPES.length]!,
    color: COMPANION_COLORS[identityHash(`color:${identity}`) % DEFAULT_COLOR_COUNT]!,
    eyes: "lines", enabled: true, accessories: {}, sizeM: COMPANION_SIZE_M, followDistanceM: COMPANION_FOLLOW_DISTANCE_M,
  };
}

export function resolveCompanion(identity: string, stored?: unknown): CompanionAppearance {
  const parsed = companionSchema.safeParse(stored);
  return parsed.success ? parsed.data : defaultCompanion(identity);
}

/** The same plain black ink in profiles and in the map, without catchlights. */
export function companionEyeColors(_color: string): { eye: string; highlight: string } {
  return { eye: "#101014", highlight: "#101014" };
}
