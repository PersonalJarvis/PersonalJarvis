import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import en from "@/i18n/locales/society/en.json";
import de from "@/i18n/locales/society/de.json";
import es from "@/i18n/locales/society/es.json";
import { AgentSymbol } from "../AgentSymbol";
import {
  ACCESSORY_CATALOG, ACCESSORY_IDS_BY_SLOT, ACCESSORY_ITEMS, ACCESSORY_SLOTS, OFFERED_SLOTS, randomAccessories, resolveFill,
  slotDepthM, symbolViewBox, wornAccessories,
} from "./accessories";
import { COMPANION_SHAPES, companionSchema, resolveCompanion } from "./appearance";

type Labels = { society: { companion: { items: Record<string, string>; slots: Record<string, string> } } };

describe("agent symbol accessories", () => {
  it("labels every item and slot in every society locale", () => {
    for (const locale of [en, de, es] as unknown as Labels[]) {
      const { items, slots } = locale.society.companion;
      expect(Object.keys(items).sort()).toEqual(ACCESSORY_ITEMS.map(item => item.id).sort());
      expect(Object.keys(slots).sort()).toEqual([...ACCESSORY_SLOTS].sort());
    }
  });

  it("anchors every slot on every silhouette", () => {
    expect(ACCESSORY_CATALOG.slots).toEqual([...ACCESSORY_SLOTS]);
    for (const shape of COMPANION_SHAPES) {
      for (const slot of ACCESSORY_SLOTS) expect(ACCESSORY_CATALOG.shapes[shape].anchors[slot]).toHaveLength(3);
    }
    // A small, finished set: every offered slot has items, every item has a home slot.
    expect(ACCESSORY_ITEMS).toHaveLength(10);
    expect(OFFERED_SLOTS).toEqual(["outfit", "mouth", "face", "head"]);
  });

  it("keeps a bare symbol at its original frame and grows only for worn items", () => {
    expect(symbolViewBox("circle", [])).toBe("0 0 40 44");
    const [x, y, w, h] = symbolViewBox("circle", wornAccessories({ head: "top_hat" })).split(" ").map(Number);
    expect(y).toBeLessThan(0);
    expect(x + w).toBeGreaterThanOrEqual(40);
    expect(w / h).toBeCloseTo(40 / 44, 3);
  });

  it("skips unknown or misplaced ids from other builds instead of failing", () => {
    expect(wornAccessories({ head: "future_hat", face: "crown", mouth: "cigar" }).map(item => item.id)).toEqual(["cigar"]);
  });

  it("draws worn items and clips clothing to the body", () => {
    const { container } = render(<AgentSymbol shape="hexagon" color="#7ab6ef" size={40} accessories={{ head: "crown", outfit: "suit", mouth: "cigar" }} />);
    const drawn = [...container.querySelectorAll("[data-accessory]")].map(node => node.getAttribute("data-accessory"));
    expect(drawn).toEqual(expect.arrayContaining(["crown", "suit", "cigar"]));
    const mask = container.querySelector("mask");
    expect(mask).not.toBeNull();
    expect(container.querySelector(`g[mask="url(#${mask!.id})"] [data-accessory="suit"]`)).not.toBeNull();
  });

  it("frames only the head with the hoodie's hood and keeps 2D-only shading out of 3D", () => {
    const { container } = render(<AgentSymbol shape="circle" color="#7ab6ef" size={40} accessories={{ outfit: "hoodie" }} />);
    expect(container.querySelector('[data-accessory-rim="hoodie"] clipPath rect')).not.toBeNull();
    const hat = ACCESSORY_ITEMS.find(item => item.id === "top_hat")!;
    expect(hat.parts.some(part => part.only === "2d")).toBe(true);
  });

  it("gives the cigar living smoke and an ember, and the sunglasses gradient lenses with a glint", () => {
    const { container } = render(<AgentSymbol shape="circle" color="#7ab6ef" size={40} accessories={{ mouth: "cigar", face: "sunglasses" }} />);
    expect(container.querySelectorAll(".agent-acc-smoke circle")).toHaveLength(4);
    expect(container.querySelector(".agent-acc-ember")).not.toBeNull();
    expect(container.querySelector("linearGradient")).not.toBeNull();
    expect(container.querySelector(".agent-acc-twinkle")).not.toBeNull();
    const cigar = ACCESSORY_ITEMS.find(item => item.id === "cigar")!;
    expect(cigar.parts.filter(part => part.t === "smoke")).toHaveLength(1);
  });

  it("stores accessories with the companion and recovers older looks", () => {
    expect(companionSchema.safeParse({ shape: "drop", color: "#112233", accessories: { held: "coffee" } }).success).toBe(true);
    expect(companionSchema.safeParse({ shape: "drop", color: "#112233", accessories: { tail: "coffee" } }).success).toBe(false);
    expect(resolveCompanion("legacy", { shape: "drop", color: "#112233" }).accessories).toEqual({});
  });

  it("tints agent-colour tokens and leaves fixed colours alone", () => {
    expect(resolveFill("body", "#7ab6ef")).toBe("#7ab6ef");
    expect(resolveFill("#f2c14e", "#7ab6ef")).toBe("#f2c14e");
    expect(resolveFill("bodyDark", "#ffffff")).toBe("#b3b3b3");
  });

  it("rolls only real items, one per slot", () => {
    let seed = 1;
    const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    for (let i = 0; i < 50; i++) {
      for (const [slot, id] of Object.entries(randomAccessories(random))) {
        expect(ACCESSORY_IDS_BY_SLOT[slot as keyof typeof ACCESSORY_IDS_BY_SLOT]).toContain(id);
      }
    }
  });
});

describe("accessory depth on the volumetric bodies", () => {
  it.each(COMPANION_SHAPES)("rests every %s slot on the measured body surface", shape => {
    const depth = ACCESSORY_CATALOG.shapes[shape].depth!;
    for (const slot of ACCESSORY_SLOTS) expect(slotDepthM(shape, slot)).toBe(depth[slot]);
    // Worn items sit on the front of a solid at least a third of a metre deep.
    expect(depth.face!).toBeGreaterThan(0.2);
    expect(depth.back!).toBeLessThan(-0.2);
    expect(depth.head).toBe(0);
  });
});

