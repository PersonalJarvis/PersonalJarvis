import { describe, expect, it } from "vitest";
import { RETRO_STEP, idleInput, seededRandom, type RetroInput } from "../retroGame";
import { recordingContext } from "./games1Kit";
import brickBreaker, {
  BALL_R, BRICK_H, BRICK_W, COLS, HEIGHT, LAYOUTS, MAX_LIVES, MAX_SPEED, PADDLE_W, PADDLE_Y, ROWS, SLOW_FACTOR, START_LIVES, WIDE_W, WIDTH,
  brickIndex, brickX, brickY, currentSpeed, levelSpeed, liveBalls, moveBall, type BrickState, type PowerKind,
} from "./brickBreaker";

const random = seededRandom(5);
const fresh = (): BrickState => brickBreaker.create(seededRandom(5));
const run = (s: BrickState, input: RetroInput, seconds: number): void => {
  for (let t = 0; t < seconds; t += RETRO_STEP) brickBreaker.step(s, input, RETRO_STEP, random);
};
const launchInput = (): RetroInput => {
  const input = idleInput();
  input.a = true;
  input.pressed.a = true;
  return input;
};
const clearBoard = (s: BrickState): void => {
  s.bricks.fill(0);
  s.left = 0;
};
const placeBrick = (s: BrickState, col: number, row: number, hp: number): void => {
  s.bricks[brickIndex(col, row)] = hp;
  s.left += 1;
};
/** A free-flying ball at (x, y) with velocity (vx, vy). */
const flyBall = (s: BrickState, x: number, y: number, vx: number, vy: number) => {
  const b = s.balls[0];
  Object.assign(b, { alive: true, stuck: false, x, y, vx, vy });
  return b;
};

