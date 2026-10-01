import { describe, expect, it } from "vitest";

import { recipeKey, type FigureRecipe } from "../figures/figureRecipe";
import { HAIR_STYLES, OUTFIT_IDS, toyLookFor } from "./toyFigureModel";
import { colourwayIndexOf, dressIn, OUTFITS, pinLook, randomLook, withEyewear, withHair, withHairColour, withSkin } from "./wardrobe";

const BASE: FigureRecipe = { contract: 1, archetype: "biped", base: "toon", parts: {}, palette: { primary: "#3f9d5a", secondary: "#2b4a8b" } };
const HEX = /^#[0-9a-f]{6}$/i;

describe("wardrobe catalogue", () => {
  it("offers every outfit the figure can draw, each with valid colourways", () => {
    expect(OUTFITS.map((o) => o.id).sort()).toEqual([...OUTFIT_IDS].sort());
    for (const outfit of OUTFITS) {
      expect(outfit.colourways.length).toBeGreaterThan(0);
      for (const way of outfit.colourways) for (const c of [way.primary, way.secondary, way.accent, way.inner, way.shoes]) expect(c).toMatch(HEX);
    }
  });

  it("has the signature black leather jacket", () => {
    const look = toyLookFor(dressIn(BASE, "leather"), "x");
    expect(look.outfit).toBe("leather");
    expect(look.shirt).toBe("#131316");
  });
});

describe("dressing a recipe", () => {
  it("applies the colourway to the look and recognises it again", () => {
    const suit = dressIn(BASE, "suit", 1);
    const look = toyLookFor(suit, "x");
    const navy = OUTFITS.find((o) => o.id === "suit")!.colourways[1];
    expect(look).toMatchObject({ outfit: "suit", shirt: navy.primary, pants: navy.secondary, shirtAccent: navy.accent, inner: navy.inner, shoes: navy.shoes });
    expect(colourwayIndexOf(suit)).toBe(1);
  });

  it("keeps the hair when the colours change", () => {
    const hair = toyLookFor(BASE, "").hairStyle;
    for (const outfit of OUTFIT_IDS) expect(toyLookFor(dressIn(BASE, outfit, 2), "").hairStyle).toBe(hair);
    expect(toyLookFor(withSkin(BASE, "#8a5a3c"), "").hairStyle).toBe(hair);
    expect(toyLookFor(withHairColour(BASE, "#d9b36c"), "").hairStyle).toBe(hair);
  });

  it("reads eyewear and falls back safely on unknown values", () => {
    expect(toyLookFor(withEyewear(BASE, "shades"), "").eyewear).toBe("shades");
    const unknown = toyLookFor({ ...BASE, eyewear: "monocle", outfit: "cape" }, "");
    expect(unknown.eyewear).toBe("none");
    expect(unknown.outfit).toBe(toyLookFor(BASE, "").outfit);
  });

  it("keeps an undressed agent's default outfit when only skin, hair or glasses change", () => {
    const before = toyLookFor(BASE, "");
    for (const edited of [withSkin(BASE, "#6b4430"), withHairColour(BASE, "#c0392b"), withHair(BASE, "buzz"), withEyewear(BASE, "glasses")]) {
      expect(toyLookFor(edited, "")).toMatchObject({ outfit: before.outfit, shirt: before.shirt, pants: before.pants, inner: before.inner });
    }
    expect(pinLook(BASE).outfit).toBe(before.outfit);
  });

  it("produces complete random looks from the catalogue", () => {
    for (let i = 0; i < 30; i += 1) {
      const look = toyLookFor(randomLook(BASE, `seed-${i}`), "");
      expect(OUTFIT_IDS).toContain(look.outfit);
      expect(HAIR_STYLES).toContain(look.hairStyle);
    }
  });

  it("leaves the key of an undressed recipe unchanged", () => {
    expect(recipeKey(BASE).endsWith("|")).toBe(true);
    expect(recipeKey(dressIn(BASE, "suit"))).not.toBe(recipeKey(BASE));
  });
});
