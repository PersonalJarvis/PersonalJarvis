/**
 * "Desert Dash": the endless-runner cabinet.
 *
 * A little fennec fox runs right across the dunes; cacti and, further in,
 * birds come at it ever faster. `a` / `up` jumps (hold for a full jump, let
 * go early for a short hop), `down` ducks on the ground and dives in the
 * air. One hit ends the run; the score is the distance run.
 *
 * Fairness is built in, not hoped for: the full jump is simulated once with
 * the real step, which gives exactly how long the fox stays above any height.
 * The spawner only picks an obstacle the fox can clear at the current speed,
 * and leaves at least a whole jump plus a reaction time of ground between
 * two obstacles, so every run is always survivable. High birds fly above a
 * ducking fox.
 *
 * The sky drifts from day through dusk and night to dawn and back, blended
 * smoothly; three parallax layers of dunes and a textured ground scroll at
 * their own speeds.
 */
import { RETRO_STEP, type RetroDrawInfo, type RetroGame, type RetroInput, type RetroStatus } from "../retroGame";
import { drawDigits, drawSparks, emitSpark, hexRgb, mixRgb, sparkPool, stepSparks, type Rgb, type Spark } from "./games2Kit";

export const WIDTH = 320;
export const HEIGHT = 180;
/** Screen y of the ground line. Heights in the game are pixels above it (up is positive). */
const GROUND_Y = 150;
/** The fox's left edge on screen; the world scrolls past it. */
export const RUNNER_X = 36;

export const JUMP_SPEED = 400;
/** Gravity while rising with the jump button held, and otherwise (falling, or let go). */
export const GRAVITY_HELD = 1150;
export const GRAVITY = 2300;
/** Letting go while rising cuts the upward speed to this: a short hop. */
export const JUMP_CUT = 220;
const DIVE_GRAVITY = 5200;
/** A jump pressed this shortly before landing still happens on landing. */
const JUMP_BUFFER = 0.1;

export const START_SPEED = 160;
export const MAX_SPEED = 440;
/** Pixels per second gained every second. */
export const SPEED_RAMP = 3.2;
/** Score is distance in tens of pixels. */
const SCORE_SCALE = 10;
/** Seconds of reaction time left between landing from one obstacle and having to jump the next. */
export const REACTION = 0.18;
/** One day-dusk-night-dawn cycle, in seconds of running. */
const DAY_CYCLE = 150;

/** The fox's hitboxes, relative to RUNNER_X and the ground, a little smaller than the drawing. */
export const STAND_HIT = { x0: 5, x1: 15, h: 17 };
export const DUCK_HIT = { x0: 3, x1: 20, h: 9 };

export interface ObstacleKind {
  /** Drawn width in pixels. */
  w: number;
  /** Hitbox: left / right inset edges, and bottom / top heights above the ground. */
  hx0: number; hx1: number; bottom: number; top: number;
  bird: boolean;
  /** Passed by ducking instead of jumping. */
  duck: boolean;
  /** First appears at this score / speed. */
  minScore: number;
  minSpeed: number;
  /** Relative chance among the kinds that are allowed. */
  weight: number;
}

export const KINDS: readonly ObstacleKind[] = [
  { w: 9, hx0: 1, hx1: 8, bottom: 0, top: 17, bird: false, duck: false, minScore: 0, minSpeed: 0, weight: 4 }, // small cactus
  { w: 11, hx0: 1, hx1: 10, bottom: 0, top: 27, bird: false, duck: false, minScore: 0, minSpeed: 0, weight: 3 }, // tall cactus
  { w: 20, hx0: 1, hx1: 19, bottom: 0, top: 17, bird: false, duck: false, minScore: 60, minSpeed: 0, weight: 2 }, // pair
  { w: 22, hx0: 1, hx1: 21, bottom: 0, top: 27, bird: false, duck: false, minScore: 200, minSpeed: 220, weight: 2 }, // tall + small
  { w: 31, hx0: 1, hx1: 30, bottom: 0, top: 17, bird: false, duck: false, minScore: 400, minSpeed: 260, weight: 1 }, // triple
  { w: 16, hx0: 2, hx1: 14, bottom: 3, top: 11, bird: true, duck: false, minScore: 300, minSpeed: 0, weight: 2 }, // low bird: jump
  { w: 16, hx0: 2, hx1: 14, bottom: 12, top: 20, bird: true, duck: true, minScore: 300, minSpeed: 0, weight: 2 }, // high bird: duck
];

