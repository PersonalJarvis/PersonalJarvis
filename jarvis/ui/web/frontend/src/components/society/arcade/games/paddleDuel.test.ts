import { describe, expect, it } from "vitest";
import { RETRO_STEP, idleInput, seededRandom, type RetroInput } from "../retroGame";
import { recordingContext } from "./games1Kit";
import paddleDuel, {
  BOTTOM, CPU_X, HEIGHT, HIT_SPEEDUP, MATCH_PAUSE, MAX_BALL_SPEED, PADDLE_H, PADDLE_W, PLAYER_SPEED, PLAYER_X, SERVE_DELAY,
  SERVE_SPEED, TOP, WIDTH, WIN_POINTS, cpuSkill, predictY, serve, type DuelState,
} from "./paddleDuel";

const random = seededRandom(3);
const fresh = (): DuelState => paddleDuel.create(seededRandom(3));
const step = (s: DuelState, input: RetroInput = idleInput(), rnd: () => number = random): void =>
  paddleDuel.step(s, input, RETRO_STEP, rnd);
const run = (s: DuelState, input: RetroInput, seconds: number): void => {
  for (let t = 0; t < seconds; t += RETRO_STEP) step(s, input);
};
const pressA = (): RetroInput => {
  const input = idleInput();
  input.a = true;
  input.pressed.a = true;
  return input;
};
/** A ball in flight at (x, y) with velocity (vx, vy). */
const rally = (s: DuelState, x: number, y: number, vx: number, vy: number): void => {
  Object.assign(s, { serving: false, serveTimer: 0, ballX: x, ballY: y, vx, vy, spin: 0, speed: Math.hypot(vx, vy) });
};

/** Where the ball will reach the player's face, folding wall bounces (spin ignored). */
function predictPlayerY(s: DuelState): number {
  const t = (s.ballX - (PLAYER_X + PADDLE_W)) / -s.vx;
  const lo = TOP + 2.5, span = BOTTOM - 2.5 - lo, period = span * 2;
  let y = ((s.ballY + s.vy * t - lo) % period + period) % period;
  if (y > span) y = period - y;
  return y + lo;
}

/** A good human: meets the ball near a paddle end, angling it away from the CPU. */
function botInput(s: DuelState): RetroInput {
  const input = idleInput();
  let target = HEIGHT / 2;
  if (!s.serving && s.vx < 0) {
    const y = predictPlayerY(s);
    target = s.cpuY < HEIGHT / 2 ? y - PADDLE_H * 0.38 : y + PADDLE_H * 0.38;
  }
  input.up = target < s.playerY - 2;
  input.down = target > s.playerY + 2;
  return input;
}

