import { afterEach, describe, expect, it } from "vitest";
import { useCompanionPet } from "./companionPetStore";
import { BUILTIN_COMPANIONS, GIGI_COMPANION, type CompanionPet } from "./petCompanions";

const cocoa: CompanionPet = { id: "cocoa", ...BUILTIN_COMPANIONS.cocoa! };
const worn = (id: string | undefined) => {
  const { catalog } = useCompanionPet.getState();
  return !id ? null : catalog === null ? undefined : catalog[id] ?? null;
};

afterEach(() => useCompanionPet.setState({ pet: GIGI_COMPANION, catalog: null }));

describe("the shared pet catalog an agent's companion reads", () => {
  it("knows nothing before the pets answer, then every pet by id", () => {
    expect(worn("cocoa")).toBeUndefined();
    useCompanionPet.getState().setCatalog({ cocoa, gigi: GIGI_COMPANION });
    expect(worn("cocoa")).toBe(cocoa);
    expect(worn("u0123456789abcdef")).toBeNull();
    expect(worn(undefined)).toBeNull();
  });

  it("keeps an unchanged pet's object on a refetch, so its model never reloads", () => {
    useCompanionPet.getState().setCatalog({ cocoa });
    const before = useCompanionPet.getState().catalog;
    useCompanionPet.getState().setCatalog({ cocoa: { ...cocoa } });
    expect(useCompanionPet.getState().catalog).toBe(before);
    useCompanionPet.getState().setCatalog({ cocoa: { ...cocoa }, gigi: GIGI_COMPANION });
    expect(useCompanionPet.getState().catalog?.cocoa).toBe(cocoa);
  });
});
