import { describe, expect, it } from "vitest";
import { idleInput, RETRO_STEP, seededRandom, type RetroButtons, type RetroInput } from "../retroGame";
import game, {
  BAY_X, CELL, HOME_ROW, LANES, LIFE_TIME, START_ROW, START_X,
  diveState, itemX, levelFactor, platformAt, vehicleAt, type HopperState,
} from "./roadHopper";

const rnd = seededRandom(11);
const run = (s: HopperState, input: RetroInput, seconds: number) => {
  for (let t = 0; t < seconds; t += RETRO_STEP) game.step(s, input, RETRO_STEP, rnd);
};
/** One frame with `dir` going down (an edge), as the overlay reports a fresh key press. */
const press = (dir: keyof RetroButtons): RetroInput => {
  const input = idleInput();
  input[dir] = true;
  input.pressed[dir] = true;
  return input;
};
const hold = (dir: keyof RetroButtons): RetroInput => ({ ...idleInput(), [dir]: true });
const laneOf = (row: number) => LANES.findIndex((l) => l.row === row);
/** Scroll lane `lane` so its first item's left edge sits at `x`, and return that item's length. */
const parkItem = (s: HopperState, lane: number, x: number): number => {
  let shift = 0;
  while (Math.round(itemX(lane, 0, shift)) !== x) shift += 1;
  s.shifts[lane] = shift;
  return LANES[lane].lens[0];
};
const fresh = (): HopperState => {
  const s = game.create(seededRandom(5));
  s.flyTimer = 999;
  return s;
};

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

