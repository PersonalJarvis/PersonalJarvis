import { describe, expect, it } from "vitest";
import { RETRO_STEP, idleInput, seededRandom, type RetroInput } from "../retroGame";
import blockDrop, {
  ARR, CLEAR_TIME, COLS, DAS, HIDDEN, LINE_SCORES, LOCK_DELAY, MAX_LOCK_RESETS, PIECE_COUNT, PIECE_I, PIECE_O, PIECE_T, ROWS, SHAPES,
  createBlockDrop, fits, gravityInterval, spawnPiece, tryRotate, type BlockDropState,
} from "./blockDrop";

/** A 2D context stand-in: every call is a no-op, texts are recorded, non-finite numbers are flagged. */
function fakeContext(texts: string[], bad: string[]): CanvasRenderingContext2D {
  const target: Record<string, unknown> = {
    fillText: (s: unknown) => { texts.push(String(s)); },
    createLinearGradient: () => ({ addColorStop: () => undefined }),
    createRadialGradient: () => ({ addColorStop: () => undefined }),
    measureText: (s: string) => ({ width: s.length * 6 }),
  };
  return new Proxy(target, {
    get: (t, key) => {
      if (typeof key === "string" && key in t) return t[key];
      return (...args: unknown[]) => {
        if (args.some((a) => typeof a === "number" && !Number.isFinite(a))) bad.push(String(key));
      };
    },
    set: (t, key, value) => { t[String(key)] = value; return true; },
  }) as unknown as CanvasRenderingContext2D;
}

const run = (s: BlockDropState, input: RetroInput, steps: number, random: () => number) => {
  for (let i = 0; i < steps; i += 1) blockDrop.step(s, input, RETRO_STEP, random);
};
const press = (button: "left" | "right" | "up" | "down" | "a" | "b"): RetroInput => {
  const input = idleInput();
  input[button] = true;
  input.pressed[button] = true;
  return input;
};
const hold = (button: "left" | "right" | "down"): RetroInput => {
  const input = idleInput();
  input[button] = true;
  return input;
};
/** A fresh run with a given piece falling at a given spot. */
const withPiece = (kind: number, x = 3, y = 5): BlockDropState => {
  const s = createBlockDrop(seededRandom(1));
  s.piece = kind; s.rot = 0; s.px = x; s.py = y; s.active = true; s.lowestRow = y;
  return s;
};
const cellsOf = (s: BlockDropState): Array<[number, number]> => {
  const shape = SHAPES[s.piece][s.rot];
  const out: Array<[number, number]> = [];
  for (let i = 0; i < 4; i += 1) out.push([s.px + shape[i * 2], s.py + shape[i * 2 + 1]]);
  return out;
};