export interface Obstacle { active: boolean; kind: number; x: number }

export interface DesertState {
  /** The fox's height above the ground and vertical speed (up positive). */
  y: number;
  vy: number;
  onGround: boolean;
  ducking: boolean;
  /** The jump button has been held since take-off (letting go cuts the jump once). */
  jumpHeld: boolean;
  jumpBuffer: number;
  speed: number;
  distance: number;
  /** Seconds of running; drives the speed ramp and the sky. */
  time: number;
  score: number;
  over: boolean;
  obstacles: Obstacle[];
  /** Pixels of ground still to scroll before the next obstacle appears. */
  nextGap: number;
  /** Score of the last milestone reached (every 500) and the seconds its glow has left. */
  milestone: number;
  milestoneGlow: number;
  dustTimer: number;
  sparks: Spark[];
  /** Star positions as x, y pairs (picked once per run). */
  stars: Float32Array;
  ticks: number;
}

/**
 * The full held jump, simulated once at the real step: heights per step from
 * take-off to landing. Everything about fairness is read off this.
 */
const JUMP_PROFILE: readonly number[] = (() => {
  const heights: number[] = [];
  let y = 0, vy = JUMP_SPEED;
  for (let i = 0; i < 400; i += 1) {
    vy -= (vy > 0 ? GRAVITY_HELD : GRAVITY) * RETRO_STEP;
    y += vy * RETRO_STEP;
    if (y <= 0) break;
    heights.push(y);
  }
  return heights;
})();

/** Seconds a full jump spends in the air. */
export const AIRTIME = (JUMP_PROFILE.length + 1) * RETRO_STEP;
export const JUMP_APEX = Math.max(...JUMP_PROFILE);

/** When, after take-off, a full jump is first and last above `height` (seconds); null if it never is. */
export function jumpWindow(height: number): { rise: number; fall: number } | null {
  let first = -1, last = -1;
  for (let i = 0; i < JUMP_PROFILE.length; i += 1) {
    if (JUMP_PROFILE[i] > height) { if (first < 0) first = i; last = i; }
  }
  if (first < 0) return null;
  return { rise: (first + 1) * RETRO_STEP, fall: (last + 1) * RETRO_STEP };
}

/** Whether a full jump at this speed carries the fox's hitbox over the obstacle (with two steps to spare). */
export function clearable(kind: ObstacleKind, speed: number): boolean {
  if (kind.duck) return DUCK_HIT.h < kind.bottom;
  const win = jumpWindow(kind.top);
  if (!win) return false;
  const overlap = (kind.hx1 - kind.hx0 + STAND_HIT.x1 - STAND_HIT.x0) / speed;
  return win.fall - win.rise >= overlap + RETRO_STEP * 2;
}

/** The least ground between two obstacles at this speed: land from one, react, take off for the next. */
export function minGap(speed: number): number {
  return speed * (AIRTIME + REACTION);
}

export function speedAt(time: number): number {
  return Math.min(MAX_SPEED, START_SPEED + SPEED_RAMP * time);
}

