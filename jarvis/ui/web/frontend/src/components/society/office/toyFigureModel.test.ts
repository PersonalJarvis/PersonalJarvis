import { describe, expect, it } from "vitest";

import type { FigureRecipe } from "../figures/figureRecipe";
import { OUTFITS } from "./outfitCatalog";
import {
  blendPose, createPose, isNaturalSkin, KEYBOARD, lowestSoleY, poseFor, SEAT_BACK_CLEARANCE, SEAT_HEIGHT,
  TOY_HEIGHT, toyLookFor, toyPoints, type ToyMode,
} from "./toyFigureModel";

const SAMPLES = Array.from({ length: 40 }, (_, i) => i * 0.137);
const SEATED: ToyMode[] = ["sit", "work", "sleep"];
const GROUNDED: ToyMode[] = ["idle", "talk", "wave"];

describe("poseFor — seated", () => {
  it.each(SEATED)("%s rests the pelvis exactly on the seat", (mode) => {
    for (const seat of Object.values(SEAT_HEIGHT)) {
      for (const t of SAMPLES) {
        const p = poseFor(mode, t, 0, { seatHeight: seat });
        expect(p.hipY).toBeCloseTo(seat, 6);
      }
    }
  });

  it.each(SEATED)("%s keeps every body point clear of the backrest", (mode) => {
    for (const t of SAMPLES) {
      const pose = poseFor(mode, t, 0, { seatHeight: SEAT_HEIGHT.chair });
      for (const { name, p } of toyPoints(pose)) {
        expect(p[2], `${mode} ${name} at t=${t}`).toBeGreaterThanOrEqual(-SEAT_BACK_CLEARANCE);
      }
    }
  });

  it("sits with thighs forward and shins hanging down", () => {
    const pts = new Map(toyPoints(poseFor("sit", 0, 0, { seatHeight: 0.52 })).map((x) => [x.name, x.p]));
    const hip = pts.get("leftHip")!;
    const knee = pts.get("leftKnee")!;
    const ankle = pts.get("leftAnkle")!;
    expect(knee[2]).toBeGreaterThan(hip[2] + 0.15);
    expect(Math.abs(knee[1] - hip[1])).toBeLessThan(0.02);
    expect(ankle[1]).toBeLessThan(knee[1] - 0.12);
    expect(Math.abs(ankle[2] - knee[2])).toBeLessThan(0.03);
  });

  it("work puts both hands on the keyboard in front of the seat", () => {
    for (const t of SAMPLES) {
      const pts = new Map(toyPoints(poseFor("work", t, 0, { seatHeight: SEAT_HEIGHT.chair })).map((x) => [x.name, x.p]));
      for (const side of ["left", "right"]) {
        const hand = pts.get(`${side}Hand`)!;
        expect(Math.abs(hand[2] - KEYBOARD.z)).toBeLessThan(0.02);
        expect(hand[1]).toBeGreaterThan(KEYBOARD.y);
        expect(hand[1]).toBeLessThan(KEYBOARD.y + 0.1);
      }
    }
  });
});

describe("poseFor — standing and walking", () => {
  it.each(GROUNDED)("%s keeps the feet on the floor", (mode) => {
    for (const t of SAMPLES) expect(lowestSoleY(poseFor(mode, t, 0, { seatHeight: 0.52 }))).toBeCloseTo(0, 5);
  });

  it("walk keeps a foot on the floor at every speed", () => {
    for (const speed of [0, 0.5, 1.4, 2.5, 4, 6]) {
      for (const t of SAMPLES) expect(lowestSoleY(poseFor("walk", t, speed, { seatHeight: 0.52 }))).toBeCloseTo(0, 5);
    }
  });

  it("walk swings harder and leans forward into a run", () => {
    const peak = (speed: number) => Math.max(...SAMPLES.map((t) => Math.abs(poseFor("walk", t, speed, { seatHeight: 0.52 }).leftHip)));
    expect(peak(4)).toBeGreaterThan(peak(1.2));
    expect(poseFor("walk", 0.3, 4, { seatHeight: 0.52 }).torsoPitch).toBeGreaterThan(poseFor("walk", 0.3, 1.2, { seatHeight: 0.52 }).torsoPitch);
  });

  it("idle stands about as tall as the figure", () => {
    const top = Math.max(...toyPoints(poseFor("idle", 0, 0, { seatHeight: 0.52 })).map((x) => x.p[1]));
    expect(top).toBeGreaterThan(TOY_HEIGHT - 0.08);
    expect(top).toBeLessThan(TOY_HEIGHT + 0.03);
  });

  it("celebrate hops off the floor but never sinks through it", () => {
    const soles = SAMPLES.map((t) => lowestSoleY(poseFor("celebrate", t, 0, { seatHeight: 0.52 })));
    expect(Math.min(...soles)).toBeGreaterThan(-1e-6);
    expect(Math.max(...soles)).toBeGreaterThan(0.05);
  });

  it("wave raises the right arm above the head line", () => {
    const pts = new Map(toyPoints(poseFor("wave", 0.4, 0, { seatHeight: 0.52 })).map((x) => [x.name, x.p]));
    expect(pts.get("rightHand")![1]).toBeGreaterThan(pts.get("rightShoulder")![1] + 0.1);
    expect(pts.get("rightHand")![0]).toBeLessThan(0);
  });
});

