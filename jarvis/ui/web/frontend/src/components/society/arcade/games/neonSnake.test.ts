import { describe, expect, it } from "vitest";
import { RETRO_STEP, idleInput, seededRandom, type RetroButtons, type RetroInput } from "../retroGame";
import { recordingContext } from "./games1Kit";
import neonSnake, {
  BONUS_EVERY, BONUS_S, COLS, DOWN, EMPTY, FOODS_PER_LEVEL, LEFT, MAX_QUEUED_TURNS, RIGHT, ROWS, SNAKE, START_LENGTH, UP, WALL,
  cellOf, moveInterval, queueTurn, type SnakeState,
} from "./neonSnake";

const random = seededRandom(7);
const fresh = (seed = 7): SnakeState => neonSnake.create(seededRandom(seed));
const press = (...keys: (keyof RetroButtons)[]): RetroInput => {
  const input = idleInput();
  for (const k of keys) { input[k] = true; input.pressed[k] = true; }
  return input;
};
/** Run exactly one movement tick (the clock is due, no time passes). */
const tick = (s: SnakeState, input: RetroInput = idleInput()): void => {
  s.clock = moveInterval(s);
  neonSnake.step(s, input, 0, random);
};
const headXY = (s: SnakeState): [number, number] => [s.body[0] % COLS, Math.floor(s.body[0] / COLS)];
/** Replace the snake with the given cells (head first), keeping obstacles. */
const setBody = (s: SnakeState, cells: [number, number][], dir: number): void => {
  for (const c of s.body) s.grid[c] = EMPTY;
  s.body = cells.map(([x, y]) => cellOf(x, y));
  for (const c of s.body) s.grid[c] = SNAKE;
  s.dir = dir;
  s.queue = [];
  s.grow = 0;
};
/** Put the food somewhere far from the action. */
const parkFood = (s: SnakeState): void => { s.food = cellOf(0, ROWS - 1); };