export function createDesert(random: () => number): DesertState {
  const stars = new Float32Array(48 * 2);
  for (let i = 0; i < 48; i += 1) { stars[i * 2] = random() * WIDTH; stars[i * 2 + 1] = random() * 95; }
  const obstacles: Obstacle[] = [];
  for (let i = 0; i < 8; i += 1) obstacles.push({ active: false, kind: 0, x: 0 });
  return {
    y: 0, vy: 0, onGround: true, ducking: false, jumpHeld: false, jumpBuffer: 0,
    speed: START_SPEED, distance: 0, time: 0, score: 0, over: false,
    obstacles, nextGap: WIDTH * 0.6, milestone: 0, milestoneGlow: 0, dustTimer: 0,
    sparks: sparkPool(40), stars, ticks: 0,
  };
}

/** Pick an obstacle the fox can clear at this speed and that fits the score so far. */
export function pickKind(s: DesertState, random: () => number): number {
  let total = 0;
  for (let i = 0; i < KINDS.length; i += 1) if (allowed(KINDS[i], s)) total += KINDS[i].weight;
  let roll = random() * total;
  for (let i = 0; i < KINDS.length; i += 1) {
    if (!allowed(KINDS[i], s)) continue;
    roll -= KINDS[i].weight;
    if (roll < 0) return i;
  }
  return 0;
}

function allowed(kind: ObstacleKind, s: DesertState): boolean {
  return s.score >= kind.minScore && s.speed >= kind.minSpeed && clearable(kind, s.speed);
}

/** Spawn one obstacle just off the right edge and set the ground until the next. Returns its gap. */
export function spawnObstacle(s: DesertState, random: () => number): number {
  let slot: Obstacle | null = null;
  for (const o of s.obstacles) if (!o.active) { slot = o; break; }
  const kind = pickKind(s, random);
  const gap = minGap(s.speed) * (1 + random() * 0.9) + random() * 40;
  if (slot) {
    slot.active = true;
    slot.kind = kind;
    // Carry over the overshoot so spacing is exact whatever the step size.
    slot.x = WIDTH + 4 + Math.min(0, s.nextGap);
  }
  s.nextGap = KINDS[kind].w + gap;
  return gap;
}

function hitbox(s: DesertState): { x0: number; x1: number; h: number } {
  return s.ducking ? DUCK_HIT : STAND_HIT;
}

export function collides(s: DesertState, o: Obstacle): boolean {
  const k = KINDS[o.kind];
  const box = hitbox(s);
  const rx0 = RUNNER_X + box.x0, rx1 = RUNNER_X + box.x1;
  const ox0 = o.x + k.hx0, ox1 = o.x + k.hx1;
  return rx1 > ox0 && rx0 < ox1 && s.y + box.h > k.bottom && s.y < k.top;
}

function dust(s: DesertState, count: number, random: () => number): void {
  for (let i = 0; i < count; i += 1) {
    emitSpark(s.sparks, RUNNER_X + 6 + random() * 6, GROUND_Y - 1, -s.speed * 0.3 - random() * 30, -10 - random() * 30,
      0.25 + random() * 0.25, 2, "#e8c896");
  }
}