describe("blendPose", () => {
  it("interpolates and writes in place", () => {
    const a = poseFor("idle", 0, 0, { seatHeight: 0.52 });
    const b = poseFor("sit", 0, 0, { seatHeight: 0.52 });
    const mid = blendPose(a, b, 0.5);
    expect(mid.hipY).toBeCloseTo((a.hipY + b.hipY) / 2, 6);
    const out = createPose();
    expect(blendPose(a, b, 1, out)).toBe(out);
    expect(out.leftHip).toBeCloseTo(b.leftHip, 6);
  });
});

describe("toyLookFor", () => {
  const recipe: FigureRecipe = {
    contract: 1, archetype: "biped", base: "chibi", parts: {},
    palette: { skin: "#d8a37c", hair: "#123456", primary: "#6fbf5a", secondary: "#2f4a7a", accent: "#f2c14e", shoes: "#ffffff" },
  };

  it("is deterministic", () => {
    expect(toyLookFor(recipe, "agent-7")).toEqual(toyLookFor(recipe, "agent-7"));
    expect(toyLookFor(null, "agent-7")).toEqual(toyLookFor(null, "agent-7"));
  });

  it("wears a chosen outfit in the recipe colours", () => {
    const look = toyLookFor({ ...recipe, outfit: "suit" }, "agent-7");
    expect(look).toMatchObject({ outfit: "suit", skin: "#d8a37c", hair: "#123456", shirt: "#6fbf5a", pants: "#2f4a7a", shirtAccent: "#f2c14e", shoes: "#ffffff" });
  });

  it("dresses an undressed recipe in an office outfit and colourway, keeping skin and hair", () => {
    const look = toyLookFor(recipe, "agent-7");
    expect(look).toMatchObject({ skin: "#d8a37c", hair: "#123456" });
    const way = OUTFITS.find((o) => o.id === look.outfit)!.colourways.find((w) => w.primary === look.shirt);
    expect(way).toMatchObject({ secondary: look.pants, accent: look.shirtAccent, inner: look.inner, shoes: look.shoes });
    // The same recipe looks the same everywhere, whoever asks.
    expect(toyLookFor(recipe, "")).toMatchObject({ outfit: look.outfit, shirt: look.shirt });
  });

  it("gives a crowd of agents varied office outfits", () => {
    const outfits = new Set(Array.from({ length: 60 }, (_, i) =>
      toyLookFor({ ...recipe, palette: { ...recipe.palette, primary: `#${(i * 40503).toString(16).padStart(6, "0").slice(-6)}` } }, "").outfit));
    expect(outfits.size).toBeGreaterThanOrEqual(5);
  });

  it("keeps skin natural and varies hair styles across identities", () => {
    const green = toyLookFor({ ...recipe, palette: { ...recipe.palette, skin: "#3fa34d" } }, "orc");
    expect(isNaturalSkin(green.skin)).toBe(true);
    const styles = new Set(Array.from({ length: 60 }, (_, i) => toyLookFor(null, `agent-${i}`).hairStyle));
    expect(styles.size).toBeGreaterThanOrEqual(5);
    for (let i = 0; i < 30; i += 1) expect(isNaturalSkin(toyLookFor(null, `a${i}`).skin)).toBe(true);
  });
});