describe("road hopper", () => {
  it("starts on the bottom bank with three lives and a full clock", () => {
    const s = fresh();
    expect(game.status(s)).toEqual({ score: 0, lives: 3, level: 1, over: false });
    expect([s.x, s.row, s.timeLeft]).toEqual([START_X, START_ROW, LIFE_TIME]);
    expect(Array.from(s.bays)).toEqual([0, 0, 0, 0, 0]);
  });

  it("hops one cell per press; holding the key does not keep hopping", () => {
    const s = fresh();
    game.step(s, press("left"), RETRO_STEP, rnd);
    run(s, hold("left"), 1);
    expect(s.x).toBe(START_X - CELL);
    run(s, hold("right"), 1);
    expect(s.x).toBe(START_X - CELL);
    expect(s.row).toBe(START_ROW);
  });

  it("animates a hop and takes a press made mid-hop right after landing", () => {
    const s = fresh();
    game.step(s, press("right"), RETRO_STEP, rnd);
    expect(s.hop.active).toBe(true);
    game.step(s, idleInput(), RETRO_STEP, rnd);
    game.step(s, press("right"), RETRO_STEP, rnd);
    run(s, idleInput(), 0.5);
    expect(s.x).toBe(START_X + 2 * CELL);
  });

  it("a vehicle squashes the frog", () => {
    const s = fresh();
    const row = 10, lane = laneOf(row);
    parkItem(s, lane, 100);
    s.row = row;
    s.x = 108;
    expect(vehicleAt(s, row, s.x)).toBe(true);
    game.step(s, idleInput(), RETRO_STEP, rnd);
    expect(s.phase).toBe("dying");
    expect(s.deathKind).toBe("splat");
    expect(s.lives).toBe(2);
    run(s, idleInput(), 1.5);
    expect([s.phase, s.row, s.x]).toEqual(["play", START_ROW, START_X]);
  });

  it("a log carries the frog along", () => {
    const s = fresh();
    const row = 4, lane = laneOf(row);
    const len = parkItem(s, lane, 60);
    s.row = row;
    s.x = 60 + len / 2;
    run(s, idleInput(), 0.5);
    expect(s.phase).toBe("play");
    // Half a second of drift, give or take the last frame.
    expect(Math.abs(s.x - (60 + len / 2) - LANES[lane].speed * 0.5)).toBeLessThan(2);
  });

  it("open water drowns the frog", () => {
    const s = fresh();
    const row = 2;
    let x = 20;
    while (platformAt(s, row, x)) x += 4;
    s.row = row;
    s.x = x;
    game.step(s, idleInput(), RETRO_STEP, rnd);
    expect(s.phase).toBe("dying");
    expect(s.deathKind).toBe("splash");
  });

  it("a diving turtle drops the frog in the water", () => {
    const s = fresh();
    const row = 3, lane = laneOf(row);
    // Group 1 of this lane dives; find a moment it is under.
    let clock = 0;
    while (diveState(lane, 1, clock) !== 2) clock += 0.1;
    s.diveClock = clock;
    s.row = row;
    const x0 = itemX(lane, 1, s.shifts[lane]);
    s.x = x0 + 16;
    game.step(s, idleInput(), RETRO_STEP, rnd);
    expect(s.phase).toBe("dying");
  });

  it("being carried off the edge costs a life", () => {
    const s = fresh();
    const row = 2, lane = laneOf(row);
    parkItem(s, lane, 180);
    s.row = row;
    s.x = 214;
    run(s, idleInput(), 1);
    expect(s.phase).toBe("dying");
    expect(s.lives).toBe(2);
  });

  it("filling a bay scores the time left and sends a new frog", () => {
    const s = fresh();
    s.row = 2;
    s.x = BAY_X[0];
    game.step(s, press("up"), RETRO_STEP, rnd);
    run(s, idleInput(), 0.2);
    expect(s.bays[0]).toBe(1);
    expect(s.score).toBeGreaterThanOrEqual(50 + 10 * 29);
    expect([s.row, s.x, s.phase]).toEqual([START_ROW, START_X, "play"]);
  });

  it("the hedge between bays and a filled bay both kill", () => {
    const s = fresh();
    s.row = 2;
    s.x = (BAY_X[0] + BAY_X[1]) / 2;
    game.step(s, press("up"), RETRO_STEP, rnd);
    run(s, idleInput(), 0.2);
    expect(s.phase).toBe("dying");
    expect(s.row).toBe(HOME_ROW);

    const t = fresh();
    t.bays[2] = 1;
    t.row = 2;
    t.x = BAY_X[2];
    game.step(t, press("up"), RETRO_STEP, rnd);
    run(t, idleInput(), 0.2);
    expect(t.phase).toBe("dying");
  });

  it("a fly in a bay is worth a bonus", () => {
    const s = fresh();
    s.flyBay = 1;
    s.flyLeft = 5;
    s.row = 2;
    s.x = BAY_X[1];
    game.step(s, press("up"), RETRO_STEP, rnd);
    run(s, idleInput(), 0.2);
    expect(s.score).toBeGreaterThanOrEqual(50 + 200 + 10 * 29);
    expect(s.flyBay).toBe(-1);
  });

  it("filling all five bays starts a faster level", () => {
    const s = fresh();
    s.bays.set([1, 1, 1, 1, 0]);
    s.row = 2;
    s.x = BAY_X[4];
    game.step(s, press("up"), RETRO_STEP, rnd);
    run(s, idleInput(), 0.2);
    expect(s.phase).toBe("levelup");
    expect(s.score).toBeGreaterThanOrEqual(1000);
    run(s, idleInput(), 2.5);
    expect(game.status(s).level).toBe(2);
    expect(Array.from(s.bays)).toEqual([0, 0, 0, 0, 0]);
    expect(levelFactor(2)).toBeGreaterThan(levelFactor(1));
  });

  it("the clock running out costs a life", () => {
    const s = fresh();
    run(s, idleInput(), LIFE_TIME - 1);
    expect(s.phase).toBe("play");
    run(s, idleInput(), 1.5);
    expect(s.phase).toBe("dying");
    expect(s.deathKind).toBe("time");
    expect(s.lives).toBe(2);
  });

  it("the last life ends the run", () => {
    const s = fresh();
    s.lives = 1;
    run(s, idleInput(), LIFE_TIME + 2);
    expect(game.status(s)).toMatchObject({ lives: 0, over: true });
  });

  it("forward hops score once per new row", () => {
    const s = fresh();
    s.row = 7; // the middle bank
    s.bestRow = 7;
    game.step(s, press("down"), RETRO_STEP, rnd);
    run(s, idleInput(), 0.15);
    expect(s.score).toBe(0);

    const t = fresh();
    game.step(t, press("up"), RETRO_STEP, rnd);
    run(t, idleInput(), 0.15);
    expect(t.score).toBe(10);
  });

  it("draws every phase without throwing, idle and reduced included", () => {
    const s = game.create(seededRandom(9));
    for (const info of [
      { time: 0, reduced: false, idle: true },
      { time: 4.2, reduced: true, idle: true },
      { time: 1, reduced: false, idle: false },
    ]) {
      const fake = fakeContext();
      game.draw(fake.ctx, s, info);
      expect(fake.depth()).toBe(0);
      expect(fake.rects()).toBeGreaterThan(100);
    }
    s.bays.set([1, 0, 1, 0, 0]);
    s.flyBay = 3;
    game.step(s, press("left"), RETRO_STEP, rnd);
    game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
    s.timeLeft = 3;
    game.draw(fakeContext().ctx, s, { time: 2, reduced: true, idle: false });
    for (const kind of ["splat", "splash", "time"] as const) {
      s.phase = "dying";
      s.deathKind = kind;
      s.timer = 0.5;
      game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
    }
    s.phase = "levelup";
    game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
    s.phase = "over";
    game.draw(fakeContext().ctx, s, { time: 2, reduced: true, idle: false });
  });
});
