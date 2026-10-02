import { describe, expect, it } from "vitest";
import type { Pet } from "@/lib/petsApi";
import { BUILTIN_COMPANIONS, companionFlies, companionFor, GIGI_COMPANION } from "./petCompanions";
import { petModelUrl } from "./PetModel";

function pet(over: Partial<Pet>): Pet {
  return {
    id: "x", name: "X", description: "", builtin: false, frame_size: 48,
    animations: { idle: { row: 0, frames: 4, fps: 6, loop: true } }, sheet_url: "/api/pets/x/sheet.png", ...over,
  };
}

describe("companionFor", () => {
  it("is Gigi with no pet, and for the Gigi pet", () => {
    expect(companionFor(null)).toBe(GIGI_COMPANION);
    expect(companionFor(pet({ id: "gigi", builtin: true }))).toBe(GIGI_COMPANION);
  });

  it("gives every built-in pet its own model and a gait that fits it", () => {
    expect(companionFor(pet({ id: "ember", builtin: true })).gait).toBe("fly");
    expect(companionFor(pet({ id: "miso", builtin: true })).gait).toBe("walk");
    expect(companionFor(pet({ id: "shelly", builtin: true })).gait).toBe("crawl");
    for (const id of Object.keys(BUILTIN_COMPANIONS)) {
      expect(companionFor(pet({ id, builtin: true })).kind).toBe("model");
      expect(petModelUrl(id)).toBeTruthy();
    }
  });

  it("lets only the dragon fly among the built-ins, besides Gigi", () => {
    const flyers = Object.keys(BUILTIN_COMPANIONS).filter((id) => companionFlies(companionFor(pet({ id, builtin: true }))));
    expect(flyers).toEqual(["ember"]);
    expect(companionFlies(GIGI_COMPANION)).toBe(true);
  });

  it("turns a drawn pet into a floating voxel figure from its idle row", () => {
    const companion = companionFor(pet({ id: "u0123456789abcdef", animations: { idle: { row: 2, frames: 6, fps: 8, loop: true } } }));
    expect(companion).toMatchObject({
      kind: "voxel", gait: "float", sheetUrl: "/api/pets/x/sheet.png", frameSize: 48, idle: { row: 2, frames: 6, fps: 8 },
    });
    expect(companionFlies(companion)).toBe(true);
  });

  it("falls back to Gigi for a drawn pet without a usable idle row", () => {
    expect(companionFor(pet({ id: "u0123456789abcdef", animations: {} }))).toBe(GIGI_COMPANION);
  });
});
