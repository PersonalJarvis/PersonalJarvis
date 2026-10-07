import { describe, expect, it } from "vitest";
import { idleInput, RETRO_STEP, seededRandom, type RetroInput } from "../retroGame";
import game, {
  AMMO, BATTERY_X, BATTERY_Y, BLAST_R, BLAST_S, CITY_X, GROUND_Y, WIDTH,
  addWarhead, blastRadius, citiesStanding, firingBattery, multiplier, type DefenseState,
} from "./cityDefense";

const rnd = seededRandom(17);
const run = (s: DefenseState, input: RetroInput, seconds: number) => {
  for (let t = 0; t < seconds; t += RETRO_STEP) game.step(s, input, RETRO_STEP, rnd);
};
/** A wave in progress with the sky held empty, so one rule can be watched at a time. */
const quiet = (): DefenseState => {
  const s = game.create(seededRandom(4));
  run(s, idleInput(), 2);
  for (const w of s.warheads) w.active = false;
  s.toLaunch = 5;
  s.salvoTimer = 999;
  s.flyerTimer = 999;
  return s;
};
const pressA = (): RetroInput => {
  const input = idleInput();
  input.a = true;
  input.pressed.a = true;
  return input;
};
const pressB = (): RetroInput => {
  const input = idleInput();
  input.b = true;
  input.pressed.b = true;
  return input;
};
const click = (x: number, y: number): RetroInput => ({ ...idleInput(), pointer: { x, y, down: true, clicked: true } });
const activeMissiles = (s: DefenseState) => s.missiles.filter((m) => m.active);
const activeWarheads = (s: DefenseState) => s.warheads.filter((w) => w.active).length;

function fakeContext(): { ctx: CanvasRenderingContext2D; depth: () => number; rects: () => number } {
  let depth = 0, rects = 0;
  const finite = (...v: number[]) => {
    if (!v.every(Number.isFinite)) throw new Error(`non-finite draw arguments ${v.join(",")}`);
  };
  const noop = () => {};
  const ctx = {
    fillStyle: "", strokeStyle: "", lineWidth: 1, globalAlpha: 1, lineCap: "butt",
    fillRect: (x: number, y: number, w: number, h: number) => { finite(x, y, w, h); rects += 1; },
    strokeRect: (x: number, y: number, w: number, h: number) => finite(x, y, w, h),
    beginPath: noop, closePath: noop, fill: noop, stroke: noop,
    moveTo: (x: number, y: number) => finite(x, y),
    lineTo: (x: number, y: number) => finite(x, y),
    arc: (x: number, y: number, r: number) => { finite(x, y, r); if (r < 0) throw new Error("negative radius"); },
    translate: (x: number, y: number) => finite(x, y),
    save: () => { depth += 1; },
    restore: () => { depth -= 1; },
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, depth: () => depth, rects: () => rects };
}

