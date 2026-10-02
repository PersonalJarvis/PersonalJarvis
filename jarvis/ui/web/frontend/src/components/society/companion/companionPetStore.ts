/**
 * The companion the Jarvis Verse draws for Jarvis, shared by every place in
 * the scene that shows it. The office stage feeds it from the pets query
 * (outside the WebGL canvas); the flyer and the lead walker read it inside.
 * The object only changes when the pet does, so a refetch never reloads a
 * model or a sprite sheet.
 */
import { useEffect } from "react";
import { create } from "zustand";
import { useActivePet } from "@/hooks/usePets";
import { companionFor, GIGI_COMPANION, type CompanionPet } from "./petCompanions";

interface CompanionPetState {
  pet: CompanionPet;
  setPet: (pet: CompanionPet) => void;
}

export function sameCompanion(a: CompanionPet, b: CompanionPet): boolean {
  return a.id === b.id && a.kind === b.kind && a.sheetUrl === b.sheetUrl && a.frameSize === b.frameSize
    && a.idle?.row === b.idle?.row && a.idle?.frames === b.idle?.frames && a.idle?.fps === b.idle?.fps;
}

export const useCompanionPet = create<CompanionPetState>((set, get) => ({
  pet: GIGI_COMPANION,
  setPet: (pet) => { if (!sameCompanion(get().pet, pet)) set({ pet }); },
}));

/** Keeps the shared companion in step with the pet chosen in My Pets. */
export function useSyncCompanionPet(): void {
  const active = useActivePet();
  const setPet = useCompanionPet((s) => s.setPet);
  // Until the pets answer arrives the last known companion stays (Gigi on a cold start).
  useEffect(() => { if (active) setPet(companionFor(active)); }, [active, setPet]);
}
