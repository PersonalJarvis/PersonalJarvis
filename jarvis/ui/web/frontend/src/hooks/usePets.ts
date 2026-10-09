/**
 * React Query hooks for the desktop pet (Settings → My Pets).
 *
 * One query holds the whole `/api/pets` answer. Every mutation invalidates
 * it, and so does the `PetChanged` bus event (`useWebSocket`), so a change
 * made from another window, the shortcut or the CLI repaints here too. No
 * polling: an idle settings page opens no requests (AP-33).
 */
import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NO_PET_ID } from "@/lib/petStates";
import {
  createPet,
  deletePet,
  fetchPets,
  savePetSettings,
  selectPet,
  setPetVisible,
  type CreatePetInput,
  type Pet,
  type PetSettingsPatch,
  type PetsState,
} from "@/lib/petsApi";

export const petKeys = {
  all: ["pets"] as const,
};

export function usePets() {
  return useQuery({ queryKey: petKeys.all, queryFn: fetchPets });
}

/** The pet that stands in when the user has chosen none (or "None"). */
export const DEFAULT_PET_ID = "gigi";

/**
 * The pet the app draws as Jarvis: the one chosen in My Pets, or Gigi when
 * nothing (or "None", which only hides the desktop figure) is chosen.
 */
export function pickActivePet(state: Pick<PetsState, "active" | "pets"> | null | undefined): Pet | null {
  if (!state) return null;
  const pets = Array.isArray(state.pets) ? state.pets : [];
  const id = state.active && state.active !== NO_PET_ID ? state.active : DEFAULT_PET_ID;
  return pets.find((p) => p.id === id) ?? pets.find((p) => p.id === DEFAULT_PET_ID) ?? pets[0] ?? null;
}

/**
 * The active pet for the app's own marks. The cached answer never goes stale
 * on a timer: every mark in the window shares it, and `PetChanged` (or a
 * mutation here) invalidates it, so a new mark mounting opens no request.
 */
export function useActivePet(): Pet | null {
  const { data } = useQuery({ queryKey: petKeys.all, queryFn: fetchPets, staleTime: Infinity });
  return useMemo(() => pickActivePet(data), [data]);
}

/**
 * One pet by id, for an agent that wears it: undefined while the shared
 * answer loads, null when this machine has no such pet (or the answer
 * failed), so the caller can fall back to the agent's shape.
 */
export function usePetById(id: string | undefined): Pet | null | undefined {
  const { data, isError } = useQuery({ queryKey: petKeys.all, queryFn: fetchPets, staleTime: Infinity, enabled: !!id });
  return useMemo(() => {
    if (!id) return null;
    if (!data) return isError ? null : undefined;
    return (Array.isArray(data.pets) ? data.pets : []).find((pet) => pet.id === id) ?? null;
  }, [data, id, isError]);
}

function useInvalidatePets() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: petKeys.all });
}

/** Patch the cached answer so a click paints before the refetch lands. */
function usePatchPets() {
  const qc = useQueryClient();
  return (patch: Partial<Omit<PetsState, "pets">>) =>
    qc.setQueryData<PetsState>(petKeys.all, (current) =>
      current ? { ...current, ...patch } : current,
    );
}

export function useSelectPet() {
  const invalidate = useInvalidatePets();
  const patch = usePatchPets();
  return useMutation({
    mutationFn: selectPet,
    onMutate: (petId: string) => patch({ active: petId }),
    onSettled: invalidate,
  });
}

export function useSavePetSettings() {
  const invalidate = useInvalidatePets();
  const patch = usePatchPets();
  return useMutation({
    mutationFn: savePetSettings,
    onMutate: (settings: PetSettingsPatch) => patch(settings),
    onSettled: invalidate,
  });
}

export function useSetPetVisible() {
  const invalidate = useInvalidatePets();
  const patch = usePatchPets();
  return useMutation({
    mutationFn: setPetVisible,
    onMutate: (visible: boolean) => patch({ visible }),
    onSettled: invalidate,
  });
}

export function useCreatePet() {
  const invalidate = useInvalidatePets();
  return useMutation({
    mutationFn: (input: CreatePetInput) => createPet(input),
    onSuccess: invalidate,
  });
}

export function useDeletePet() {
  const invalidate = useInvalidatePets();
  return useMutation({ mutationFn: deletePet, onSettled: invalidate });
}