export function stepDesert(s: DesertState, input: RetroInput, dt: number, random: () => number): void {
  s.ticks += 1;
  stepSparks(s.sparks, dt, 90);
  if (s.over) return;
  s.time += dt;
  s.speed = speedAt(s.time);
  s.distance += s.speed * dt;
  s.score = Math.floor(s.distance / SCORE_SCALE);
  s.milestoneGlow = Math.max(0, s.milestoneGlow - dt);
  if (s.score >= s.milestone + 500) { s.milestone += 500; s.milestoneGlow = 1; }

  const jumpPressed = input.pressed.a || input.pressed.up;
  const jumpDown = input.a || input.up;
  if (jumpPressed) s.jumpBuffer = JUMP_BUFFER;
  else s.jumpBuffer = Math.max(0, s.jumpBuffer - dt);

  if (s.onGround) {
    if (s.jumpBuffer > 0) {
      s.onGround = false;
      s.ducking = false;
      s.vy = JUMP_SPEED;
      // A buffered press already released before landing gives the short hop.
      s.jumpHeld = jumpDown;
      s.jumpBuffer = 0;
    } else {
      s.ducking = input.down;
    }
  }
  if (!s.onGround) {
    if (s.jumpHeld && !jumpDown) {
      s.jumpHeld = false;
      if (s.vy > JUMP_CUT) s.vy = JUMP_CUT;
    }
    const g = input.down ? DIVE_GRAVITY : s.vy > 0 && s.jumpHeld ? GRAVITY_HELD : GRAVITY;
    s.vy -= g * dt;
    s.y += s.vy * dt;
    if (s.y <= 0) {
      s.y = 0; s.vy = 0; s.onGround = true; s.jumpHeld = false;
      s.ducking = input.down;
      dust(s, 5, random);
    }
  }

  s.dustTimer -= dt;
  if (s.onGround && s.dustTimer <= 0) { s.dustTimer = 0.09; dust(s, 1, random); }

  const shift = s.speed * dt;
  for (const o of s.obstacles) {
    if (!o.active) continue;
    o.x -= shift;
    if (o.x + KINDS[o.kind].w < -4) o.active = false;
  }
  s.nextGap -= shift;
  if (s.nextGap <= 0) spawnObstacle(s, random);

  for (const o of s.obstacles) {
    if (o.active && collides(s, o)) {
      s.over = true;
      for (let i = 0; i < 14; i += 1) {
        const a = random() * Math.PI * 2, v = 30 + random() * 70;
        emitSpark(s.sparks, RUNNER_X + 10, GROUND_Y - s.y - 10, Math.cos(a) * v, Math.sin(a) * v - 30, 0.4 + random() * 0.4, 2, "#ff9a3d");
      }
      return;
    }
  }
}

// ---------------------------------------------------------------- drawing

interface Palette { skyTop: Rgb; skyBottom: Rgb; far: Rgb; near: Rgb; ground: Rgb; detail: Rgb; cactus: Rgb }

const pal = (skyTop: string, skyBottom: string, far: string, near: string, ground: string, detail: string, cactus: string): Palette => ({
  skyTop: hexRgb(skyTop), skyBottom: hexRgb(skyBottom), far: hexRgb(far), near: hexRgb(near),
  ground: hexRgb(ground), detail: hexRgb(detail), cactus: hexRgb(cactus),
});

const DAY = pal("#6ec6ff", "#ffe9b8", "#e9b97a", "#d99a55", "#f0c27b", "#b8844a", "#2f8f4e");
const DUSK = pal("#4b3a78", "#ff8a5c", "#b8705a", "#8f4f43", "#c98a5e", "#7a4a3a", "#2a6a48");
const NIGHT = pal("#070b24", "#1d2457", "#2b2f5c", "#222650", "#3a3a66", "#262850", "#2a6a5a");
const WHITE = hexRgb("#ffffff");
const GOLD = hexRgb("#ffd23f");
const DAWN = pal("#3f6fb8", "#ffb8a0", "#c99a8a", "#a8766a", "#d8a888", "#8a6a5a", "#2a7a52");

/** Keyframes over one cycle (0..1): long day and night, short smooth dusk and dawn. */
const SKY_KEYS: readonly (readonly [number, Palette])[] = [
  [0, DAY], [0.24, DAY], [0.34, DUSK], [0.44, NIGHT], [0.68, NIGHT], [0.78, DAWN], [0.88, DAY], [1, DAY],
];

/** Where the sky is: the two keyframes around `phase` and the smoothed blend between them, plus how dark it is. */
function skyAt(phase: number): { a: Palette; b: Palette; k: number; night: number } {
  let i = 0;
  while (i < SKY_KEYS.length - 2 && phase >= SKY_KEYS[i + 1][0]) i += 1;
  const [t0, a] = SKY_KEYS[i];
  const [t1, b] = SKY_KEYS[i + 1];
  const raw = t1 > t0 ? (phase - t0) / (t1 - t0) : 0;
  const k = raw * raw * (3 - 2 * raw);
  const dark = (p: Palette) => (p === NIGHT ? 1 : p === DUSK ? 0.45 : p === DAWN ? 0.35 : 0);
  return { a, b, k, night: dark(a) + (dark(b) - dark(a)) * k };
}

