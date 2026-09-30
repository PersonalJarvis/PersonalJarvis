import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import en from "@/i18n/locales/onboarding/en.json";
import { cutout, placeCard, TOUR_STEPS } from "./tourSteps";

const SRC = join(__dirname, "..", "..", "..");

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

describe("tour steps", () => {
  it("have unique ids and a text in the locale", () => {
    const ids = TOUR_STEPS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    const texts = (en as { app_tour: { steps: Record<string, string> } }).app_tour.steps;
    for (const id of ids) expect(texts[id], id).toBeTruthy();
  });

  it("point only at anchors the app actually sets", () => {
    const code = sources(SRC)
      .filter((f) => !f.includes(`${join("onboarding", "tour")}`))
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    for (const step of TOUR_STEPS) {
      const literal = code.includes(`data-tour="${step.anchor}"`);
      // Sidebar rows get theirs from a template: data-tour={`nav-${item.id}`}.
      const templated = step.anchor.startsWith("nav-") && code.includes("data-tour={`nav-${item.id}`}");
      expect(literal || templated, step.anchor).toBe(true);
    }
  });

  it("never press a control — every effect is navigation", () => {
    for (const step of TOUR_STEPS) {
      for (const effect of [step.onEnter, step.onExit]) {
        if (effect) expect(["home-voice", "open-agents", "back-home"]).toContain(effect);
      }
    }
  });

  it("ends where it started, on the voice bar", () => {
    expect(TOUR_STEPS[0].anchor).toBe("voice-bar");
    expect(TOUR_STEPS[TOUR_STEPS.length - 1].anchor).toBe("voice-bar");
  });
});

describe("cutout", () => {
  it("keeps the same number of points for any hole, so it can animate", () => {
    const a = cutout({ x: 0, y: 0, w: 1200, h: 800 });
    const b = cutout({ x: 40, y: 60, w: 200, h: 36 });
    expect(a.split(",").length).toBe(b.split(",").length);
    expect(b).toContain("40px 60px");
    expect(b).toContain("240px 96px");
    expect(b.startsWith("polygon(evenodd,")).toBe(true);
  });
});

describe("placeCard", () => {
  const view = { w: 1280, h: 800 };
  const card = { w: 320, h: 160 };

  it("sits to the right of a sidebar row", () => {
    const pos = placeCard({ x: 8, y: 200, w: 220, h: 36 }, "right", card, view);
    expect(pos.x).toBe(8 + 220 + 14);
    expect(pos.y).toBe(200 + 18 - 80);
  });

  it("stays inside the window near an edge", () => {
    const pos = placeCard({ x: 8, y: 770, w: 220, h: 20 }, "right", card, view);
    expect(pos.y + card.h).toBeLessThanOrEqual(view.h - 12);
  });

  it("flips above when there is no room below", () => {
    const pos = placeCard({ x: 400, y: 700, w: 400, h: 60 }, "below", card, view);
    expect(pos.y).toBe(700 - 14 - 160);
  });

  it("centres the card without an element", () => {
    expect(placeCard(null, "below", card, view)).toEqual({ x: 480, y: 320 });
  });
});
