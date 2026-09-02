/**
 * The creator's promise, checked against the SHIPPED catalog: every style it
 * offers has a figure, every figure and part it puts in the same row can
 * actually be worn together, and a switch never leaves a recipe holding
 * something the new base cannot wear.
 *
 * These run on `catalog.json` itself — the file the build generates — so a
 * rebuild that drops a style's last base, or tags a part onto a style whose
 * bodies it was never cut for, fails here instead of in someone's dialog.
 */
import { describe, expect, it } from "vitest";

import {
  archetypeOfBase,
  basesForStyle,
  catalogBaseFor,
  keepablePartsFor,
  partAssetsFor,
  partsForSlot,
  slotsWithParts,
  stylesWithBases,
  CATALOG,
  type CatalogPart,
} from "./figureRegistry";

/** Styles that are a lane, not a look: `custom` is the person's own import. */
const IMPORT_ONLY = new Set(["custom"]);

const pickableStyles = Object.keys(CATALOG.styles).filter((s) => !IMPORT_ONLY.has(s));

describe("the catalog covers every style the creator shows", () => {
  it.each(pickableStyles)("%s has at least one base", (style) => {
    expect(basesForStyle(style).length).toBeGreaterThan(0);
  });

  it("reports exactly the styles that have a base, never the import lane", () => {
    expect(stylesWithBases().sort()).toEqual(pickableStyles.sort());
  });

  it("gives every base a label, a palette and at least an idle", () => {
    for (const base of CATALOG.bases) {
      expect(base.label, base.id).toBeTruthy();
      expect(base.palette.skin, base.id).toMatch(/^#[0-9a-f]{6}$/i);
      expect(base.clips, base.id).toContain("idle");
    }
  });
});

describe("a part is only ever offered where it fits", () => {
  it("never tags a part onto a style that has no base of its archetype", () => {
    for (const part of CATALOG.parts) {
      for (const style of part.styles) {
        const hosts = basesForStyle(style, part.archetype);
        expect(hosts.length, `${part.id} is tagged ${style}`).toBeGreaterThan(0);
      }
    }
  });

  it("only lists slots that actually hold something for the style", () => {
    for (const style of pickableStyles) {
      for (const base of basesForStyle(style)) {
        for (const slot of slotsWithParts(base.archetype, style)) {
          expect(partsForSlot(slot, base.archetype, style).length).toBeGreaterThan(0);
        }
      }
    }
  });

  it("gives every biped style something to put on", () => {
    for (const style of pickableStyles) {
      const bipeds = basesForStyle(style, "biped");
      if (bipeds.length === 0) continue;
      expect(slotsWithParts("biped", style).length, style).toBeGreaterThan(0);
    }
  });

  it("marks the flat capes two-sided and nothing solid", () => {
    const twoSided = CATALOG.parts.filter((p) => p.two_sided).map((p) => p.id);
    expect(twoSided.sort()).toEqual(
      CATALOG.parts.filter((p) => p.slot === "back" && p.id.endsWith("-cape")).map((p) => p.id).sort(),
    );
  });
});

describe("switching style or base never leaves a stranded part", () => {
  const capeOf = (style: string): CatalogPart | undefined =>
    CATALOG.parts.find((p) => p.slot === "back" && p.styles.includes(style));

  it("keeps a part the new style still offers", () => {
    const cape = capeOf("fantasy");
    expect(cape).toBeDefined();
    const kept = keepablePartsFor({ back: cape!.id }, "biped", "fantasy");
    expect(kept).toEqual({ back: cape!.id });
  });

  it("drops a part the new style does not offer", () => {
    const cape = capeOf("fantasy")!;
    expect(cape.styles).not.toContain("scifi");
    expect(keepablePartsFor({ back: cape.id }, "biped", "scifi")).toEqual({});
  });

  it("drops every biped part when the base becomes a spirit", () => {
    const worn = Object.fromEntries(
      CATALOG.parts.filter((p) => p.archetype === "biped").map((p) => [p.slot, p.id]),
    );
    expect(keepablePartsFor(worn, "spirit", null)).toEqual({});
  });

  it("drops an id that no longer exists", () => {
    expect(keepablePartsFor({ back: "back-gone" }, "biped", null)).toEqual({});
  });
});

describe("a recipe is read for what it names, not what it claims", () => {
  it("finds a base whose archetype drifted from the recipe", () => {
    // Gigi is a spirit; a row still carrying `biped` must not silently
    // render the ranger instead.
    expect(catalogBaseFor({ archetype: "biped", base: "gigi" })?.archetype).toBe("spirit");
    expect(archetypeOfBase("gigi")).toBe("spirit");
  });

  it("resolves the legacy size ids to the base they became", () => {
    expect(catalogBaseFor({ archetype: "biped", base: "medium" })?.base).toBe("rogue");
  });

  it("refuses to hang a biped part on a spirit", () => {
    const cape = CATALOG.parts.find((p) => p.archetype === "biped" && p.slot === "back")!;
    const worn = { archetype: "spirit" as const, base: "gigi", parts: { back: cape.id } };
    expect(partAssetsFor(worn)).toEqual([]);
  });
});
