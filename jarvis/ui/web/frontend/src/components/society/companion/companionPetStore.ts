/**
 * The pets the Jarvis Verse draws, shared by every place in the scene that
 * shows one: Jarvis's own companion (the pet chosen in My Pets) and the pets
 * agents wear instead of their shape (`companion.pet`). The stages feed it
 * from the pets query outside the WebGL canvas; the flyer, the lead walker
 * and every agent follower read it inside. An entry only changes when its
 * pet does, so a refetch never reloads a model or a sprite sheet.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { create } from "zustand";
import { pickActivePet, petKeys } from "@/hooks/usePets";
import { fetchPets } from "@/lib/petsApi";
import { companionFor, GIGI_COMPANION, type CompanionPet } from "./petCompanions";

interface CompanionPetState {
  pet: CompanionPet;
  /** Every pet on this machine by id, as a companion; null until the pets answer arrives. */
  catalog: Readonly<Record<string, CompanionPet>> | null;
  setPet: (pet: CompanionPet) => void;
  setCatalog: (catalog: Record<string, CompanionPet>) => void;
}

export function sameCompanion(a: CompanionPet, b: CompanionPet): boolean {
  return a.id === b.id && a.kind === b.kind && a.sheetUrl === b.sheetUrl && a.frameSize === b.frameSize
    && a.idle?.row === b.idle?.row && a.idle?.frames === b.idle?.frames && a.idle?.fps === b.idle?.fps;
}

export const useCompanionPet = create<CompanionPetState>((set, get) => ({
  pet: GIGI_COMPANION,
  catalog: null,
  setPet: (pet) => { if (!sameCompanion(get().pet, pet)) set({ pet }); },
  setCatalog: (next) => {
    const current = get().catalog;
    // Keep each unchanged entry's object, so a worn pet's model never reloads on a refetch.
    const merged: Record<string, CompanionPet> = {};
    let changed = !current || Object.keys(current).length !== Object.keys(next).length;
    for (const [id, pet] of Object.entries(next)) {
      const kept = current?.[id];
      merged[id] = kept && sameCompanion(kept, pet) ? kept : pet;
      if (merged[id] !== kept) changed = true;
    }
    if (changed) set({ catalog: merged });
  },
}));

/**
 * The companion an agent wears instead of its shape: undefined while the
 * pets answer is still on its way (draw nothing yet), null when it wears no
 * pet or this machine has no such pet (draw the shape).
 */
export function useWornPet(petId: string | undefined): CompanionPet | null | undefined {
  return useCompanionPet((s) => !petId ? null : s.catalog === null ? undefined : s.catalog[petId] ?? null);
}

/** Keeps the shared companions in step with My Pets: Jarvis's own, and every pet an agent may wear. */
export function useSyncCompanionPet(): void {
  const { data } = useQuery({ queryKey: petKeys.all, queryFn: fetchPets, staleTime: Infinity });
  const setPet = useCompanionPet((s) => s.setPet);
  const setCatalog = useCompanionPet((s) => s.setCatalog);
  // Until the pets answer arrives the last known companion stays (Gigi on a cold start).
  useEffect(() => {
    if (!data) return;
    const active = pickActivePet(data);
    if (active) setPet(companionFor(active));
    const catalog: Record<string, CompanionPet> = {};
    for (const pet of Array.isArray(data.pets) ? data.pets : []) {
      // A drawn pet without a usable sheet would come back as Gigi; leave it out so the agent keeps its shape.
      const companion = companionFor(pet);
      if (companion.id === pet.id) catalog[pet.id] = companion;
    }
    setCatalog(catalog);
  }, [data, setPet, setCatalog]);
}