describe("neon snake", () => {
  it("starts as a short snake heading right with food on a free cell", () => {
    const s = fresh();
    expect(s.body).toHaveLength(START_LENGTH);
    expect(s.dir).toBe(RIGHT);
    expect(s.food).toBeGreaterThanOrEqual(0);
    expect(s.grid[s.food]).toBe(EMPTY);
    expect(neonSnake.status(s)).toEqual({ score: 0, level: 1, over: false, won: false });
    expect(neonSnake.width).toBe(320);
    expect(neonSnake.height).toBe(240);
  });

  it("is reproducible from the same seed", () => {
    expect(fresh(3).food).toBe(fresh(3).food);
  });

  it("moves one cell per tick at the tick rate", () => {
    const s = fresh();
    parkFood(s);
    const [x0] = headXY(s);
    for (let t = 0; t < 1; t += RETRO_STEP) neonSnake.step(s, idleInput(), RETRO_STEP, random);
    const [x1] = headXY(s);
    expect(x1 - x0).toBeGreaterThanOrEqual(6);
    expect(x1 - x0).toBeLessThanOrEqual(8);
  });

  it("ignores a turn straight back into its neck", () => {
    const s = fresh();
    parkFood(s);
    const [x0, y0] = headXY(s);
    tick(s, press("left"));
    expect(s.dir).toBe(RIGHT);
    expect(headXY(s)).toEqual([x0 + 1, y0]);
  });

  it("buffers two quick turns between ticks and drops a third", () => {
    const s = fresh();
    parkFood(s);
    const [x0, y0] = headXY(s);
    neonSnake.step(s, press("up"), 0, random);
    neonSnake.step(s, press("left"), 0, random);
    neonSnake.step(s, press("down"), 0, random);
    expect(s.queue).toEqual([UP, LEFT]);
    expect(s.queue.length).toBe(MAX_QUEUED_TURNS);
    tick(s);
    expect(headXY(s)).toEqual([x0, y0 - 1]);
    tick(s);
    expect(s.dir).toBe(LEFT);
    expect(headXY(s)).toEqual([x0 - 1, y0 - 1]);
  });

  it("judges reversal against the last queued turn, not the current heading", () => {
    const s = fresh();
    queueTurn(s, UP);
    queueTurn(s, DOWN); // reverses the queued up
    expect(s.queue).toEqual([UP]);
    queueTurn(s, LEFT); // left is fine after up, though it reverses the current right
    expect(s.queue).toEqual([UP, LEFT]);
  });

  it("eats food, scores and grows by one", () => {
    const s = fresh();
    const [x, y] = headXY(s);
    s.food = cellOf(x + 1, y);
    tick(s);
    expect(s.score).toBe(10);
    expect(s.eaten).toBe(1);
    expect(s.food).not.toBe(cellOf(x + 1, y));
    parkFood(s);
    tick(s);
    expect(s.body).toHaveLength(START_LENGTH + 1);
  });

  it("ends the run at the wall", () => {
    const s = fresh();
    parkFood(s);
    for (let i = 0; i < COLS && !s.over; i += 1) tick(s);
    expect(neonSnake.status(s).over).toBe(true);
    const body = [...s.body];
    tick(s, press("up"));
    expect(s.body).toEqual(body);
  });

  it("ends the run when it bites itself", () => {
    const s = fresh();
    parkFood(s);
    setBody(s, [[5, 5], [6, 5], [6, 6], [5, 6], [4, 6], [3, 6]], LEFT);
    tick(s, press("down"));
    expect(s.over).toBe(true);
  });

  it("may follow its own tail into the cell the tail is leaving", () => {
    const s = fresh();
    parkFood(s);
    setBody(s, [[5, 5], [6, 5], [6, 6], [5, 6]], LEFT);
    tick(s, press("down"));
    expect(s.over).toBe(false);
    expect(headXY(s)).toEqual([5, 6]);
  });

  it("crashes into an obstacle", () => {
    const s = fresh();
    parkFood(s);
    const [x, y] = headXY(s);
    s.grid[cellOf(x + 1, y)] = WALL;
    tick(s);
    expect(s.over).toBe(true);
  });

  it("levels up every few foods and drops obstacles clear of the snake's path", () => {
    const s = fresh();
    for (let i = 0; i < FOODS_PER_LEVEL; i += 1) {
      const [x, y] = headXY(s);
      s.bonus = -1;
      s.food = cellOf(x + 1, y);
      tick(s);
    }
    expect(s.level).toBe(2);
    expect(s.score).toBe(10 * FOODS_PER_LEVEL);
    expect(s.obstacles).toHaveLength(4);
    const [hx, hy] = headXY(s);
    for (const c of s.obstacles) {
      const ox = c % COLS, oy = Math.floor(c / COLS);
      expect(s.body).not.toContain(c);
      expect(Math.abs(ox - hx) + Math.abs(oy - hy)).toBeGreaterThan(4);
      expect(oy === hy && ox > hx && ox - hx <= 8).toBe(false);
      expect(s.grid[c]).toBe(WALL);
    }
    // Food scores more on a higher level.
    const [x, y] = headXY(s);
    s.food = cellOf(x + 1, y);
    tick(s);
    expect(s.score).toBe(10 * FOODS_PER_LEVEL + 20);
  });

  it("speeds up with length and level but never past the floor", () => {
    const s = fresh();
    const base = moveInterval(s);
    s.body.push(0, 1, 2, 3, 4);
    expect(moveInterval(s)).toBeLessThan(base);
    s.level = 3;
    const higher = moveInterval(s);
    expect(higher).toBeLessThan(base);
    s.level = 99;
    expect(moveInterval(s)).toBe(0.05);
  });

  it("offers a timed bonus fruit worth more the sooner it is eaten", () => {
    const s = fresh();
    for (let i = 0; i < BONUS_EVERY; i += 1) {
      const [x, y] = headXY(s);
      s.food = cellOf(x + 1, y);
      tick(s);
    }
    expect(s.bonus).toBeGreaterThanOrEqual(0);
    expect(s.bonusTime).toBe(BONUS_S);
    const scoreBefore = s.score;
    const [x, y] = headXY(s);
    s.bonus = cellOf(x + 1, y);
    parkFood(s);
    tick(s);
    expect(s.score - scoreBefore).toBe(50 + BONUS_S * 10);
    expect(s.bonus).toBe(-1);
  });

  it("lets an uneaten bonus fruit expire", () => {
    const s = fresh();
    s.bonus = cellOf(20, 3);
    s.bonusTime = BONUS_S;
    parkFood(s);
    setBody(s, [[3, 20], [2, 20], [1, 20], [0, 20]], UP);
    for (let t = 0; t < BONUS_S + 0.1; t += RETRO_STEP) {
      s.clock = 0; // hold still
      neonSnake.step(s, idleInput(), RETRO_STEP, random);
    }
    expect(s.bonus).toBe(-1);
  });

  it("keeps the grid and the body in sync through a long random game", () => {
    const rnd = seededRandom(11);
    const s = neonSnake.create(rnd);
    const keys: (keyof RetroButtons)[] = ["up", "down", "left", "right"];
    for (let i = 0; i < 4000 && !s.over; i += 1) {
      const input = rnd() < 0.08 ? press(keys[Math.floor(rnd() * 4)]) : idleInput();
      neonSnake.step(s, input, RETRO_STEP, rnd);
      let snakeCells = 0;
      for (const v of s.grid) if (v === SNAKE) snakeCells += 1;
      expect(snakeCells).toBe(s.body.length);
    }
  });

  it("draws every state without throwing, words or broken numbers", () => {
    const s = fresh();
    s.bonus = cellOf(20, 3);
    s.bonusTime = 1;
    s.obstacles.push(cellOf(25, 20));
    for (const info of [
      { time: 0, reduced: false, idle: true },
      { time: 1.3, reduced: false, idle: false },
      { time: 2.1, reduced: true, idle: false },
    ]) {
      const rec = recordingContext();
      neonSnake.draw(rec.ctx, s, info);
      expect(rec.calls.length).toBeGreaterThan(50);
      expect(rec.nonFinite).toBe(0);
      expect(rec.depth).toBe(0);
      expect(rec.ctx.globalAlpha).toBe(1);
      for (const text of rec.texts) expect(text).not.toMatch(/[a-z]/i);
    }
    tick(s, press("up"));
    s.grid[s.body[0] - COLS] = WALL;
    tick(s);
    expect(s.over).toBe(true);
    const rec = recordingContext();
    neonSnake.draw(rec.ctx, s, { time: 3, reduced: false, idle: false });
    expect(rec.nonFinite).toBe(0);
  });
});