describe("paddle duel", () => {
  it("starts serving from the middle with nothing scored", () => {
    const s = fresh();
    expect(paddleDuel.status(s)).toEqual({ score: 0, level: 1, over: false, won: false });
    expect(paddleDuel.pointer).toBe(true);
    expect(s.serving).toBe(true);
    expect([s.ballX, s.ballY]).toEqual([WIDTH / 2, HEIGHT / 2]);
  });

  it("serves by itself after a short pause, or at once with A", () => {
    const s = fresh();
    run(s, idleInput(), SERVE_DELAY - 0.2);
    expect(s.serving).toBe(true);
    run(s, idleInput(), 0.3);
    expect(s.serving).toBe(false);
    expect(s.vx).toBeGreaterThan(0);
    expect(s.speed).toBe(SERVE_SPEED);

    const quick = fresh();
    step(quick, pressA());
    expect(quick.serving).toBe(false);
  });

  it("moves the player's paddle with the keys and the pointer, inside the court", () => {
    const s = fresh();
    run(s, { ...idleInput(), up: true }, 0.1);
    expect(s.playerY).toBeCloseTo(HEIGHT / 2 - PLAYER_SPEED * 0.1, -1);
    run(s, { ...idleInput(), up: true }, 3);
    expect(s.playerY).toBe(TOP + PADDLE_H / 2);
    const pointed = idleInput();
    pointed.pointer = { x: 50, y: 180, down: false, clicked: false };
    run(s, pointed, 1);
    expect(s.playerY).toBeCloseTo(180);
    run(s, { ...pointed, up: true }, 0.2);
    expect(s.playerY).toBeLessThan(170);
  });

  it("returns the ball at an angle set by the contact point", () => {
    const centre = fresh();
    rally(centre, PLAYER_X + 20, centre.playerY, -200, 0);
    run(centre, idleInput(), 0.1);
    expect(centre.vx).toBeGreaterThan(0);
    expect(Math.abs(centre.vy)).toBeLessThan(20);

    const edge = fresh();
    rally(edge, PLAYER_X + 20, edge.playerY + PADDLE_H / 2 - 1, -200, 0);
    run(edge, idleInput(), 0.1);
    expect(edge.vx).toBeGreaterThan(0);
    expect(edge.vy).toBeGreaterThan(100);
    expect(edge.rally).toBe(1);
    expect(edge.speed).toBe(200 + HIT_SPEEDUP);
  });

  it("puts spin on the ball from a moving paddle, and the ball curves", () => {
    const s = fresh();
    rally(s, PLAYER_X + 14, s.playerY, -200, 0);
    for (let i = 0; i < 6; i += 1) step(s, { ...idleInput(), down: true });
    expect(s.vx).toBeGreaterThan(0);
    expect(s.spin).toBeGreaterThan(0.5);
    const vy = s.vy;
    run(s, idleInput(), 0.2);
    expect(s.vy).toBeGreaterThan(vy);
  });

  it("speeds up with every return but never past the cap", () => {
    const s = fresh();
    rally(s, PLAYER_X + 20, s.playerY, -MAX_BALL_SPEED + 2, 0);
    run(s, idleInput(), 0.1);
    expect(s.speed).toBe(MAX_BALL_SPEED);
  });

  it("scores a won point with a rally bonus and serves to the loser", () => {
    const s = fresh();
    s.cpuY = TOP + PADDLE_H / 2;
    rally(s, CPU_X - 10, BOTTOM - 10, 300, 0);
    s.rally = 4;
    run(s, idleInput(), 0.3);
    expect(s.playerPoints).toBe(1);
    expect(s.score).toBe(100 + 4 * 10);
    expect(s.serving).toBe(true);
    expect(s.serveDir).toBe(1);

    rally(s, PLAYER_X + 30, BOTTOM - 10, -300, 0);
    s.playerY = TOP + PADDLE_H / 2;
    for (let i = 0; i < 20; i += 1) step(s, { ...idleInput(), up: true });
    expect(s.cpuPoints).toBe(1);
    expect(s.serveDir).toBe(-1);
  });

  it("predicts a ball off the wall", () => {
    const s = fresh();
    rally(s, WIDTH / 2, TOP + 20, 200, -200);
    const y = predictY(s);
    expect(y).toBeGreaterThan(TOP);
    expect(y).toBeLessThan(BOTTOM);
    // Fly it for real up to the CPU's face (or its return) and compare.
    while (s.vx > 0 && s.ballX + 2.5 < CPU_X) step(s);
    expect(Math.abs(s.ballY - y)).toBeLessThan(6);
  });

  it("gets sharper every match without becoming perfect", () => {
    for (let level = 1; level < 12; level += 1) {
      const a = cpuSkill(level), b = cpuSkill(level + 1);
      expect(b.reaction).toBeLessThanOrEqual(a.reaction);
      expect(b.speed).toBeGreaterThanOrEqual(a.speed);
      expect(b.error).toBeLessThanOrEqual(a.error);
    }
    expect(cpuSkill(2).speed).toBeGreaterThan(cpuSkill(1).speed);
    expect(cpuSkill(50).reaction).toBeGreaterThan(0);
    expect(cpuSkill(50).error).toBeGreaterThan(0);
    expect(cpuSkill(50).speed).toBeLessThan(MAX_BALL_SPEED);
  });

  it("returns a gentle serve at level one", () => {
    let returned = 0;
    for (let seed = 1; seed <= 10; seed += 1) {
      const rnd = seededRandom(seed);
      const s = paddleDuel.create(rnd);
      serve(s, rnd);
      for (let i = 0; i < 240 && s.vx > 0 && !s.serving; i += 1) step(s, idleInput(), rnd);
      if (!s.serving && s.vx < 0) returned += 1;
    }
    expect(returned).toBeGreaterThanOrEqual(7);
  });

  it("can be beaten: a good player wins the first match to seven and meets a harder CPU", () => {
    const rnd = seededRandom(21);
    const s = paddleDuel.create(rnd);
    for (let i = 0; i < 60 * 300 && s.level === 1 && !s.over; i += 1) paddleDuel.step(s, botInput(s), RETRO_STEP, rnd);
    expect(s.over).toBe(false);
    expect(s.level).toBe(2);
    expect(s.playerPoints).toBe(WIN_POINTS);
    expect(s.score).toBeGreaterThanOrEqual(WIN_POINTS * 100 + 1000);
    // After the break the scores reset for the next match.
    for (let t = 0; t < MATCH_PAUSE + 0.1; t += RETRO_STEP) paddleDuel.step(s, idleInput(), RETRO_STEP, rnd);
    expect([s.playerPoints, s.cpuPoints]).toEqual([0, 0]);
    expect(s.serving).toBe(true);
  });

  it("ends the run when the CPU takes a match", () => {
    const rnd = seededRandom(4);
    const s = paddleDuel.create(rnd);
    for (let i = 0; i < 60 * 300 && !s.over; i += 1) paddleDuel.step(s, idleInput(), RETRO_STEP, rnd);
    expect(paddleDuel.status(s)).toMatchObject({ over: true, won: false, level: 1 });
    expect(s.cpuPoints).toBe(WIN_POINTS);
    const frozen = s.ballX;
    step(s, pressA());
    expect(s.ballX).toBe(frozen);
  });

  it("draws every state without throwing, words or broken numbers", () => {
    const s = fresh();
    const states: [string, () => void][] = [
      ["serving", () => undefined],
      ["rally", () => { rally(s, 100, 100, 200, 60); s.spin = 0.6; run(s, idleInput(), 0.1); }],
      ["flash", () => { s.flash = 1; s.flashSide = -1; s.shake = 0.3; }],
      ["pause", () => { s.matchPause = 1; s.level = 4; }],
      ["over", () => { s.matchPause = 0; s.over = true; }],
    ];
    for (const [, setup] of states) {
      setup();
      for (const info of [
        { time: 0.5, reduced: false, idle: false },
        { time: 0.5, reduced: true, idle: false },
        { time: 0, reduced: false, idle: true },
      ]) {
        const rec = recordingContext();
        paddleDuel.draw(rec.ctx, s, info);
        expect(rec.calls.length).toBeGreaterThan(30);
        expect(rec.nonFinite).toBe(0);
        expect(rec.depth).toBe(0);
        expect(rec.ctx.globalAlpha).toBe(1);
        for (const text of rec.texts) expect(text).toMatch(/^\d+$/);
      }
    }
  });
});
