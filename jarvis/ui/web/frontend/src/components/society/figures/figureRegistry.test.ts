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
  fitsBody,
  keepablePartsFor,
  partAssetsFor,
  partsForSlot,
  slotsWithParts,
  stylesWithBases,
  CATALOG,
  type CatalogPart,
} from "./figureRegistry";

/**
 * Styles that are a lane or a reservation, not a look a person picks:
 * `custom` is their own import, `spirit` is Jarvis and there is one of him.
 */
const NOT_PICKABLE = new Set(["custom", "spirit"]);

const pickableStyles = Object.keys(CATALOG.styles).filter((s) => !NOT_PICKABLE.has(s));

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

  /**
   * The point of the wardrobe: a person opening a style finds real choices,
   * not one body and one hat. The floor is deliberately low — it catches a
   * style emptied by a bad rebuild, not a style that is merely small.
   */
  it.each(pickableStyles)("%s offers more than one build", (style) => {
    expect(basesForStyle(style).length).toBeGreaterThan(1);
  });

  it.each(pickableStyles)("%s fills at least three slots for every body it has", (style) => {
    for (const base of basesForStyle(style)) {
      const slots = slotsWithParts(base.archetype, style, base.family ?? null);
      expect(slots.length, `${base.label} (${style})`).toBeGreaterThanOrEqual(3);
    }
  });

  /**
   * The Knight bug: a plate cut for one torso hung in front of a wider one as
   * a narrow board, with the body showing past both edges. A garment that
   * wraps the body is now cut per girth and only offered to the girth it fits.
   */
  it("offers exactly one cut of every wrapping garment per body", () => {
    const wrapping = CATALOG.parts.filter((p) => p.fits_size);
    expect(wrapping.length).toBeGreaterThan(0);
    for (const base of CATALOG.bases) {
      const labels = new Map<string, number>();
      for (const part of wrapping) {
        if (part.archetype !== base.archetype) continue;
        if (!base.styles.some((s) => part.styles.includes(s))) continue;
        if (!fitsBody(part, base.family ?? null, base.fitSize)) continue;
        const key = `${part.slot}/${part.label}`;
        labels.set(key, (labels.get(key) ?? 0) + 1);
      }
      for (const [key, count] of labels) {
        expect(count, `${base.label} is offered ${count} of ${key}`).toBe(1);
      }
    }
  });

  it("never offers a wrapping garment to a body with no cut of its own", () => {
    // The KayKit bodies have their own build; a garment measured against this
    // module's torso must not reach them at all.
    for (const part of CATALOG.parts.filter((p) => p.fits_size)) {
      expect(part.fits_family, part.id).toBeTruthy();
    }
  });

  it("never offers a skull-fitted part on a body of another family", () => {
    for (const part of CATALOG.parts.filter((p) => p.fits_family)) {
      for (const style of part.styles) {
        for (const base of basesForStyle(style, part.archetype)) {
          const offered = partsForSlot(part.slot, part.archetype, style, base.family ?? null);
          const listed = offered.some((p) => p.id === part.id);
          expect(listed, `${part.id} on ${base.label}`).toBe(part.fits_family === base.family);
        }
      }
    }
  });

  it("renders every hanging cloth from both sides, and nothing solid", () => {
    // A cape or a cloak is one open sheet with no inside; front-side-only it
    // vanishes when the wearer turns. Nothing with a volume needs the cost.
    const cloth = CATALOG.parts.filter((p) => /-(cape|cloak)$/.test(p.id));
    expect(cloth.length).toBeGreaterThan(0);
    for (const part of cloth) {
      expect(part.two_sided, part.id).toBe(true);
      expect(part.slot, part.id).toBe("back");
    }
    for (const part of CATALOG.parts.filter((p) => p.two_sided)) {
      expect(part.slot, part.id).toBe("back");
    }
  });
});

describe("switching style or base never leaves a stranded part", () => {
  /** A back piece that belongs to `style` and to nothing else — the clean case. */
  const exclusiveBack = (style: string): CatalogPart | undefined =>
    CATALOG.parts.find(
      (p) => p.slot === "back" && p.archetype === "biped" && p.styles.join() === style,
    );

  it("keeps a part the new style still offers", () => {
    const cape = exclusiveBack("fantasy");
    expect(cape).toBeDefined();
    const kept = keepablePartsFor({ back: cape!.id }, "biped", "fantasy");
    expect(kept).toEqual({ back: cape!.id });
  });

  it("drops a hat the new body cannot wear, even inside one style", () => {
    // Fantasy holds two skull families; a hat cut for one must not survive a
    // move to the other, which the style tag alone would let through.
    const hat = CATALOG.parts.find((p) => p.slot === "headgear" && p.fits_family);
    expect(hat).toBeDefined();
    const other = CATALOG.bases.find(
      (b) => b.archetype === hat!.archetype && b.family !== hat!.fits_family,
    );
    expect(other).toBeDefined();
    expect(keepablePartsFor({ headgear: hat!.id }, hat!.archetype, null, other!.family ?? null)).toEqual({});
    expect(keepablePartsFor({ headgear: hat!.id }, hat!.archetype, null, hat!.fits_family!)).toEqual({
      headgear: hat!.id,
    });
  });

  it("drops a part the new style does not offer", () => {
    const cape = exclusiveBack("fantasy")!;
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
