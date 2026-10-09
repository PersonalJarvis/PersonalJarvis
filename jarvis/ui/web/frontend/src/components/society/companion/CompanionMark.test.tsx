import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";

import { PetStageContext } from "@/components/pets/petStage";
import { petKeys } from "@/hooks/usePets";
import type { Pet, PetsState } from "@/lib/petsApi";
import { CompanionMark } from "./CompanionMark";
import { defaultCompanion, type CompanionAppearance } from "./appearance";

function pet(id: string): Pet {
  return {
    id, name: id, description: "", builtin: true, frame_size: 48,
    animations: { idle: { row: 0, frames: 1, fps: 1, loop: true }, searching: { row: 8, frames: 1, fps: 1, loop: true } },
    sheet_url: `/api/pets/${id}/sheet.png`,
  };
}

const PETS: PetsState = { active: "gigi", scale: 1, bubble: true, strip_always: false, visible: true, pets: [pet("gigi"), pet("cocoa")] };
const look = (patch: Partial<CompanionAppearance> = {}): CompanionAppearance => ({ ...defaultCompanion("agent"), shape: "drop", ...patch });

/** A client already holding the pets answer (or none yet), so nothing is fetched. */
function renderWith(ui: React.ReactElement, state: PetsState | null = PETS) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: state !== null } } });
  if (state) client.setQueryData(petKeys.all, state);
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(cleanup);

describe("CompanionMark", () => {
  it("draws the shape for an agent that wears no pet", () => {
    const { container } = renderWith(<CompanionMark appearance={look()} size={30} />);
    expect(container.querySelector("svg")).not.toBeNull();
    expect(screen.queryByTestId("pet-sprite")).toBeNull();
  });

  it("draws the worn pet instead of the shape, idle at rest and working while the agent works", () => {
    const { container, rerender } = renderWith(<CompanionMark appearance={look({ pet: "cocoa" })} size={30} />);
    expect(container.querySelector("[data-agent-pet='cocoa']")).not.toBeNull();
    expect(screen.getByTestId("pet-sprite").dataset.state).toBe("idle");
    const client = new QueryClient();
    client.setQueryData(petKeys.all, PETS);
    rerender(<QueryClientProvider client={client}><CompanionMark appearance={look({ pet: "cocoa" })} size={30} thinking /></QueryClientProvider>);
    expect(screen.getByTestId("pet-sprite").dataset.state).toBe("working");
  });

  it("plays the row a surrounding stage asks for", () => {
    renderWith(<PetStageContext.Provider value="searching"><CompanionMark appearance={look({ pet: "cocoa" })} size={30} /></PetStageContext.Provider>);
    expect(screen.getByTestId("pet-sprite").dataset.state).toBe("searching");
  });

  it("keeps an empty box while the pets load, so no shape flashes first", () => {
    const { container } = renderWith(<CompanionMark appearance={look({ pet: "cocoa" })} size={30} />, null);
    expect(screen.queryByTestId("pet-sprite")).toBeNull();
    expect(container.querySelector("svg")).toBeNull();
    expect((container.firstElementChild as HTMLElement).style.width).toBe("30px");
  });

  it("falls back to the shape for a pet this machine does not have", () => {
    const { container } = renderWith(<CompanionMark appearance={look({ pet: "u0123456789abcdef" })} size={30} />);
    expect(screen.queryByTestId("pet-sprite")).toBeNull();
    expect(container.querySelector("svg")).not.toBeNull();
  });
});
