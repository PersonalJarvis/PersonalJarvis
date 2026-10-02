import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";

import { PetMark, petStateForVoice } from "@/components/pets/PetMark";
import { petKeys, pickActivePet } from "@/hooks/usePets";
import type { Pet, PetsState } from "@/lib/petsApi";
import { NO_PET_ID } from "@/lib/petStates";
import { useEventStore } from "@/store/events";

function pet(id: string): Pet {
  return {
    id,
    name: id,
    description: "",
    builtin: true,
    frame_size: 48,
    animations: {
      idle: { row: 0, frames: 1, fps: 1, loop: true },
      thinking: { row: 2, frames: 1, fps: 1, loop: true },
      talking: { row: 3, frames: 1, fps: 1, loop: true },
    },
    sheet_url: `/api/pets/${id}/sheet.png`,
  };
}

function pets(active: string): PetsState {
  return { active, scale: 1, bubble: true, strip_always: false, visible: true, pets: [pet("gigi"), pet("bolt")] };
}

/** A client already holding the pets answer, so nothing is fetched. */
function renderWith(state: PetsState, ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(petKeys.all, state);
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  useEventStore.setState({ voiceState: "idle" });
});

describe("pickActivePet", () => {
  it("returns the chosen pet", () => {
    expect(pickActivePet(pets("bolt"))?.id).toBe("bolt");
  });

  it("falls back to Gigi when none, an unknown one or nothing is chosen", () => {
    expect(pickActivePet(pets(NO_PET_ID))?.id).toBe("gigi");
    expect(pickActivePet(pets("gone"))?.id).toBe("gigi");
    expect(pickActivePet({ active: "", pets: [pet("gigi")] })?.id).toBe("gigi");
  });

  it("has no pet before the answer arrives", () => {
    expect(pickActivePet(undefined)).toBeNull();
    expect(pickActivePet({ active: "bolt", pets: [] })).toBeNull();
  });
});

describe("PetMark", () => {
  it("draws the chosen pet at the exact size, smoothly when shrunk", () => {
    renderWith(pets("bolt"), <PetMark size={20} />);
    expect(screen.getByTestId("pet-mark").dataset.pet).toBe("bolt");
    const sprite = screen.getByTestId("pet-sprite");
    expect(sprite.style.width).toBe("20px");
    const cell = screen.getByTestId("pet-sprite-cell");
    expect(cell.style.backgroundImage).toContain("/api/pets/bolt/sheet.png");
    expect(cell.className).not.toContain("pixelated");
  });

  it("stays pixel-crisp at a whole-number size", () => {
    renderWith(pets("gigi"), <PetMark size={96} />);
    expect(screen.getByTestId("pet-sprite-cell").className).toContain("pixelated");
  });

  it("shows Gigi when the user chose no pet", () => {
    renderWith(pets(NO_PET_ID), <PetMark size={32} />);
    expect(screen.getByTestId("pet-mark").dataset.pet).toBe("gigi");
  });

  it("plays the given state, or follows the voice when reactive", () => {
    renderWith(pets("gigi"), <><PetMark size={32} state="thinking" /><PetMark size={32} reactive /></>);
    const [fixed, reactive] = screen.getAllByTestId("pet-sprite");
    expect(fixed.dataset.state).toBe("thinking");
    expect(reactive.dataset.state).toBe("idle");
  });

  it("keeps an empty box of the final size outside a query client", () => {
    render(<PetMark size={40} />);
    const box = screen.getByTestId("pet-mark");
    expect(box.style.width).toBe("40px");
    expect(screen.queryByTestId("pet-sprite")).toBeNull();
  });
});

describe("petStateForVoice", () => {
  it("maps the voice to the pet's rows", () => {
    expect(petStateForVoice("listening")).toBe("listening");
    expect(petStateForVoice("thinking")).toBe("working");
    expect(petStateForVoice("connecting")).toBe("working");
    expect(petStateForVoice("speaking")).toBe("talking");
    expect(petStateForVoice("error")).toBe("error");
    expect(petStateForVoice("idle")).toBe("idle");
  });
});
