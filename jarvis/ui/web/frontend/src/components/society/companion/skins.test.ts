import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { createElement } from "react";
import { AgentSymbol } from "../AgentSymbol";
import {
  angleVector, encodeDesignCode, paletteFromPixels, parseDesignImport, sameSkin, skinBlend, skinEffect,
  skinSchema, SKIN_PRESETS, type CompanionSkin,
} from "./skins";

const GALAXY: CompanionSkin = { colors: ["#e05cc5", "#6a2fc2", "#160f3d"], pattern: "radial", angle: 135, effect: "stars", name: "Galaxy" };

describe("companion designs", () => {
  it("ships only valid presets", () => {
    for (const preset of SKIN_PRESETS) expect(skinSchema.safeParse({ ...preset.skin, name: preset.id }).success).toBe(true);
  });

  it("round-trips a design code, names with umlauts included", () => {
    const skin = { ...GALAXY, name: "Grüne Galaxie" };
    const code = encodeDesignCode(skin);
    expect(code.startsWith("jarvis-design:")).toBe(true);
    expect(parseDesignImport(code)).toEqual(skin);
  });

  it("imports JSON of a design, a companion or a template avatar", () => {
    expect(parseDesignImport(JSON.stringify(GALAXY))).toEqual(GALAXY);
    expect(parseDesignImport(JSON.stringify({ companion: { shape: "circle", skin: GALAXY } }))).toEqual(GALAXY);
  });

  it("imports CSS gradients from gradient sites", () => {
    expect(parseDesignImport("background: linear-gradient(90deg, #FF2FD6 0%, rgba(47, 243, 255, 1) 100%);"))
      .toEqual({ colors: ["#ff2fd6", "#2ff3ff"], pattern: "linear", angle: 90, effect: "none", name: "" });
    expect(parseDesignImport("linear-gradient(to bottom right, #fc0, #f06, #60f)")?.angle).toBe(135);
    expect(parseDesignImport("radial-gradient(circle, #111111, #222222, #333333, #444444, #555555, #666666)"))
      .toMatchObject({ pattern: "radial", colors: ["#111111", "#333333", "#444444", "#666666"] });
  });

  it("takes a plain list of hex colours and refuses junk", () => {
    expect(parseDesignImport("#123456 #abcdef")?.colors).toEqual(["#123456", "#abcdef"]);
    expect(parseDesignImport("hello")).toBeNull();
    expect(parseDesignImport("jarvis-design:%%%")).toBeNull();
    expect(parseDesignImport("{ broken")).toBeNull();
    expect(parseDesignImport(JSON.stringify({ colors: ["#123456"] }))).toBeNull();
  });

  it("blends a design into the one colour other surfaces use", () => {
    expect(skinBlend(["#000000", "#000000"])).toBe("#000000");
    expect(skinBlend(["#ff0000", "#ff0000", "#ff0000"])).toBe("#ff0000");
    expect(skinBlend(["#000000", "#ffffff"])).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("points the gradient the CSS way and compares looks by value", () => {
    expect(angleVector(90)).toEqual([0, 0.5, 1, 0.5]);
    expect(angleVector(180)).toEqual([0.5, 0, 0.5, 1]);
    expect(sameSkin(GALAXY, { ...GALAXY, name: "Other", colors: ["#E05CC5", "#6a2fc2", "#160f3d"] })).toBe(true);
    expect(sameSkin(GALAXY, { ...GALAXY, effect: "glow" })).toBe(false);
    expect(skinEffect({ ...GALAXY, effect: "futureeffect" })).toBe("none");
  });

  it("finds distinct dominant colours in a picture", () => {
    const pixels = new Uint8ClampedArray(4 * 100);
    for (let i = 0; i < 100; i++) {
      const [r, g, b] = i < 50 ? [20, 10, 60] : i < 80 ? [200, 60, 180] : [21, 11, 61];
      pixels.set([r, g, b, 255], i * 4);
    }
    const palette = paletteFromPixels(pixels, 3);
    expect(palette[0]).toBe("#c83cb4");
    expect(palette).toContain("#140a3c");
  });

  it("paints the symbol with the design and its effect", () => {
    const { container } = render(createElement(AgentSymbol, { shape: "circle", color: "#7a3fb8", size: 40, skin: GALAXY }));
    expect(container.querySelector("radialGradient stop")?.getAttribute("stop-color")).toBe("#e05cc5");
    expect(container.querySelector("[data-agent-body]")?.getAttribute("data-skin")).toBe("stars");
    expect(container.querySelectorAll("[data-skin-effect='stars'] circle").length).toBeGreaterThan(5);
    const plain = render(createElement(AgentSymbol, { shape: "circle", color: "#7a3fb8", size: 40 }));
    expect(plain.container.querySelector("[data-skin-effect]")).toBeNull();
  });
});