function duneLayer(ctx: CanvasRenderingContext2D, scroll: number, base: number, amp: number, freq: number, color: string): void {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(0, GROUND_Y);
  for (let x = 0; x <= WIDTH; x += 8) {
    const wx = x + scroll;
    const y = base - (Math.sin(wx * freq) * amp + Math.sin(wx * freq * 2.7 + 1.3) * amp * 0.35);
    ctx.lineTo(x, y);
  }
  ctx.lineTo(WIDTH, GROUND_Y);
  ctx.closePath();
  ctx.fill();
}

function drawCactus(ctx: CanvasRenderingContext2D, x: number, w: number, h: number, color: string): void {
  const top = GROUND_Y - h;
  const stem = Math.max(3, Math.round(w * 0.4));
  const sx = Math.round(x + (w - stem) / 2);
  ctx.fillStyle = color;
  ctx.fillRect(sx, top + 1, stem, h - 1);
  ctx.fillRect(sx + 1, top, stem - 2, 1);
  // Arms: one up on each side, at different heights.
  const armY = top + Math.round(h * 0.35);
  ctx.fillRect(Math.round(x), armY + 2, sx - Math.round(x), 2);
  ctx.fillRect(Math.round(x), armY - 2, 2, 5);
  ctx.fillRect(sx + stem, armY + 5, Math.round(x + w) - sx - stem, 2);
  ctx.fillRect(Math.round(x + w) - 2, armY + 1, 2, 6);
  ctx.fillStyle = "rgba(255,255,255,0.18)";
  ctx.fillRect(sx + 1, top + 2, 1, h - 4);
}

/** Draw one obstacle kind at screen x (cactus groups are built from single cacti). */
function drawObstacle(ctx: CanvasRenderingContext2D, kindIndex: number, x: number, cactus: string, time: number, reduced: boolean): void {
  const k = KINDS[kindIndex];
  if (k.bird) {
    const y = GROUND_Y - k.top;
    const flap = reduced ? 0 : Math.floor(time * 8) % 2;
    ctx.fillStyle = "#5a3a2a";
    ctx.fillRect(Math.round(x + 4), Math.round(y + 3), 9, 4); // body
    ctx.fillRect(Math.round(x + 1), Math.round(y + 2), 4, 3); // head
    ctx.fillStyle = "#ffb000";
    ctx.fillRect(Math.round(x - 1), Math.round(y + 3), 2, 1); // beak
    ctx.fillStyle = "#5a3a2a";
    ctx.fillRect(Math.round(x + 13), Math.round(y + 4), 3, 2); // tail
    if (flap === 0) ctx.fillRect(Math.round(x + 6), Math.round(y - 2), 4, 5); // wing up
    else ctx.fillRect(Math.round(x + 6), Math.round(y + 6), 4, 4); // wing down
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(Math.round(x + 2), Math.round(y + 3), 1, 1);
    return;
  }
  switch (kindIndex) {
    case 0: drawCactus(ctx, x, 9, 18, cactus); break;
    case 1: drawCactus(ctx, x, 11, 28, cactus); break;
    case 2: drawCactus(ctx, x, 9, 18, cactus); drawCactus(ctx, x + 11, 9, 15, cactus); break;
    case 3: drawCactus(ctx, x, 11, 28, cactus); drawCactus(ctx, x + 13, 9, 18, cactus); break;
    default: drawCactus(ctx, x, 9, 16, cactus); drawCactus(ctx, x + 11, 9, 18, cactus); drawCactus(ctx, x + 22, 9, 14, cactus);
  }
}

