import { z } from "zod";

export const COMPANION_SHAPES = ["circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop"] as const;
export type SymbolShape = typeof COMPANION_SHAPES[number];
export const COMPANION_COLORS = ["#8b5cf6", "#c5dfd4", "#f2a65a", "#7ab6ef", "#ed91aa", "#b7cb78", "#bba7ed", "#79c7c4"] as const;
export const COMPANION_SIZE_M = 0.5;
export const COMPANION_FOLLOW_DISTANCE_M = 1;
export const companionSchema = z.object({
  shape: z.enum(COMPANION_SHAPES), color: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  eyes: z.enum(["dots", "lines"]).default("dots"), enabled: z.boolean().default(true),
  // Accept earlier saved slider values, but every presentation uses one scale.
  sizeM: z.number().finite().min(0.25).max(0.8).default(COMPANION_SIZE_M).transform(() => COMPANION_SIZE_M),
  followDistanceM: z.number().finite().min(0.5).max(2).default(COMPANION_FOLLOW_DISTANCE_M).transform(() => COMPANION_FOLLOW_DISTANCE_M),
}).strict();
export type CompanionAppearance = z.infer<typeof companionSchema>;

function identityHash(identity: string): number {
  let hash = 2166136261;
  for (const character of identity) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
  return hash;
}

export function defaultCompanion(identity: string): CompanionAppearance {
  return {
    shape: COMPANION_SHAPES[identityHash(`shape:${identity}`) % COMPANION_SHAPES.length]!,
    color: COMPANION_COLORS[identityHash(`color:${identity}`) % COMPANION_COLORS.length]!,
    eyes: "dots", enabled: true, sizeM: COMPANION_SIZE_M, followDistanceM: COMPANION_FOLLOW_DISTANCE_M,
  };
}

export function resolveCompanion(identity: string, stored?: unknown): CompanionAppearance {
  const parsed = companionSchema.safeParse(stored);
  return parsed.success ? parsed.data : defaultCompanion(identity);
}

/** Preserve catchlight contrast even for a custom near-black body. */
export function companionEyeColors(color: string): { eye: string; highlight: string } {
  const value = Number.parseInt(color.slice(1), 16);
  const dark = 0.2126 * ((value >> 16) & 255) + 0.7152 * ((value >> 8) & 255) + 0.0722 * (value & 255) < 110;
  return dark ? { eye: "#f5ecd7", highlight: "#16151b" } : { eye: "#19171d", highlight: "#ffffff" };
}
