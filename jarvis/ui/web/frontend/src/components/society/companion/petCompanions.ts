/**
 * Which 3D companion keeps the person company in the Jarvis Verse: the pet
 * chosen in Settings -> My Pets (`docs/pets.md`), Gigi when none is chosen.
 *
 * Built-in pets have an authored Blender model
 * (`art/studies/jarvis-pet-companions`, `scripts/art/build_pet_companions.py`)
 * and a way of moving that fits them: the dragon and Gigi fly, the cat walks,
 * the snail crawls. A pet the person drew themselves has only its sprite
 * sheet, so it becomes a voxel figure extruded from its idle frames and
 * floats low beside them.
 */
import type { Pet } from "@/lib/petsApi";

/** How a companion moves; everything but "fly" and "float" stays on the floor. */
export type PetGait = "fly" | "float" | "walk" | "waddle" | "hop" | "bounce" | "crawl";

export interface CompanionPet {
  id: string;
  /** Gigi keeps its own hover model; built-ins load their GLB; drawn pets become voxels. */
  kind: "gigi" | "model" | "voxel";
  gait: PetGait;
  /** Height of the companion in the office, metres (the toy figures are 1.3 m). */
  heightM: number;
  /** Height of the authored model in its GLB, metres (from the build report). */
  modelHeightM: number;
  /** Ground covered by one full gait cycle at office scale, metres; keeps feet from sliding. */
  strideM: number;
  /** Drawn pets: the sprite sheet and its idle row. */
  sheetUrl?: string;
  frameSize?: number;
  idle?: { row: number; frames: number; fps: number };
}

type BuiltinSpec = Omit<CompanionPet, "id" | "sheetUrl" | "frameSize" | "idle">;

export const GIGI_COMPANION: CompanionPet = { id: "gigi", kind: "gigi", gait: "fly", heightM: 0.5, modelHeightM: 0.4, strideM: 1 };

/** Model heights match `art/studies/jarvis-pet-companions/source/build-report.json`. */
export const BUILTIN_COMPANIONS: Readonly<Record<string, BuiltinSpec>> = {
  ember: { kind: "model", gait: "fly", heightM: 0.5, modelHeightM: 0.534, strideM: 1 },
  miso: { kind: "model", gait: "walk", heightM: 0.44, modelHeightM: 0.519, strideM: 0.42 },
  bolt: { kind: "model", gait: "waddle", heightM: 0.38, modelHeightM: 0.367, strideM: 0.3 },
  brew: { kind: "model", gait: "hop", heightM: 0.34, modelHeightM: 0.341, strideM: 0.5 },
  mochi: { kind: "model", gait: "bounce", heightM: 0.28, modelHeightM: 0.25, strideM: 0.45 },
  shelly: { kind: "model", gait: "crawl", heightM: 0.36, modelHeightM: 0.4, strideM: 0.28 },
};

/** A drawn pet floats at this height (its voxel figure), metres. */
export const VOXEL_HEIGHT_M = 0.42;

/** The companion for the pet the app draws as Jarvis (`pickActivePet`); null means Gigi. */
export function companionFor(pet: Pet | null | undefined): CompanionPet {
  if (!pet || pet.id === GIGI_COMPANION.id) return GIGI_COMPANION;
  const builtin = pet.builtin ? BUILTIN_COMPANIONS[pet.id] : undefined;
  if (builtin) return { id: pet.id, ...builtin };
  const idle = pet.animations?.idle;
  if (!pet.sheet_url || !idle || !(pet.frame_size > 0)) return GIGI_COMPANION;
  return {
    id: pet.id, kind: "voxel", gait: "float", heightM: VOXEL_HEIGHT_M, modelHeightM: VOXEL_HEIGHT_M, strideM: 1,
    sheetUrl: pet.sheet_url, frameSize: pet.frame_size,
    idle: { row: idle.row, frames: Math.max(1, idle.frames), fps: Math.max(1, idle.fps) },
  };
}

/** True for companions that stay in the air (Gigi, the dragon, drawn pets). */
export function companionFlies(pet: CompanionPet): boolean {
  return pet.gait === "fly" || pet.gait === "float";
}
