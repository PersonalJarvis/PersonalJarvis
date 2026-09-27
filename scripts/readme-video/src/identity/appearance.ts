/**
 * Pure identity helpers from:
 * jarvis/ui/web/frontend/src/components/society/companion/appearance.ts
 * Source SHA-256: BE0421843FA0B58F8C8A8705BF03B60C0C01B32DC93BEA2DBC8B2E3F9160F91B.
 * The original constants and helper bodies are retained. Runtime Zod validation
 * is omitted because this renderer only supplies deterministic default fixtures.
 */
export const COMPANION_SHAPES = ["circle", "squircle", "pill", "triangle", "hexagon", "cloud", "drop"] as const;
export type SymbolShape = typeof COMPANION_SHAPES[number];
export const COMPANION_COLORS = ["#8b5cf6", "#c5dfd4", "#f2a65a", "#7ab6ef", "#ed91aa", "#b7cb78", "#bba7ed", "#79c7c4"] as const;
export interface CompanionAppearance {
  shape: SymbolShape;
  color: string;
  eyes: "dots" | "lines";
  enabled: boolean;
  sizeM: number;
  followDistanceM: number;
}

function identityHash(identity: string): number {
  let hash = 2166136261;
  for (const character of identity) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
  return hash;
}

export function defaultCompanion(identity: string): CompanionAppearance {
  return {
    shape: COMPANION_SHAPES[identityHash(`shape:${identity}`) % COMPANION_SHAPES.length]!,
    color: COMPANION_COLORS[identityHash(`color:${identity}`) % COMPANION_COLORS.length]!,
    eyes: "dots", enabled: true, sizeM: 0.5, followDistanceM: 1,
  };
}

/** Preserve catchlight contrast even for a custom near-black body. */
export function companionEyeColors(color: string): { eye: string; highlight: string } {
  const value = Number.parseInt(color.slice(1), 16);
  const dark = 0.2126 * ((value >> 16) & 255) + 0.7152 * ((value >> 8) & 255) + 0.0722 * (value & 255) < 110;
  return dark ? { eye: "#f5ecd7", highlight: "#16151b" } : { eye: "#19171d", highlight: "#ffffff" };
}