/** The fennec fox: big ears, a bushy tail, two-frame legs; flat and stretched when ducking. */
function drawFox(ctx: CanvasRenderingContext2D, s: DesertState, legFrame: number, dead: boolean): void {
  const x = RUNNER_X;
  const fur = "#ff9a3d", cream = "#ffe6c0", dark = "#2a1a0a";
  if (s.ducking && s.onGround && !dead) {
    const top = GROUND_Y - 11;
    ctx.fillStyle = fur;
    ctx.fillRect(x - 3, top + 3, 6, 3); // tail
    ctx.fillRect(x + 2, top + 3, 14, 6); // body
    ctx.fillRect(x + 14, top + 1, 8, 6); // head
    ctx.fillRect(x + 11, top, 5, 2); // ears laid back
    ctx.fillStyle = cream;
    ctx.fillRect(x - 3, top + 3, 2, 2);
    ctx.fillRect(x + 20, top + 4, 3, 2);
    ctx.fillRect(x + 6, top + 7, 8, 2);
    ctx.fillStyle = dark;
    ctx.fillRect(x + 18, top + 3, 1, 1);
    ctx.fillRect(x + 23, top + 4, 1, 1);
    ctx.fillStyle = fur;
    ctx.fillRect(x + 4 + legFrame * 2, top + 9, 2, 2);
    ctx.fillRect(x + 12 - legFrame * 2, top + 9, 2, 2);
    return;
  }
  const top = GROUND_Y - 20 - Math.round(s.y);
  ctx.fillStyle = fur;
  ctx.fillRect(x, top + 8, 5, 4); // tail
  ctx.fillRect(x + 4, top + 8, 9, 7); // body
  ctx.fillRect(x + 8, top + 2, 8, 7); // head
  ctx.fillRect(x + 9, top - 2, 2, 4); // ears
  ctx.fillRect(x + 13, top - 2, 2, 4);
  ctx.fillStyle = cream;
  ctx.fillRect(x, top + 8, 2, 3); // tail tip
  ctx.fillRect(x + 7, top + 11, 5, 3); // belly
  ctx.fillRect(x + 14, top + 5, 3, 3); // snout
  ctx.fillRect(x + 10, top - 1, 1, 2); // inner ears
  ctx.fillRect(x + 14, top - 1, 1, 2);
  ctx.fillStyle = dark;
  ctx.fillRect(x + 17, top + 5, 1, 1); // nose
  if (dead) {
    // Crossed-out eye: a tiny x.
    ctx.fillRect(x + 11, top + 3, 1, 1); ctx.fillRect(x + 13, top + 3, 1, 1);
    ctx.fillRect(x + 12, top + 4, 1, 1);
    ctx.fillRect(x + 11, top + 5, 1, 1); ctx.fillRect(x + 13, top + 5, 1, 1);
  } else {
    ctx.fillRect(x + 12, top + 4, 1, 2);
  }
  ctx.fillStyle = fur;
  if (!s.onGround) {
    ctx.fillRect(x + 5, top + 15, 2, 3);
    ctx.fillRect(x + 10, top + 15, 2, 3);
  } else {
    ctx.fillRect(x + 5, top + 15, 2, legFrame === 0 ? 5 : 3);
    ctx.fillRect(x + 10, top + 15, 2, legFrame === 0 ? 3 : 5);
  }
}

