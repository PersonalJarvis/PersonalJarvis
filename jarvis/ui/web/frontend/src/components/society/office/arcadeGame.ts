/**
 * The break-room arcade game: a small invaders shooter. Pure state and a
 * fixed-rate `stepArcade`, so the rules are testable without a canvas; the
 * cabinet overlay (ArcadeCabinet) only feeds input and draws the state.
 */

/** Playfield size in logical pixels; the canvas scales it up crisply. */
export const ARCADE_W = 224;
export const ARCADE_H = 256;

const SHIP_Y = ARCADE_H - 24;
const SHIP_HALF = 7;
const SHIP_SPEED = 110;
const SHOT_SPEED = 260;
const BOMB_SPEED = 95;
const COLS = 9;
const ROWS = 5;
const CELL_X = 18;
const CELL_Y = 15;
export const INVADER_W = 11;
export const INVADER_H = 8;
/** Points per invader by row, top row worth most. */
const ROW_POINTS = [30, 20, 20, 10, 10];
/** How long the ship blinks after a hit before play resumes (s). */
const RESPAWN_S = 1.2;

export type ArcadePhase = "ready" | "playing" | "paused" | "over";

export interface Invader { col: number; row: number; alive: boolean }
export interface Shot { x: number; y: number }

export interface ArcadeState {
  phase: ArcadePhase;
  score: number;
  lives: number;
  wave: number;
  shipX: number;
  /** Seconds of post-hit grace left; the ship neither moves nor dies meanwhile. */
  hitTimer: number;
  shot: Shot | null;
  bombs: Shot[];
  invaders: Invader[];
  /** Top-left corner of the invader block. */
  swarmX: number;
  swarmY: number;
  swarmDir: 1 | -1;
  /** Seconds until the next bomb drops. */
  bombTimer: number;
  /** Animation frame of the marching invaders (0/1), flips per swarm step. */
  frame: 0 | 1;
  stepTimer: number;
}

export interface ArcadeInput { left: boolean; right: boolean; fire: boolean }

function spawnWave(state: ArcadeState, wave: number): void {
  state.wave = wave;
  state.invaders = [];
  for (let row = 0; row < ROWS; row += 1) {
    for (let col = 0; col < COLS; col += 1) state.invaders.push({ col, row, alive: true });
  }
  state.swarmX = 20;
  // Each wave starts a little lower, capped so there is always room to react.
  state.swarmY = 34 + Math.min(wave - 1, 5) * 8;
  state.swarmDir = 1;
  state.shot = null;
  state.bombs = [];
  state.bombTimer = 1.5;
  state.stepTimer = 0;
}

export function newArcade(): ArcadeState {
  const state: ArcadeState = {
    phase: "ready", score: 0, lives: 3, wave: 1, shipX: ARCADE_W / 2, hitTimer: 0,
    shot: null, bombs: [], invaders: [], swarmX: 0, swarmY: 0, swarmDir: 1, bombTimer: 0, frame: 0, stepTimer: 0,
  };
  spawnWave(state, 1);
  return state;
}

/** Where an invader's top-left corner currently is. */
export function invaderPos(state: ArcadeState, inv: Invader): { x: number; y: number } {
  return { x: state.swarmX + inv.col * CELL_X, y: state.swarmY + inv.row * CELL_Y };
}

function aliveCount(state: ArcadeState): number {
  let n = 0;
  for (const inv of state.invaders) if (inv.alive) n += 1;
  return n;
}

/** Seconds between swarm steps: the fewer left, the faster they march. */
export function swarmInterval(state: ArcadeState): number {
  const left = aliveCount(state) / (ROWS * COLS);
  return Math.max(0.05, (0.08 + 0.55 * left) * Math.pow(0.9, state.wave - 1));
}