describe("block drop", () => {
  it("starts with a falling piece, an empty well and a full preview queue", () => {
    const s = blockDrop.create(seededRandom(7));
    expect(s.active).toBe(true);
    expect(s.cells.every((c) => c === 0)).toBe(true);
    expect(s.queue.length).toBeGreaterThanOrEqual(PIECE_COUNT);
    expect(blockDrop.status(s)).toEqual({ score: 0, level: 1, over: false });
    expect(blockDrop.width).toBeGreaterThan(0);
  });

  it("deals every one of the seven pieces once per bag", () => {
    const random = seededRandom(42);
    const s = createBlockDrop(random);
    const dealt: number[] = [s.piece];
    for (let i = 0; i < 20; i += 1) { spawnPiece(s, random); dealt.push(s.piece); }
    for (let bag = 0; bag < 3; bag += 1) {
      expect([...dealt.slice(bag * 7, bag * 7 + 7)].sort()).toEqual([0, 1, 2, 3, 4, 5, 6]);
    }
  });

  it("has four distinct rotations that come back round, each a solid four-cell piece", () => {
    for (let kind = 0; kind < PIECE_COUNT; kind += 1) {
      const s = withPiece(kind);
      const start = JSON.stringify(cellsOf(s));
      for (let r = 0; r < 4; r += 1) expect(tryRotate(s)).toBe(true);
      expect(s.rot).toBe(0);
      expect(JSON.stringify(cellsOf(s))).toBe(start);
    }
    const t = withPiece(PIECE_T);
    tryRotate(t);
    expect(t.rot).toBe(1);
    // The T pointing right: a vertical bar of three with a nub on its right.
    expect(cellsOf(t).sort()).toEqual([[4, 5], [4, 6], [4, 7], [5, 6]].sort());
  });

  it("rotates with up or a, and kicks off a wall instead of refusing", () => {
    const s = withPiece(PIECE_T);
    run(s, press("up"), 1, seededRandom(1));
    expect(s.rot).toBe(1);
    run(s, idleInput(), 1, seededRandom(1));
    run(s, press("a"), 1, seededRandom(1));
    expect(s.rot).toBe(2);

    // A vertical I flush against the right wall would stick out when turned flat: it kicks left.
    const i = withPiece(PIECE_I, 0, 5);
    tryRotate(i);
    i.px = COLS - 3; // vertical I occupies column px + 2 = the last column
    expect(cellsOf(i).every(([x]) => x === COLS - 1)).toBe(true);
    expect(tryRotate(i)).toBe(true);
    expect(cellsOf(i).every(([x]) => x >= 0 && x < COLS)).toBe(true);
    expect(i.rot).toBe(2);
  });

  it("refuses a rotation when no kick fits", () => {
    const s = withPiece(PIECE_I, 3, 10);
    // Bury the flat I in a one-row slot: no room to stand up anywhere nearby.
    for (let y = 0; y < ROWS; y += 1) for (let x = 0; x < COLS; x += 1) if (y !== 11) s.cells[y * COLS + x] = 1;
    for (let x = 0; x < COLS; x += 1) if (x < 3 || x > 6) s.cells[11 * COLS + x] = 1;
    expect(fits(s, PIECE_I, 0, 3, 10)).toBe(true);
    expect(tryRotate(s)).toBe(false);
    expect(s.rot).toBe(0);
  });

  it("shifts once on press, then auto-repeats after the delay", () => {
    const s = withPiece(PIECE_O, 4, 5);
    s.lockTimer = 0;
    const random = seededRandom(1);
    run(s, press("left"), 1, random);
    expect(s.px).toBe(3);
    const steps = Math.floor(DAS / RETRO_STEP) - 2;
    run(s, hold("left"), steps, random);
    expect(s.px).toBe(3);
    run(s, hold("left"), Math.ceil(ARR / RETRO_STEP) * 2 + 3, random);
    expect(s.px).toBeLessThan(3);
    run(s, hold("left"), 60, random);
    expect(s.px).toBe(0);
  });

  it("clears a full line, scores it times the level and drops the rows above", () => {
    const random = seededRandom(3);
    const s = withPiece(PIECE_I, 3, 5);
    const bottom = ROWS - 1;
    for (let x = 0; x < COLS; x += 1) if (x < 3 || x > 6) s.cells[bottom * COLS + x] = 2;
    s.cells[(bottom - 1) * COLS] = 3; // a lone cell above that should fall one row
    s.level = 2;
    run(s, press("b"), 1, random);
    expect(s.clearing).toBeGreaterThan(0);
    expect(s.lines).toBe(1);
    const dropBonus = (bottom - 1 - 5) * 2;
    expect(s.score).toBe(LINE_SCORES[1] * 2 + dropBonus);
    run(s, idleInput(), Math.ceil(CLEAR_TIME / RETRO_STEP) + 1, random);
    expect(s.clearing).toBe(0);
    expect(s.cells[bottom * COLS]).toBe(3);
    for (let x = 1; x < COLS; x += 1) expect(s.cells[bottom * COLS + x]).toBe(0);
    expect(s.active).toBe(true);
  });

  it("pays more for four lines at once and levels up every ten lines", () => {
    const random = seededRandom(5);
    const s = withPiece(PIECE_I, 0, 5);
    tryRotate(s); // vertical, in column px + 2
    s.px = -2;
    expect(fits(s, PIECE_I, 1, -2, 5)).toBe(true);
    for (let y = ROWS - 4; y < ROWS; y += 1) for (let x = 1; x < COLS; x += 1) s.cells[y * COLS + x] = 5;
    s.lines = 8;
    run(s, press("b"), 1, random);
    expect(s.lines).toBe(12);
    expect(s.level).toBe(2);
    expect(s.score).toBe(LINE_SCORES[4] * 1 + (ROWS - 4 - 5) * 2);
    expect(gravityInterval(2)).toBeLessThan(gravityInterval(1));
  });

  it("locks a grounded piece after the lock delay, and limits how often moves restart it", () => {
    const random = seededRandom(9);
    const s = withPiece(PIECE_O, 4, ROWS - 2);
    run(s, idleInput(), Math.floor(LOCK_DELAY / RETRO_STEP) - 3, random);
    expect(s.cells.some((c) => c !== 0)).toBe(false);
    run(s, idleInput(), 6, random);
    expect(s.cells[(ROWS - 1) * COLS + 4]).toBe(PIECE_O + 1);

    const t = withPiece(PIECE_T, 4, ROWS - 2);
    let steps = 0;
    // Wiggle left and right forever: the piece must still lock eventually.
    while (t.cells.every((c) => c === 0) && steps < 2000) {
      run(t, press(steps % 2 === 0 ? "left" : "right"), 1, random);
      run(t, idleInput(), 5, random);
      steps += 1;
    }
    expect(t.cells.some((c) => c !== 0)).toBe(true);
    expect(steps).toBeLessThanOrEqual(MAX_LOCK_RESETS + 10);
  });

  it("soft drop falls faster and scores a point per row", () => {
    const random = seededRandom(2);
    const s = withPiece(PIECE_O, 4, 2);
    run(s, hold("down"), 30, random);
    expect(s.py).toBeGreaterThan(10);
    expect(s.score).toBe(s.py - 2);
  });

  it("tops out when a new piece has no room", () => {
    const random = seededRandom(4);
    const s = createBlockDrop(random);
    for (let y = HIDDEN; y < ROWS; y += 1) for (let x = 0; x < COLS; x += 1) if (x !== 0) s.cells[y * COLS + x] = 1;
    s.cells[1 * COLS + 4] = 1;
    s.cells[1 * COLS + 5] = 1;
    spawnPiece(s, random);
    expect(s.over).toBe(true);
    expect(blockDrop.status(s).over).toBe(true);
    // A finished run ignores input.
    const score = s.score;
    run(s, press("b"), 5, random);
    expect(s.score).toBe(score);
  });

  it("tops out by stacking pieces in the middle", () => {
    const random = seededRandom(11);
    const s = blockDrop.create(random);
    let steps = 0;
    while (!s.over && steps < 400) { run(s, press("b"), 1, random); run(s, idleInput(), 30, random); steps += 1; }
    expect(s.over).toBe(true);
  });

  it("draws every state without throwing, without words and without bad numbers", () => {
    const texts: string[] = [];
    const bad: string[] = [];
    const ctx = fakeContext(texts, bad);
    const random = seededRandom(8);
    const s = blockDrop.create(random);
    blockDrop.draw(ctx, s, { time: 0, reduced: false, idle: true });
    blockDrop.draw(ctx, s, { time: 1.3, reduced: true, idle: true });
    run(s, press("b"), 1, random);
    blockDrop.draw(ctx, s, { time: 2, reduced: false, idle: false });
    const line = withPiece(PIECE_I, 3, 5);
    for (let x = 0; x < COLS; x += 1) if (x < 3 || x > 6) line.cells[(ROWS - 1) * COLS + x] = 2;
    run(line, press("b"), 1, random);
    run(line, idleInput(), 5, random);
    blockDrop.draw(ctx, line, { time: 3, reduced: false, idle: false });
    blockDrop.draw(ctx, line, { time: 3, reduced: true, idle: true });
    line.over = true;
    blockDrop.draw(ctx, line, { time: 4, reduced: false, idle: false });
    expect(bad).toEqual([]);
    expect(texts.length).toBeGreaterThan(0);
    for (const text of texts) expect(text).not.toMatch(/[A-Za-z]/);
  });

  it("is deterministic for a given seed", () => {
    const play = () => {
      const random = seededRandom(123);
      const s = blockDrop.create(random);
      for (let i = 0; i < 600; i += 1) blockDrop.step(s, i % 40 === 0 ? press("b") : i % 7 === 0 ? press("left") : idleInput(), RETRO_STEP, random);
      return [s.score, s.lines, s.piece, Array.from(s.cells).join("")].join("|");
    };
    expect(play()).toBe(play());
  });
});
