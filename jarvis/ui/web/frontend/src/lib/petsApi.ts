/**
 * Client for `/api/pets` — the desktop pet overlay (`docs/pets.md`).
 *
 * Every write returns once the value is on disk and applied to the desktop;
 * the backend then publishes `PetChanged`, which `useWebSocket` turns into a
 * refetch of the pets query, so a second open window follows along.
 */
import type { PetAnimation } from "@/lib/petStates";

export interface Pet {
  id: string;
  name: string;
  description: string;
  builtin: boolean;
  frame_size: number;
  /** Keyed by pet state; a custom pet may leave states out (see STATE_FALLBACKS). */
  animations: Partial<Record<string, PetAnimation>>;
  sheet_url: string;
}

export interface PetsState {
  /** A pet id, or `NO_PET_ID` for the control strip alone. */
  active: string;
  scale: number;
  bubble: boolean;
  visible: boolean;
  pets: Pet[];
}

export interface PetSettingsPatch {
  scale?: number;
  bubble?: boolean;
}

export interface CreatePetInput {
  sheet: File;
  name: string;
  description: string;
  frameSize?: number;
  /** The text of an optional `pet.json`; sent as a plain form field. */
  manifest?: string | null;
}

/** A refused request, carrying the backend's `detail` when it sent one. */
export class PetsRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "PetsRequestError";
    this.status = status;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = (await response.json().catch(() => null)) as T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : "";
    throw new PetsRequestError(
      detail || `Pets request failed (${response.status}).`,
      response.status,
    );
  }
  return body as T;
}

function putJson<T>(url: string, payload: unknown): Promise<T> {
  return request<T>(url, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** An empty sheet in the standard layout (48 px cells, rows in PET_STATES order). */
export const PET_TEMPLATE_URL = "/api/pets/template.png";

export function fetchPets(): Promise<PetsState> {
  return request<PetsState>("/api/pets");
}

export function selectPet(petId: string): Promise<unknown> {
  return putJson("/api/pets/active", { pet_id: petId });
}

export function savePetSettings(patch: PetSettingsPatch): Promise<unknown> {
  return putJson("/api/pets/settings", patch);
}

export function setPetVisible(visible: boolean): Promise<unknown> {
  return request("/api/pets/visibility", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ visible }),
  });
}

export function createPet(input: CreatePetInput): Promise<Pet> {
  const form = new FormData();
  form.append("sheet", input.sheet);
  form.append("name", input.name);
  form.append("description", input.description);
  if (input.frameSize) form.append("frame_size", String(input.frameSize));
  if (input.manifest) form.append("manifest", input.manifest);
  // No content-type header: the browser writes the multipart boundary itself.
  return request<Pet>("/api/pets", { method: "POST", body: form });
}

export function deletePet(petId: string): Promise<unknown> {
  return request(`/api/pets/${encodeURIComponent(petId)}`, { method: "DELETE" });
}