/** Advance the game by `dt` seconds. `random` returns [0, 1) and is injectable for tests. */
export function stepArcade(state: ArcadeState, input: ArcadeInput, dt: number, random: () => number = Math.random): void {
  if (state.phase !== "playing") return;

  if (state.hitTimer > 0) {
    state.hitTimer = Math.max(0, state.hitTimer - dt);
    return;
  }

  // Ship and shot.
  const dir = (input.right ? 1 : 0) - (input.left ? 1 : 0);
  state.shipX = Math.min(ARCADE_W - 10, Math.max(10, state.shipX + dir * SHIP_SPEED * dt));
  if (input.fire && !state.shot) state.shot = { x: state.shipX, y: SHIP_Y - 6 };
  if (state.shot) {
    state.shot.y -= SHOT_SPEED * dt;
    if (state.shot.y < 12) state.shot = null;
  }

  // Swarm march: a sideways step, or down and turn at an edge.
  state.stepTimer += dt;
  const interval = swarmInterval(state);
  while (state.stepTimer >= interval) {
    state.stepTimer -= interval;
    state.frame = state.frame === 0 ? 1 : 0;
    let minX = Infinity, maxX = -Infinity;
    for (const inv of state.invaders) {
      if (!inv.alive) continue;
      const { x } = invaderPos(state, inv);
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x + INVADER_W);
    }
    const next = state.swarmDir * 3;
    if (maxX + next > ARCADE_W - 4 || minX + next < 4) {
      state.swarmY += 8;
      state.swarmDir = state.swarmDir === 1 ? -1 : 1;
    } else {
      state.swarmX += next;
    }
  }

  // Shot hits an invader.
  if (state.shot) {
    for (const inv of state.invaders) {
      if (!inv.alive) continue;
      const { x, y } = invaderPos(state, inv);
      if (state.shot.x >= x - 1 && state.shot.x <= x + INVADER_W + 1 && state.shot.y >= y && state.shot.y <= y + INVADER_H) {
        inv.alive = false;
        state.score += ROW_POINTS[inv.row];
        state.shot = null;
        break;
      }
    }
  }

  // Bombs drop from a random column's lowest invader.
  state.bombTimer -= dt;
  if (state.bombTimer <= 0) {
    const lowest = new Map<number, Invader>();
    for (const inv of state.invaders) {
      if (inv.alive && (lowest.get(inv.col)?.row ?? -1) < inv.row) lowest.set(inv.col, inv);
    }
    const shooters = [...lowest.values()];
    if (shooters.length > 0) {
      const inv = shooters[Math.floor(random() * shooters.length)];
      const { x, y } = invaderPos(state, inv);
      state.bombs.push({ x: x + INVADER_W / 2, y: y + INVADER_H });
    }
    state.bombTimer = Math.max(0.35, 1.3 - state.wave * 0.12) * (0.6 + random() * 0.8);
  }
  for (const bomb of state.bombs) bomb.y += BOMB_SPEED * dt;
  state.bombs = state.bombs.filter((b) => b.y < ARCADE_H);

  // A bomb hits the ship.
  if (state.bombs.some((b) => Math.abs(b.x - state.shipX) <= SHIP_HALF && b.y >= SHIP_Y - 4 && b.y <= SHIP_Y + 6)) {
    loseLife(state);
    return;
  }

  // The swarm reaches the ship's row: game over, whatever lives remain.
  for (const inv of state.invaders) {
    if (inv.alive && invaderPos(state, inv).y + INVADER_H >= SHIP_Y - 4) {
      state.lives = 0;
      state.phase = "over";
      return;
    }
  }

  if (aliveCount(state) === 0) spawnWave(state, state.wave + 1);
}

function loseLife(state: ArcadeState): void {
  state.lives -= 1;
  state.bombs = [];
  state.shot = null;
  if (state.lives <= 0) {
    state.lives = 0;
    state.phase = "over";
  } else {
    state.hitTimer = RESPAWN_S;
  }
}

export const ARCADE_SHIP_Y = SHIP_Y;
