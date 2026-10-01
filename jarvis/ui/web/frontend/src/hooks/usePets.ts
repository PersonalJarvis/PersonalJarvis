/**
 * React Query hooks for the desktop pet (Settings → My Pets).
 *
 * One query holds the whole `/api/pets` answer. Every mutation invalidates
 * it, and so does the `PetChanged` bus event (`useWebSocket`), so a change
 * made from another window, the shortcut or the CLI repaints here too. No
 * polling: an idle settings page opens no requests (AP-33).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createPet,
  deletePet,
  fetchPets,
  savePetSettings,
  selectPet,
  setPetVisible,
  type CreatePetInput,
  type PetSettingsPatch,
  type PetsState,
} from "@/lib/petsApi";

export const petKeys = {
  all: ["pets"] as const,
};

export function usePets() {
  return useQuery({ queryKey: petKeys.all, queryFn: fetchPets });
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
