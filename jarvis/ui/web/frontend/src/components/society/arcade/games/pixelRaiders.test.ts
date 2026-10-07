import { describe, expect, it } from "vitest";
import { idleInput, RETRO_STEP, seededRandom, type RetroInput } from "../retroGame";
import game, {
  BUNKER_X, BUNKER_Y, CELL_W, COLS, DROP_Y, KIND_POINTS, MARCH_X, PLAYER_Y, ROWS, WIDTH,
  alienX, alienY, bunkerCells, march, stepDelay, waveStartY, type RaidersState,
} from "./pixelRaiders";

const rnd = seededRandom(7);
const run = (s: RaidersState, input: RetroInput, seconds: number) => {
  for (let t = 0; t < seconds; t += RETRO_STEP) game.step(s, input, RETRO_STEP, rnd);
};
/** A run with the formation and its guns held still, so one rule can be watched at a time. */
const calm = (): RaidersState => {
  const s = game.create(seededRandom(1));
  s.stepTimer = 999;
  s.fireTimer = 999;
  s.saucerTimer = 999;
  return s;
};

/** A canvas stand-in that only knows the calls a game may make, and rejects non-finite rectangles. */
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
    arc: (x: number, y: number, r: number) => finite(x, y, r),
    translate: (x: number, y: number) => finite(x, y),
    save: () => { depth += 1; },
    restore: () => { depth -= 1; },
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, depth: () => depth, rects: () => rects };
}