describe("city defense", () => {
  it("starts with six cities, three full batteries and a short pause", () => {
    const s = game.create(seededRandom(1));
    expect(game.pointer).toBe(true);
    expect(game.status(s)).toEqual({ score: 0, lives: 6, level: 1, over: false });
    expect(s.batteries.map((b) => b.ammo)).toEqual([AMMO, AMMO, AMMO]);
    expect(s.phase).toBe("ready");
    run(s, idleInput(), 2);
    expect(s.phase).toBe("play");
  });

  it("a click aims there and fires from the nearest battery", () => {
    const s = quiet();
    game.step(s, click(290, 90), RETRO_STEP, rnd);
    expect(s.batteries.map((b) => b.ammo)).toEqual([AMMO, AMMO, AMMO - 1]);
    const [m] = activeMissiles(s);
    expect([m.ox, m.tx, m.ty]).toEqual([BATTERY_X[2], 290, 90]);
  });

  it("a resting mouse does not undo keyboard aiming", () => {
    const s = quiet();
    game.step(s, { ...idleInput(), pointer: { x: 100, y: 100, down: false, clicked: false } }, RETRO_STEP, rnd);
    run(s, { ...idleInput(), right: true, pointer: { x: 100, y: 100, down: false, clicked: false } }, 0.5);
    expect(s.cx).toBeGreaterThan(160);
  });

  it("the keyboard moves the crosshair and fires with the action button", () => {
    const s = quiet();
    run(s, { ...idleInput(), left: true, up: true }, 2);
    expect(s.cx).toBeLessThan(10);
    game.step(s, pressA(), RETRO_STEP, rnd);
    expect(s.batteries[0].ammo).toBe(AMMO - 1);
    expect(activeMissiles(s)).toHaveLength(1);
  });

  it("an empty or wrecked battery hands over to the next nearest one", () => {
    const s = quiet();
    s.batteries[0].ammo = 0;
    s.cx = 20;
    expect(firingBattery(s)).toBe(1);
    s.batteries[1].alive = false;
    expect(firingBattery(s)).toBe(2);
    s.batteries[2].ammo = 0;
    expect(firingBattery(s)).toBe(-1);
    game.step(s, pressA(), RETRO_STEP, rnd);
    expect(activeMissiles(s)).toHaveLength(0);
  });

  it("the secondary button forces a battery, cycling back to nearest", () => {
    const s = quiet();
    s.cx = 300;
    game.step(s, pressB(), RETRO_STEP, rnd);
    expect(s.selected).toBe(0);
    game.step(s, pressA(), RETRO_STEP, rnd);
    expect(s.batteries[0].ammo).toBe(AMMO - 1);
    game.step(s, pressB(), RETRO_STEP, rnd);
    game.step(s, pressB(), RETRO_STEP, rnd);
    game.step(s, pressB(), RETRO_STEP, rnd);
    expect(s.selected).toBe(-1);
    expect(firingBattery(s)).toBe(2);
  });

  it("a counter-missile bursts at the aim point and its blast destroys a warhead", () => {
    const s = quiet();
    s.cx = 160;
    s.cy = 120;
    addWarhead(s, 160, 118, 160, GROUND_Y, 1, false);
    game.step(s, pressA(), RETRO_STEP, rnd);
    run(s, idleInput(), 0.6);
    expect(activeWarheads(s)).toBe(0);
    expect(s.score).toBe(25 * multiplier(1));
  });

  it("blasts grow, hold and fade", () => {
    expect(blastRadius(0, BLAST_R)).toBe(0);
    expect(blastRadius(0.5, BLAST_R)).toBe(BLAST_R);
    expect(blastRadius(BLAST_S, BLAST_R)).toBeCloseTo(0, 6);
  });

  it("a destroyed warhead's own blast catches the next one (chain reaction)", () => {
    const s = quiet();
    s.cx = 100;
    s.cy = 100;
    // The second warhead is out of reach of our blast but within the first one's.
    addWarhead(s, 112, 100, 112, GROUND_Y, 0.01, false);
    addWarhead(s, 124, 100, 124, GROUND_Y, 0.01, false);
    expect(124 - 100).toBeGreaterThan(BLAST_R);
    game.step(s, click(100, 100), RETRO_STEP, rnd);
    run(s, idleInput(), 1.5);
    expect(activeWarheads(s)).toBe(0);
    expect(s.score).toBe(50);
  });

  it("a warhead reaching a city wrecks it", () => {
    const s = quiet();
    addWarhead(s, CITY_X[0], GROUND_Y - 4, CITY_X[0], GROUND_Y, 60, false);
    run(s, idleInput(), 0.3);
    expect(s.cities[0]).toBe(0);
    expect(game.status(s).lives).toBe(5);
  });

  it("a warhead on a battery wrecks it and its ammo", () => {
    const s = quiet();
    addWarhead(s, BATTERY_X[1], BATTERY_Y, BATTERY_X[1], BATTERY_Y + 4, 60, false);
    run(s, idleInput(), 0.3);
    expect(s.batteries[1]).toMatchObject({ alive: false, ammo: 0 });
  });

  it("later waves split warheads mid-air", () => {
    const s = quiet();
    s.wave = 5;
    addWarhead(s, 160, 40, 160, GROUND_Y, 60, true, 50);
    run(s, idleInput(), 0.3);
    expect(activeWarheads(s)).toBeGreaterThanOrEqual(3);
  });

  it("a bomber caught in a blast is worth extra", () => {
    const s = quiet();
    s.flyer.active = true;
    s.flyer.x = 150;
    s.flyer.y = 60;
    s.flyer.speed = 0;
    s.flyer.dropTimer = 99;
    game.step(s, click(150, 60), RETRO_STEP, rnd);
    run(s, idleInput(), 1);
    expect(s.flyer.active).toBe(false);
    expect(s.score).toBe(100);
  });

  it("the wave ends with a bonus for ammo and cities, then the next wave refills", () => {
    const s = quiet();
    s.toLaunch = 0;
    s.batteries[0].ammo = 4;
    s.cities[5] = 0;
    run(s, idleInput(), 0.1);
    expect(s.phase).toBe("bonus");
    expect(s.score).toBe((4 + AMMO * 2) * 5 + 5 * 100);
    run(s, idleInput(), 3);
    expect(s.wave).toBe(2);
    expect(s.batteries.map((b) => b.ammo)).toEqual([AMMO, AMMO, AMMO]);
    expect(citiesStanding(s)).toBe(5);
  });

  it("a banked bonus city rebuilds a lost one", () => {
    const s = quiet();
    s.score = 9990;
    s.toLaunch = 0;
    s.cities[2] = 0;
    run(s, idleInput(), 0.1);
    expect(s.banked).toBe(1);
    run(s, idleInput(), 3);
    expect(citiesStanding(s)).toBe(6);
    expect(s.banked).toBe(0);
  });

  it("losing every city ends the run", () => {
    const s = quiet();
    s.cities.fill(0);
    s.cities[3] = 1;
    addWarhead(s, CITY_X[3], GROUND_Y - 2, CITY_X[3], GROUND_Y, 60, false);
    run(s, idleInput(), 3);
    expect(game.status(s)).toMatchObject({ lives: 0, over: true });
  });

  it("a whole wave plays out without help and keeps every pool bounded", () => {
    const s = game.create(seededRandom(8));
    s.wave = 6;
    for (let i = 0; i < 60 * 90 && s.phase !== "over" && s.wave === 6; i += 1) {
      game.step(s, idleInput(), RETRO_STEP, rnd);
      expect(s.warheads.length).toBe(40);
    }
    expect(s.wave === 7 || s.phase === "over" || s.phase === "lost").toBe(true);
  });

  it("draws every phase without throwing, idle and reduced included", () => {
    const s = game.create(seededRandom(3));
    for (const info of [
      { time: 0, reduced: false, idle: true },
      { time: 5.5, reduced: true, idle: true },
      { time: 1, reduced: false, idle: false },
    ]) {
      const fake = fakeContext();
      game.draw(fake.ctx, s, info);
      expect(fake.depth()).toBe(0);
      expect(fake.rects()).toBeGreaterThan(50);
    }
    run(s, idleInput(), 2);
    addWarhead(s, 100, 10, 120, GROUND_Y, 30, true, 80);
    game.step(s, click(200, 80), RETRO_STEP, rnd);
    s.flyer.active = true;
    s.flyer.x = 80;
    s.flyer.y = 60;
    run(s, idleInput(), 0.3);
    s.shake = 0.3;
    s.selected = 1;
    s.banked = 2;
    for (const kind of [0, 1] as const) {
      s.flyer.kind = kind;
      game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
      game.draw(fakeContext().ctx, s, { time: 2, reduced: true, idle: false });
    }
    s.cities[1] = 0;
    s.batteries[2].alive = false;
    s.phase = "bonus";
    game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
    s.phase = "over";
    game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
    expect(WIDTH).toBe(game.width);
  });
});
