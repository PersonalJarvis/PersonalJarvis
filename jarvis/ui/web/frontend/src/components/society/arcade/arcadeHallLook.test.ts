import { describe, expect, it } from "vitest";
import { Color } from "three";
import { ARCADE_GAMES } from "./arcadeGames";
import { drawCarpet, drawGlowSpot, drawPrizeCarpet, drawSnackFloor, drawWallPanels, PartKit } from "./arcadeHallLook";
import { createLiveScreen, drawScreenFrame, FACE_DRAWINGS, NEON_DRAW, PREVIEW_FPS, type NeonSymbol } from "./arcadeScreens";
import type { RetroGame } from "./retroGame";

/**
 * A stand-in 2D context (jsdom has no canvas): every method is recorded, the
 * gradient and text-metric factories return plain objects.
 */
function fakeContext(): { ctx: CanvasRenderingContext2D; calls: string[]; texts: string[] } {
  const calls: string[] = [];
  const texts: string[] = [];
  const state: Record<string, unknown> = {};
  const gradient = { addColorStop: () => undefined };
  const ctx = new Proxy(state, {
    get(target, key) {
      if (typeof key !== "string") return undefined;
      if (key in target) return target[key];
      if (key === "createLinearGradient" || key === "createRadialGradient") return () => gradient;
      if (key === "measureText") return (text: string) => ({ width: text.length * 10 });
      return (...args: unknown[]) => {
        if (key === "fillText" || key === "strokeText") texts.push(String(args[0]));
        calls.push(`${key}(${args.map((a) => (typeof a === "number" ? a.toFixed(2) : String(a))).join(",")})`);
      };
    },
    set(target, key, value) {
      if (typeof key === "string") {
        target[key] = value;
        calls.push(`${key}=${String(value)}`);
      }
      return true;
    },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls, texts };
}

/** Only digits and spaces may be drawn as text, besides a game's own title: everything else is i18n. */
const NUMERIC = /^[\d\s]*$/;

describe("PartKit", () => {
  it("merges placed, painted parts into one vertex-coloured geometry", () => {
    const geometry = new PartKit()
      .box([1, 2, 3], [1, 0, 0], "#ff0000")
      .sphere(0.5, [0, 1, 0], "#00ff00")
      .cylinder(0.2, 0.4, [0, 0, -2], "#0000ff", { rotation: [Math.PI / 2, 0, 0] })
      .build();
    const position = geometry.getAttribute("position");
    const colour = geometry.getAttribute("color");
    expect(colour.count).toBe(position.count);
    const red = new Color("#ff0000");
    expect([colour.getX(0), colour.getY(0), colour.getZ(0)]).toEqual([red.r, red.g, red.b]);
    const box = geometry.boundingBox!;
    // The sphere is a polygon: its extent is within a few centimetres of its radius.
    expect(box.min.x).toBeCloseTo(-0.5, 1);
    expect(box.max.x).toBeCloseTo(1.5);
    expect(box.max.y).toBeCloseTo(1.5);
    expect(box.min.z).toBeCloseTo(-2.2);
    geometry.dispose();
  });

  it("builds an empty geometry from no parts", () => {
    const kit = new PartKit();
    expect(kit.size).toBe(0);
    const geometry = kit.build();
    expect(geometry.getAttribute("position")).toBeUndefined();
  });
});

describe("hall surfaces", () => {
  it.each([
    ["carpet", drawCarpet], ["prize carpet", drawPrizeCarpet], ["snack floor", drawSnackFloor],
    ["wall panels", drawWallPanels], ["glow spot", drawGlowSpot],
  ] as const)("draws the %s the same way every time", (_name, draw) => {
    const a = fakeContext(), b = fakeContext();
    draw(a.ctx, 256, 256);
    draw(b.ctx, 256, 256);
    expect(a.calls.length).toBeGreaterThan(0);
    expect(a.calls).toEqual(b.calls);
    expect(a.texts).toEqual([]);
  });
});

describe("attract screens", () => {
  it.each(ARCADE_GAMES.map((g) => [g.id, g] as const))("%s has two different frames with its title and no words", (_id, game) => {
    const frames = ([0, 1] as const).map((frame) => {
      const fake = fakeContext();
      drawScreenFrame(fake.ctx, 0, game, frame);
      return fake;
    });
    expect(frames[0].calls).not.toEqual(frames[1].calls);
    for (const { texts } of frames) {
      expect(texts).toContain(game.title);
      for (const text of texts) if (text !== game.title) expect(text).toMatch(NUMERIC);
    }
  });

  it("draws a blank screen for a cabinet without a game", () => {
    const fake = fakeContext();
    drawScreenFrame(fake.ctx, 0, null, 0);
    expect(fake.texts.every((t) => NUMERIC.test(t))).toBe(true);
  });
});

describe("machine faces and neon signs", () => {
  it.each(Object.entries(FACE_DRAWINGS))("%s writes nothing but digits", (_name, draw) => {
    const fake = fakeContext();
    draw(fake.ctx, 256, 128);
    expect(fake.calls.length).toBeGreaterThan(0);
    for (const text of fake.texts) expect(text).toMatch(NUMERIC);
  });

  it.each(Object.keys(NEON_DRAW) as NeonSymbol[])("the %s sign is tubes only", (symbol) => {
    const fake = fakeContext();
    NEON_DRAW[symbol](fake.ctx, 256);
    expect(fake.calls.some((c) => c.startsWith("stroke("))).toBe(true);
    expect(fake.texts).toEqual([]);
  });
});

describe("live preview", () => {
  interface Counter { draws: number }
  const fakeCanvas = () => {
    const { ctx } = fakeContext();
    return { width: 0, height: 0, getContext: () => ctx } as unknown as HTMLCanvasElement;
  };
  const game = (draw: (state: Counter, idle: boolean, time: number) => void): RetroGame<Counter> => ({
    id: "fake", width: 160, height: 120,
    create: () => ({ draws: 0 }),
    step: () => undefined,
    draw: (_ctx, state, info) => draw(state, info.idle, info.time),
    status: () => ({ score: 0, over: false }),
  });

  it("is unavailable without a 2D canvas", () => {
    expect(createLiveScreen(game(() => undefined) as RetroGame<unknown>, { canvas: () => null })).toBeNull();
  });

  it("repaints the game's idle title screen a dozen times a second", () => {
    const seen: { idle: boolean; time: number }[] = [];
    let state: Counter | null = null;
    const screen = createLiveScreen(game((s, idle, time) => { state = s; s.draws += 1; seen.push({ idle, time }); }) as RetroGame<unknown>,
      { canvas: fakeCanvas });
    expect(screen).not.toBeNull();
    screen!.tick(0.01);
    screen!.tick(0.5 / PREVIEW_FPS);
    expect(state!.draws).toBe(1);
    screen!.tick(0.6 / PREVIEW_FPS);
    expect(state!.draws).toBe(2);
    expect(seen.every((s) => s.idle)).toBe(true);
    expect(seen[1].time).toBeGreaterThan(seen[0].time);
    screen!.dispose();
  });

  it("freezes on a game that throws while drawing", () => {
    let calls = 0;
    const screen = createLiveScreen(game(() => { calls += 1; throw new Error("broken"); }) as RetroGame<unknown>, { canvas: fakeCanvas });
    screen!.tick(1);
    screen!.tick(1);
    expect(calls).toBe(1);
  });

  it("gives up on a game that cannot start", () => {
    const broken: RetroGame<unknown> = { ...game(() => undefined), create: () => { throw new Error("no start"); } } as RetroGame<unknown>;
    expect(createLiveScreen(broken, { canvas: fakeCanvas })).toBeNull();
  });
});