describe("pixel raiders", () => {
  it("starts with a full formation, four whole bunkers and three lives", () => {
    const s = game.create(seededRandom(1));
    expect(s.aliveCount).toBe(ROWS * COLS);
    expect(game.status(s)).toEqual({ score: 0, lives: 3, level: 1, over: false });
    const whole = bunkerCells(s, 0);
    expect(whole).toBeGreaterThan(60);
    for (let b = 1; b < 4; b += 1) expect(bunkerCells(s, b)).toBe(whole);
    expect(s.fy).toBe(waveStartY(1));
  });

  it("marches sideways one step at a time", () => {
    const s = calm();
    const x0 = s.fx;
    s.stepTimer = 0.001;
    game.step(s, idleInput(), RETRO_STEP, rnd);
    expect(s.fx).toBe(x0 + MARCH_X);
    expect(s.frame).toBe(1);
  });

  it("drops a row and turns around at the wall", () => {
    const s = calm();
    s.fx = WIDTH - COLS * CELL_W - 4;
    const y0 = s.fy;
    march(s, rnd);
    expect(s.fy).toBe(y0 + DROP_Y);
    expect(s.dir).toBe(-1);
    const x1 = s.fx;
    march(s, rnd);
    expect(s.fx).toBe(x1 - MARCH_X);
  });

  it("ignores empty outer columns when finding the wall", () => {
    const s = calm();
    for (let r = 0; r < ROWS; r += 1) s.alive[r * COLS + COLS - 1] = 0;
    s.aliveCount -= ROWS;
    s.fx = WIDTH - COLS * CELL_W - 4;
    const y0 = s.fy;
    march(s, rnd);
    expect(s.fy).toBe(y0);
  });

  it("speeds up as fewer aliens remain and in later waves", () => {
    expect(stepDelay(10, 1)).toBeLessThan(stepDelay(55, 1));
    expect(stepDelay(1, 1)).toBeLessThan(RETRO_STEP);
    expect(stepDelay(55, 4)).toBeLessThan(stepDelay(55, 1));
    expect(waveStartY(3)).toBeGreaterThan(waveStartY(1));
  });

  it("a shot kills the alien it hits and scores its kind", () => {
    const s = calm();
    // Bottom row, middle column: clear line of fire above the cannon.
    const row = ROWS - 1, col = 5;
    s.shots[0].active = true;
    s.shots[0].x = alienX(s, row, col) + 4;
    s.shots[0].y = alienY(s, row) + 12;
    run(s, idleInput(), 0.1);
    expect(s.alive[row * COLS + col]).toBe(0);
    expect(s.aliveCount).toBe(ROWS * COLS - 1);
    expect(s.score).toBe(KIND_POINTS[2]);
    expect(s.shots[0].active).toBe(false);
  });

  it("fires on the action button, at most two shots at once", () => {
    const s = calm();
    s.px = 4; // left of the first bunker, so the shots fly free
    run(s, { ...idleInput(), a: true }, 0.4);
    expect(s.shots.filter((x) => x.active).length).toBeGreaterThanOrEqual(1);
    run(s, { ...idleInput(), a: true }, 1);
    expect(s.shots.filter((x) => x.active).length).toBeLessThanOrEqual(2);
  });

  it("a tap shorter than one frame still fires (the press edge alone)", () => {
    const s = calm();
    s.px = 4;
    const tap = idleInput();
    tap.pressed.a = true;
    game.step(s, tap, RETRO_STEP, rnd);
    expect(s.shots.filter((x) => x.active).length).toBe(1);
  });

  it("player shots and alien shots chip bunkers cell by cell", () => {
    const s = calm();
    const before = bunkerCells(s, 1);
    s.shots[0].active = true;
    s.shots[0].x = BUNKER_X[1] + 10;
    s.shots[0].y = BUNKER_Y + 20;
    run(s, idleInput(), 0.2);
    const afterShot = bunkerCells(s, 1);
    expect(afterShot).toBeLessThan(before);
    expect(afterShot).toBeGreaterThan(before - 15);
    expect(s.shots[0].active).toBe(false);

    s.bombs[0].active = true;
    s.bombs[0].x = BUNKER_X[2] + 4;
    s.bombs[0].y = BUNKER_Y - 10;
    s.bombs[0].speed = 80;
    s.bombs[0].kind = 0;
    run(s, idleInput(), 0.3);
    expect(bunkerCells(s, 2)).toBeLessThan(before);
    expect(s.bombs[0].active).toBe(false);
  });

  it("an alien shot costs a life, then the cannon comes back", () => {
    const s = calm();
    s.px = 100;
    s.bombs[0].active = true;
    s.bombs[0].x = 106;
    s.bombs[0].y = PLAYER_Y - 8;
    s.bombs[0].speed = 80;
    run(s, idleInput(), 0.2);
    expect(s.lives).toBe(2);
    expect(s.phase).toBe("dying");
    run(s, idleInput(), 2);
    expect(s.phase).toBe("play");
    expect(game.status(s).over).toBe(false);
  });

  it("the last life lost ends the run", () => {
    const s = calm();
    s.lives = 1;
    s.px = 100;
    s.bombs[0].active = true;
    s.bombs[0].x = 106;
    s.bombs[0].y = PLAYER_Y - 4;
    s.bombs[0].speed = 80;
    run(s, idleInput(), 2);
    expect(game.status(s)).toMatchObject({ lives: 0, over: true });
  });

  it("clearing the formation starts a lower, faster wave with fresh bunkers", () => {
    const s = calm();
    s.alive.fill(0);
    s.alive[(ROWS - 1) * COLS + 5] = 1;
    s.aliveCount = 1;
    s.bunkers.fill(0);
    s.shots[0].active = true;
    s.shots[0].x = alienX(s, ROWS - 1, 5) + 4;
    s.shots[0].y = alienY(s, ROWS - 1) + 10;
    run(s, idleInput(), 0.1);
    expect(s.phase).toBe("clear");
    run(s, idleInput(), 2);
    expect(s.wave).toBe(2);
    expect(s.aliveCount).toBe(ROWS * COLS);
    expect(s.fy).toBe(waveStartY(2));
    expect(bunkerCells(s, 0)).toBeGreaterThan(60);
    expect(stepDelay(55, 2)).toBeLessThan(stepDelay(55, 1));
  });

  it("the formation reaching the cannon ends the run", () => {
    const s = calm();
    s.fy = PLAYER_Y - (ROWS - 1) * 16 - 8 - 2;
    s.fx = WIDTH - COLS * CELL_W - 4;
    march(s, rnd);
    run(s, idleInput(), 2);
    expect(game.status(s).over).toBe(true);
  });

  it("the saucer comes by and is worth bonus points", () => {
    const s = calm();
    s.saucerTimer = 0.01;
    run(s, idleInput(), 0.05);
    expect(s.saucer.active).toBe(true);
    s.saucer.x = 100;
    s.shots[0].active = true;
    s.shots[0].x = 108;
    s.shots[0].y = 40;
    const before = s.score;
    run(s, idleInput(), 0.1);
    expect(s.saucer.active).toBe(false);
    expect(s.score - before).toBe(s.popups.find((p) => p.active)?.value);
  });

  it("aliens shoot back but never more than the cap", () => {
    const s = game.create(seededRandom(3));
    s.stepTimer = 999;
    let most = 0;
    for (let i = 0; i < 1200 && s.phase === "play"; i += 1) {
      game.step(s, idleInput(), RETRO_STEP, rnd);
      most = Math.max(most, s.bombs.filter((b) => b.active).length);
    }
    expect(most).toBeGreaterThan(0);
    expect(most).toBeLessThanOrEqual(2);
  });

  it("draws every phase without throwing, idle and reduced included", () => {
    const s = game.create(seededRandom(2));
    for (const info of [
      { time: 0, reduced: false, idle: true },
      { time: 3.3, reduced: true, idle: true },
      { time: 1.2, reduced: false, idle: false },
    ]) {
      const fake = fakeContext();
      game.draw(fake.ctx, s, info);
      expect(fake.depth()).toBe(0);
      expect(fake.rects()).toBeGreaterThan(100);
    }
    // A busy frame: shots, bombs, saucer, a dying cannon, then game over.
    run(s, { ...idleInput(), a: true, right: true }, 3);
    s.saucer.active = true;
    s.saucer.x = 60;
    s.popups[0].active = true;
    s.popups[0].t = 1;
    s.popups[0].value = 150;
    s.shake = 0.3;
    s.phase = "dying";
    s.timer = 1;
    game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
    game.draw(fakeContext().ctx, s, { time: 2, reduced: true, idle: false });
    s.phase = "clear";
    game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
    s.phase = "over";
    game.draw(fakeContext().ctx, s, { time: 2, reduced: false, idle: false });
  });
});