describe("brick breaker", () => {
  it("starts with three lives, a full board and the ball on the paddle", () => {
    const s = fresh();
    expect(brickBreaker.status(s)).toEqual({ score: 0, lives: START_LIVES, level: 1, over: false });
    expect(brickBreaker.pointer).toBe(true);
    const expected = LAYOUTS[0].join("").replace(/\./g, "").length;
    expect(s.left).toBe(expected);
    expect(liveBalls(s)).toBe(1);
    expect(s.balls[0].stuck).toBe(true);
    expect(s.balls[0].y).toBe(PADDLE_Y - BALL_R);
  });

  it("has at least five well-formed layouts", () => {
    expect(LAYOUTS.length).toBeGreaterThanOrEqual(5);
    for (const layout of LAYOUTS) {
      expect(layout).toHaveLength(ROWS);
      for (const row of layout) expect(row).toMatch(new RegExp(`^[.123]{${COLS}}$`));
      expect(layout.join("").replace(/\./g, "").length).toBeGreaterThan(20);
    }
    // The board fits the screen with room above the paddle.
    expect(brickX(COLS - 1) + BRICK_W).toBeLessThanOrEqual(WIDTH);
    expect(brickY(ROWS - 1) + BRICK_H).toBeLessThan(PADDLE_Y - 60);
  });

  it("moves the paddle with the keys and keeps it on screen, carrying the stuck ball", () => {
    const s = fresh();
    const x0 = s.paddleX;
    run(s, { ...idleInput(), right: true }, 0.2);
    expect(s.paddleX).toBeGreaterThan(x0);
    expect(s.balls[0].x).toBe(s.paddleX);
    run(s, { ...idleInput(), right: true }, 3);
    expect(s.paddleX).toBe(WIDTH - PADDLE_W / 2);
    run(s, { ...idleInput(), left: true }, 3);
    expect(s.paddleX).toBe(PADDLE_W / 2);
  });

  it("follows the pointer when it moves but not while it rests", () => {
    const s = fresh();
    const pointed = idleInput();
    pointed.pointer = { x: 80, y: 100, down: false, clicked: false };
    brickBreaker.step(s, pointed, RETRO_STEP, random);
    expect(s.paddleX).toBe(80);
    const keys = { ...pointed, right: true };
    run(s, keys, 0.3);
    expect(s.paddleX).toBeGreaterThan(100);
  });

  it("launches with A and climbs", () => {
    const s = fresh();
    run(s, idleInput(), 0.5);
    expect(s.balls[0].stuck).toBe(true);
    brickBreaker.step(s, launchInput(), RETRO_STEP, random);
    const b = s.balls[0];
    expect(b.stuck).toBe(false);
    expect(b.vy).toBeLessThan(0);
    const y0 = b.y;
    run(s, idleInput(), 0.1);
    expect(b.y).toBeLessThan(y0);
  });

  it("bounces off the paddle at an angle set by where the ball lands", () => {
    const s = fresh();
    clearBoard(s);
    placeBrick(s, 0, 0, 3);
    const centre = flyBall(s, s.paddleX, PADDLE_Y - 10, 0, 150);
    moveBall(s, centre, 0.1, random);
    expect(centre.vy).toBeLessThan(0);
    expect(Math.abs(centre.vx)).toBeLessThan(5);
    const edge = flyBall(s, s.paddleX + PADDLE_W / 2 - 1, PADDLE_Y - 10, 0, 150);
    moveBall(s, edge, 0.1, random);
    expect(edge.vy).toBeLessThan(0);
    expect(edge.vx).toBeGreaterThan(100);
  });

  it("breaks a one-hit brick and wears down a three-hit brick", () => {
    const s = fresh();
    clearBoard(s);
    placeBrick(s, 5, 4, 1);
    placeBrick(s, 0, 0, 3);
    const bx = brickX(5) + BRICK_W / 2;
    const b = flyBall(s, bx, brickY(4) + BRICK_H + 20, 0, -200);
    moveBall(s, b, 0.2, random);
    expect(s.bricks[brickIndex(5, 4)]).toBe(0);
    expect(s.left).toBe(1);
    expect(s.score).toBeGreaterThan(0);
    expect(b.vy).toBeGreaterThan(0);

    const tough = brickIndex(0, 0);
    for (let hit = 3; hit > 0; hit -= 1) {
      expect(s.bricks[tough]).toBe(hit);
      const ball = flyBall(s, brickX(0) + BRICK_W / 2, brickY(0) + BRICK_H + 10, 0, -200);
      moveBall(s, ball, 0.1, random);
    }
    expect(s.bricks[tough]).toBe(0);
  });

  it("never tunnels through a brick, however fast the ball", () => {
    for (const speed of [2000, 8000, 30000]) {
      const s = fresh();
      clearBoard(s);
      placeBrick(s, 6, 5, 1);
      placeBrick(s, 0, 0, 3);
      s.paddleX = WIDTH - PADDLE_W; // out of the way, so the rebound is not returned
      const x = brickX(6) + BRICK_W / 2;
      const b = flyBall(s, x, brickY(5) + BRICK_H + 4, 0, -speed);
      moveBall(s, b, RETRO_STEP, random);
      expect(s.bricks[brickIndex(6, 5)]).toBe(0);
      expect(b.vy).toBeGreaterThan(0);
      // Diagonally, too.
      placeBrick(s, 3, 3, 1);
      const d = flyBall(s, brickX(3) - 20, brickY(3) + BRICK_H + 20, speed, -speed);
      moveBall(s, d, RETRO_STEP, random);
      expect(s.bricks[brickIndex(3, 3)]).toBe(0);
    }
  });

  it("loses a life when the last ball drops and ends the run after three", () => {
    const s = fresh();
    for (let life = START_LIVES; life > 0; life -= 1) {
      expect(s.lives).toBe(life);
      flyBall(s, 10, HEIGHT - 2, 0, 200);
      s.paddleX = WIDTH - PADDLE_W;
      run(s, idleInput(), 0.2);
    }
    expect(brickBreaker.status(s)).toMatchObject({ lives: 0, over: true });
  });

  it("clears the board into the next layout with a bonus", () => {
    const s = fresh();
    clearBoard(s);
    placeBrick(s, 4, 2, 1);
    flyBall(s, brickX(4) + BRICK_W / 2, brickY(2) + BRICK_H + 6, 0, -150);
    run(s, idleInput(), 0.1);
    expect(s.level).toBe(2);
    expect(s.score).toBeGreaterThanOrEqual(500);
    expect(s.left).toBe(LAYOUTS[1].join("").replace(/\./g, "").length);
    expect(s.balls[0].stuck).toBe(true);
    expect(s.speed).toBe(levelSpeed(2));
  });

  it("repeats the layouts faster, but never past the cap", () => {
    expect(levelSpeed(LAYOUTS.length + 1)).toBeGreaterThan(levelSpeed(LAYOUTS.length));
    expect(levelSpeed(LAYOUTS.length + 1)).toBeGreaterThan(levelSpeed(1));
    expect(levelSpeed(500)).toBe(MAX_SPEED);
    const s = fresh();
    s.speed = 99999;
    expect(currentSpeed(s)).toBe(MAX_SPEED);
    brickBreaker.step(s, launchInput(), RETRO_STEP, random);
    run(s, idleInput(), 0.2);
    for (const b of s.balls) if (b.alive && !b.stuck) expect(Math.hypot(b.vx, b.vy)).toBeLessThanOrEqual(MAX_SPEED + 1e-6);
  });

  const catchDrop = (s: BrickState, kind: PowerKind): void => {
    Object.assign(s.drops[0], { alive: true, kind, x: s.paddleX, y: PADDLE_Y - 6 });
    run(s, idleInput(), 0.3);
  };

  it("power-ups: wide paddle, multi-ball, slow ball and an extra life", () => {
    const wide = fresh();
    catchDrop(wide, "wide");
    expect(wide.drops[0].alive).toBe(false);
    run(wide, idleInput(), 1);
    expect(wide.paddleW).toBe(WIDE_W);

    const multi = fresh();
    catchDrop(multi, "multi");
    expect(liveBalls(multi)).toBe(3);

    const slow = fresh();
    const before = currentSpeed(slow);
    catchDrop(slow, "slow");
    expect(currentSpeed(slow)).toBeCloseTo(before * SLOW_FACTOR);

    const life = fresh();
    catchDrop(life, "life");
    expect(life.lives).toBe(START_LIVES + 1);
    life.lives = MAX_LIVES;
    catchDrop(life, "life");
    expect(life.lives).toBe(MAX_LIVES);
  });

  it("lets a missed capsule fall away", () => {
    const s = fresh();
    s.paddleX = 30;
    Object.assign(s.drops[0], { alive: true, kind: "life", x: 250, y: PADDLE_Y - 6 });
    run(s, idleInput(), 1);
    expect(s.drops[0].alive).toBe(false);
    expect(s.lives).toBe(START_LIVES);
  });

  it("plays a long automated game without escaping the screen", () => {
    const rnd = seededRandom(9);
    const s = brickBreaker.create(rnd);
    let maxLevel = 1;
    for (let i = 0; i < 60 * 240 && !s.over; i += 1) {
      // A decent player: follow the lowest falling ball, slightly off-centre to aim.
      let target = s.paddleX;
      let lowest = -1;
      for (const b of s.balls) if (b.alive && b.y > lowest) { lowest = b.y; target = b.x + (b.vx > 0 ? -8 : 8); }
      const input = idleInput();
      input.left = target < s.paddleX - 3;
      input.right = target > s.paddleX + 3;
      input.pressed.a = i % 30 === 0;
      brickBreaker.step(s, input, RETRO_STEP, rnd);
      maxLevel = Math.max(maxLevel, s.level);
      for (const b of s.balls) {
        if (!b.alive) continue;
        expect(Number.isFinite(b.x) && Number.isFinite(b.y)).toBe(true);
        expect(b.x).toBeGreaterThanOrEqual(BALL_R - 1e-9);
        expect(b.x).toBeLessThanOrEqual(WIDTH - BALL_R + 1e-9);
        expect(b.y).toBeGreaterThanOrEqual(BALL_R - 1e-9);
      }
    }
    expect(s.score).toBeGreaterThan(1000);
    expect(maxLevel).toBeGreaterThanOrEqual(2);
  });

  it("draws every state without throwing, words or broken numbers", () => {
    const s = fresh();
    s.bricks[brickIndex(1, 1)] = 2;
    s.hitFlash[brickIndex(1, 1)] = 0.05;
    const kinds: PowerKind[] = ["wide", "multi", "slow", "life"];
    kinds.forEach((kind, i) => Object.assign(s.drops[i], { alive: true, kind, x: 40 + i * 50, y: 150 }));
    for (const info of [
      { time: 0, reduced: false, idle: true },
      { time: 1, reduced: false, idle: false },
      { time: 2, reduced: true, idle: false },
    ]) {
      const rec = recordingContext();
      brickBreaker.draw(rec.ctx, s, info);
      expect(rec.calls.length).toBeGreaterThan(100);
      expect(rec.nonFinite).toBe(0);
      expect(rec.depth).toBe(0);
      expect(rec.ctx.globalAlpha).toBe(1);
      for (const text of rec.texts) expect(text).not.toMatch(/[a-z]/i);
    }
    s.over = true;
    s.shake = 0.3;
    s.flash = 0.5;
    s.wideTime = 2;
    s.slowTime = 2;
    flyBall(s, 100, 100, 50, -50);
    const rec = recordingContext();
    brickBreaker.draw(rec.ctx, s, { time: 3, reduced: false, idle: false });
    expect(rec.nonFinite).toBe(0);
    expect(rec.depth).toBe(0);
  });
});