export function drawDesert(ctx: CanvasRenderingContext2D, s: DesertState, info: RetroDrawInfo): void {
  const attract = info.idle && s.ticks === 0;
  const scroll = attract ? (info.reduced ? 0 : info.time * 24) : s.distance;
  const phase = ((s.time / DAY_CYCLE) % 1 + 1) % 1;
  const sky = skyAt(phase);
  const c = (pick: (p: Palette) => Rgb) => mixRgb(pick(sky.a), pick(sky.b), sky.k);

  const grad = ctx.createLinearGradient(0, 0, 0, GROUND_Y);
  grad.addColorStop(0, c((p) => p.skyTop));
  grad.addColorStop(1, c((p) => p.skyBottom));
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, WIDTH, HEIGHT);

  // Stars fade in with the night.
  if (sky.night > 0.05) {
    ctx.fillStyle = "#ffffff";
    for (let i = 0; i < s.stars.length; i += 2) {
      const twinkle = info.reduced ? 1 : 0.6 + 0.4 * Math.sin(info.time * 2 + i);
      ctx.globalAlpha = sky.night * twinkle * 0.9;
      const sx = ((s.stars[i] - scroll * 0.02) % WIDTH + WIDTH) % WIDTH;
      ctx.fillRect(Math.round(sx), Math.round(s.stars[i + 1]), 1, 1);
    }
    ctx.globalAlpha = 1;
  }
  // Sun by day, sinking at dusk; the moon by night.
  const sunAlpha = 1 - sky.night;
  if (sunAlpha > 0.02) {
    ctx.globalAlpha = sunAlpha;
    ctx.fillStyle = "#fff4c2";
    ctx.beginPath();
    ctx.arc(250, 36 + sky.night * 70, 14, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }
  if (sky.night > 0.02) {
    ctx.globalAlpha = sky.night;
    ctx.fillStyle = "#f2f0ff";
    ctx.beginPath();
    ctx.arc(70, 34, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = c((p) => p.skyTop);
    ctx.beginPath();
    ctx.arc(74, 31, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  duneLayer(ctx, scroll * 0.12, 112, 9, 0.011, c((p) => p.far));
  duneLayer(ctx, scroll * 0.35, 132, 7, 0.02, c((p) => p.near));

  // Ground with pebbles and ridges that scroll at full speed (placed by a fixed hash, not at random).
  ctx.fillStyle = c((p) => p.ground);
  ctx.fillRect(0, GROUND_Y, WIDTH, HEIGHT - GROUND_Y);
  const detail = c((p) => p.detail);
  ctx.fillStyle = detail;
  ctx.fillRect(0, GROUND_Y, WIDTH, 1);
  const first = Math.floor(scroll / 13);
  for (let i = first; i < first + Math.ceil(WIDTH / 13) + 2; i += 1) {
    const h = (Math.imul(i, 2654435761) >>> 0) / 4294967296;
    const px = Math.round(i * 13 - scroll + h * 9);
    const py = GROUND_Y + 3 + Math.floor(h * 24);
    ctx.fillRect(px, py, h > 0.7 ? 3 : 1, 1);
  }

  const cactus = c((p) => p.cactus);
  if (attract) {
    drawCactus(ctx, 170, 11, 28, cactus);
    drawCactus(ctx, 250, 9, 18, cactus);
  } else {
    for (const o of s.obstacles) if (o.active) drawObstacle(ctx, o.kind, o.x, cactus, info.time, info.reduced);
  }

  const legFrame = attract ? (info.reduced ? 0 : Math.floor(info.time * 8) % 2) : Math.floor(s.distance / 14) % 2;
  drawFox(ctx, s, legFrame, s.over);
  drawSparks(ctx, s.sparks);

  // Score, padded like an odometer; it warms to gold for a moment at each 500.
  const color = s.milestoneGlow > 0 ? mixRgb(WHITE, GOLD, s.milestoneGlow) : "#ffffff";
  ctx.fillStyle = "rgba(0,0,0,0.25)";
  ctx.fillRect(WIDTH - 52, 4, 48, 13);
  drawDigits(ctx, String(s.score).padStart(5, "0"), WIDTH - 7, 5, 10, color, "right", 600);
}

const desertDash: RetroGame<DesertState> = {
  id: "desert-dash",
  width: WIDTH,
  height: HEIGHT,
  create: (random) => createDesert(random),
  step: (state, input, dt, random) => stepDesert(state, input, dt, random),
  draw: (ctx, state, info) => drawDesert(ctx, state, info),
  status: (state): RetroStatus => ({ score: state.score, over: state.over }),
};

export default desertDash;
