/**
 * "Block Drop": the falling-block puzzle cabinet.
 *
 * Seven four-cell pieces fall into a 10 × 20 well (plus two hidden rows on
 * top where pieces appear). Pieces come from a 7-bag, so every seven in a row
 * hold each shape once and droughts cannot happen. Left / right shift once on
 * press and then auto-repeat after a short delay (DAS / ARR), `up` or `a`
 * rotates clockwise with a few simple wall kicks, `down` soft-drops and `b`
 * hard-drops. A grounded piece locks after half a second; moving or rotating
 * it buys time, but only a limited number of times, so it cannot stall
 * forever. Full rows clear after a short animation and score by how many went
 * at once, times the level; every ten lines the level and the gravity rise.
 */
import { type RetroDrawInfo, type RetroGame, type RetroInput, type RetroStatus } from "../retroGame";
import { drawDigits, drawSparks, emitSpark, sparkPool, stepSparks, type Spark } from "./games2Kit";

export const COLS = 10;
/** 20 visible rows plus 2 hidden ones on top where pieces spawn. */
export const ROWS = 22;
export const HIDDEN = 2;

/** Delayed auto shift: hold this long before a held direction starts repeating... */
export const DAS = 0.17;
/** ...then move one column this often. */
export const ARR = 0.05;
export const LOCK_DELAY = 0.5;
/** How often moving or rotating a grounded piece may restart its lock timer. */
export const MAX_LOCK_RESETS = 15;
export const CLEAR_TIME = 0.3;
const SOFT_DROP_INTERVAL = 0.035;
/** Points for 1, 2, 3 or 4 lines at once, times the level. */
export const LINE_SCORES: readonly number[] = [0, 100, 300, 500, 800];

/** Piece kinds, in this order: I O T S Z J L. Cells store kind + 1 (0 is empty). */
export const PIECE_I = 0, PIECE_O = 1, PIECE_T = 2, PIECE_S = 3, PIECE_Z = 4, PIECE_J = 5, PIECE_L = 6;
export const PIECE_COUNT = 7;

/** Spawn-orientation cells per kind as x, y pairs inside its bounding box (y down). */
const BASE_CELLS: readonly (readonly number[])[] = [
  [0, 1, 1, 1, 2, 1, 3, 1], // I
  [0, 0, 1, 0, 0, 1, 1, 1], // O
  [1, 0, 0, 1, 1, 1, 2, 1], // T
  [1, 0, 2, 0, 0, 1, 1, 1], // S
  [0, 0, 1, 0, 1, 1, 2, 1], // Z
  [0, 0, 0, 1, 1, 1, 2, 1], // J
  [2, 0, 0, 1, 1, 1, 2, 1], // L
];
const BOX = [4, 2, 3, 3, 3, 3, 3];

/** SHAPES[kind][rotation] = 8 numbers (four x, y pairs), rotated clockwise inside the box. */
export const SHAPES: readonly (readonly Int8Array[])[] = BASE_CELLS.map((base, kind) => {
  const n = BOX[kind];
  const states: Int8Array[] = [Int8Array.from(base)];
  for (let r = 1; r < 4; r += 1) {
    const prev = states[r - 1];
    const next = new Int8Array(8);
    for (let i = 0; i < 4; i += 1) {
      // Clockwise in a y-down box: (x, y) -> (n - 1 - y, x).
      next[i * 2] = kind === PIECE_O ? prev[i * 2] : n - 1 - prev[i * 2 + 1];
      next[i * 2 + 1] = kind === PIECE_O ? prev[i * 2 + 1] : prev[i * 2];
    }
    states.push(next);
  }
  return states;
});

/** Offsets tried in order when a rotation does not fit in place (y < 0 lifts the piece). */
const KICKS: readonly number[] = [0, 0, -1, 0, 1, 0, 0, -1, -1, -1, 1, -1];
const KICKS_I: readonly number[] = [0, 0, -1, 0, 1, 0, -2, 0, 2, 0, 0, -1, 0, -2];

export interface BlockDropState {
  /** COLS × ROWS, row-major from the top; 0 empty, otherwise piece kind + 1. */
  cells: Uint8Array;
  /** A piece is falling (false while rows clear or after topping out). */
  active: boolean;
  piece: number;
  rot: number;
  px: number;
  py: number;
  /** Upcoming kinds; refilled a whole shuffled bag at a time, never shorter than 7. */
  queue: number[];
  /** Scratch for shuffling the next bag. */
  bag: number[];
  gravityTimer: number;
  lockTimer: number;
  lockResets: number;
  /** The lowest row this piece has reached; reaching a new one restores its lock resets. */
  lowestRow: number;
  shiftDir: number;
  shiftTimer: number;
  /** Seconds left of the line-clear animation; the rows in `clearRows` vanish when it ends. */
  clearing: number;
  clearRows: number[];
  score: number;
  lines: number;
  level: number;
  over: boolean;
  /** Steps run, so the attract screen knows a run is still untouched. */
  ticks: number;
  sparks: Spark[];
  /** Draw-only juice: the hard-drop kick, the "+points" popup, the level-up glow. */
  kick: number;
  popupValue: number;
  popupRow: number;
  popupTime: number;
  levelGlow: number;
}

export const WIDTH = 200;
export const HEIGHT = 236;
const CELL = 11;
const WELL_X = 8;
const WELL_Y = 8;
const PANEL_X = 128;
const PANEL_W = 64;

const COLORS = ["#4fd8ff", "#ffd84a", "#b06bff", "#5dea6a", "#ff5a6e", "#4a7dff", "#ff9a3d"];
const STACK_GREY = "#5b6177";

export function gravityInterval(level: number): number {
  const l = Math.min(20, Math.max(1, level));
  return Math.pow(0.8 - (l - 1) * 0.007, l - 1);
}

export function fits(s: BlockDropState, kind: number, rot: number, x: number, y: number): boolean {
  const shape = SHAPES[kind][rot];
  for (let i = 0; i < 4; i += 1) {
    const cx = x + shape[i * 2];
    const cy = y + shape[i * 2 + 1];
    if (cx < 0 || cx >= COLS || cy < 0 || cy >= ROWS) return false;
    if (s.cells[cy * COLS + cx] !== 0) return false;
  }
  return true;
}

function refill(s: BlockDropState, random: () => number): void {
  while (s.queue.length < PIECE_COUNT) {
    const bag = s.bag;
    for (let i = 0; i < PIECE_COUNT; i += 1) bag[i] = i;
    for (let i = PIECE_COUNT - 1; i > 0; i -= 1) {
      const j = Math.floor(random() * (i + 1));
      const t = bag[i]; bag[i] = bag[j]; bag[j] = t;
    }
    for (let i = 0; i < PIECE_COUNT; i += 1) s.queue.push(bag[i]);
  }
}

/** Take the next piece from the queue; topping out (no room to appear) ends the run. */
export function spawnPiece(s: BlockDropState, random: () => number): void {
  const kind = s.queue.shift() ?? PIECE_T;
  refill(s, random);
  s.piece = kind;
  s.rot = 0;
  s.px = kind === PIECE_O ? 4 : 3;
  s.py = 1;
  s.gravityTimer = 0;
  s.lockTimer = 0;
  s.lockResets = 0;
  s.lowestRow = s.py;
  if (!fits(s, kind, 0, s.px, s.py)) {
    s.active = false;
    s.over = true;
    return;
  }
  s.active = true;
}

export function createBlockDrop(random: () => number): BlockDropState {
  const s: BlockDropState = {
    cells: new Uint8Array(COLS * ROWS), active: false, piece: 0, rot: 0, px: 0, py: 0,
    queue: [], bag: [0, 0, 0, 0, 0, 0, 0],
    gravityTimer: 0, lockTimer: 0, lockResets: 0, lowestRow: 0, shiftDir: 0, shiftTimer: 0,
    clearing: 0, clearRows: [], score: 0, lines: 0, level: 1, over: false, ticks: 0,
    sparks: sparkPool(90), kick: 0, popupValue: 0, popupRow: 0, popupTime: 0, levelGlow: 0,
  };
  refill(s, random);
  spawnPiece(s, random);
  return s;
}

function grounded(s: BlockDropState): boolean {
  return !fits(s, s.piece, s.rot, s.px, s.py + 1);
}

/** A successful move or turn of a grounded piece restarts its lock timer, a limited number of times. */
function touchLock(s: BlockDropState, wasGrounded: boolean): void {
  if ((wasGrounded || grounded(s)) && s.lockResets < MAX_LOCK_RESETS) {
    s.lockTimer = 0;
    s.lockResets += 1;
  }
}

export function tryShift(s: BlockDropState, dx: number): boolean {
  if (!fits(s, s.piece, s.rot, s.px + dx, s.py)) return false;
  const was = grounded(s);
  s.px += dx;
  touchLock(s, was);
  return true;
}

/** Rotate clockwise, trying the kick offsets in order. */
export function tryRotate(s: BlockDropState): boolean {
  if (s.piece === PIECE_O) return true;
  const next = (s.rot + 1) % 4;
  const kicks = s.piece === PIECE_I ? KICKS_I : KICKS;
  const was = grounded(s);
  for (let i = 0; i < kicks.length; i += 2) {
    const x = s.px + kicks[i];
    const y = s.py + kicks[i + 1];
    if (fits(s, s.piece, next, x, y)) {
      s.rot = next; s.px = x; s.py = y;
      touchLock(s, was);
      return true;
    }
  }
  return false;
}

/** How many rows the current piece can still fall (also places the ghost). */
export function dropDistance(s: BlockDropState): number {
  let d = 0;
  while (fits(s, s.piece, s.rot, s.px, s.py + d + 1)) d += 1;
  return d;
}

function lockPiece(s: BlockDropState, random: () => number): void {
  const shape = SHAPES[s.piece][s.rot];
  let allHidden = true;
  for (let i = 0; i < 4; i += 1) {
    const cy = s.py + shape[i * 2 + 1];
    s.cells[cy * COLS + s.px + shape[i * 2]] = s.piece + 1;
    if (cy >= HIDDEN) allHidden = false;
  }
  s.active = false;
  // Locking entirely above the visible well is a top-out too.
  if (allHidden) { s.over = true; return; }
  s.clearRows.length = 0;
  for (let y = 0; y < ROWS; y += 1) {
    let full = true;
    for (let x = 0; x < COLS; x += 1) if (s.cells[y * COLS + x] === 0) { full = false; break; }
    if (full) s.clearRows.push(y);
  }
  const n = s.clearRows.length;
  if (n === 0) { spawnPiece(s, random); return; }
  const points = LINE_SCORES[n] * s.level;
  s.score += points;
  s.lines += n;
  const level = Math.max(s.level, 1 + Math.floor(s.lines / 10));
  if (level > s.level) s.levelGlow = 1;
  s.level = level;
  s.clearing = CLEAR_TIME;
  s.popupValue = points;
  s.popupRow = s.clearRows[0];
  s.popupTime = 1.1;
  for (let r = 0; r < n; r += 1) {
    const py = WELL_Y + (s.clearRows[r] - HIDDEN) * CELL + CELL / 2;
    for (let i = 0; i < 12; i += 1) {
      const px = WELL_X + random() * COLS * CELL;
      emitSpark(s.sparks, px, py, (random() - 0.5) * 90, -30 - random() * 90, 0.4 + random() * 0.4, n === 4 ? 3 : 2,
        n === 4 ? COLORS[Math.floor(random() * PIECE_COUNT)] : "#ffffff");
    }
  }
}

/** Remove the cleared rows and let everything above fall into place. */
function collapseRows(s: BlockDropState): void {
  let write = ROWS - 1;
  for (let read = ROWS - 1; read >= 0; read -= 1) {
    if (s.clearRows.includes(read)) continue;
    if (write !== read) s.cells.copyWithin(write * COLS, read * COLS, read * COLS + COLS);
    write -= 1;
  }
  for (; write >= 0; write -= 1) s.cells.fill(0, write * COLS, write * COLS + COLS);
  s.clearRows.length = 0;
}

function handleShift(s: BlockDropState, input: RetroInput, dt: number): void {
  if (input.pressed.left) {
    s.shiftDir = -1; s.shiftTimer = 0; tryShift(s, -1);
  } else if (input.pressed.right) {
    s.shiftDir = 1; s.shiftTimer = 0; tryShift(s, 1);
  } else if ((s.shiftDir === -1 && !input.left) || (s.shiftDir === 1 && !input.right)) {
    // Let go: fall back to the other direction if it is still held, without an instant move.
    s.shiftDir = s.shiftDir === -1 ? (input.right ? 1 : 0) : (input.left ? -1 : 0);
    s.shiftTimer = 0;
  } else if (s.shiftDir !== 0) {
    s.shiftTimer += dt;
    while (s.shiftTimer >= DAS) {
      if (!tryShift(s, s.shiftDir)) { s.shiftTimer = DAS; break; }
      s.shiftTimer -= ARR;
    }
  }
}

export function stepBlockDrop(s: BlockDropState, input: RetroInput, dt: number, random: () => number): void {
  s.ticks += 1;
  stepSparks(s.sparks, dt, 160);
  s.kick = Math.max(0, s.kick - dt);
  s.popupTime = Math.max(0, s.popupTime - dt);
  s.levelGlow = Math.max(0, s.levelGlow - dt * 0.8);
  if (s.over) return;
  if (s.clearing > 0) {
    s.clearing -= dt;
    if (s.clearing <= 0) {
      s.clearing = 0;
      collapseRows(s);
      spawnPiece(s, random);
    }
    return;
  }
  if (!s.active) { spawnPiece(s, random); if (!s.active) return; }

  if (input.pressed.up || input.pressed.a) tryRotate(s);
  handleShift(s, input, dt);

  if (input.pressed.b) {
    const d = dropDistance(s);
    s.py += d;
    s.score += d * 2;
    s.kick = 0.12;
    lockPiece(s, random);
    return;
  }

  const soft = input.down;
  const interval = soft ? Math.min(gravityInterval(s.level), SOFT_DROP_INTERVAL) : gravityInterval(s.level);
  s.gravityTimer += dt;
  while (s.gravityTimer >= interval) {
    if (!fits(s, s.piece, s.rot, s.px, s.py + 1)) { s.gravityTimer = 0; break; }
    s.gravityTimer -= interval;
    s.py += 1;
    if (soft) s.score += 1;
    if (s.py > s.lowestRow) { s.lowestRow = s.py; s.lockResets = 0; s.lockTimer = 0; }
  }

  if (grounded(s)) {
    s.lockTimer += dt;
    if (s.lockTimer >= LOCK_DELAY - 1e-9) lockPiece(s, random);
  }
}

// ---------------------------------------------------------------- drawing

function drawCell(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, color: string): void {
  ctx.fillStyle = color;
  ctx.fillRect(x, y, size, size);
  ctx.fillStyle = "rgba(255,255,255,0.38)";
  ctx.fillRect(x, y, size, 1);
  ctx.fillRect(x, y, 1, size);
  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.fillRect(x, y + size - 1, size, 1);
  ctx.fillRect(x + size - 1, y, 1, size);
  if (size >= 8) {
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.fillRect(x + 2, y + 2, 2, 2);
  }
}

/** A piece centred in a box, for the preview panel. */
function drawPreview(ctx: CanvasRenderingContext2D, kind: number, cx: number, cy: number, size: number): void {
  const shape = SHAPES[kind][0];
  let minX = 9, maxX = -9, minY = 9, maxY = -9;
  for (let i = 0; i < 4; i += 1) {
    minX = Math.min(minX, shape[i * 2]); maxX = Math.max(maxX, shape[i * 2]);
    minY = Math.min(minY, shape[i * 2 + 1]); maxY = Math.max(maxY, shape[i * 2 + 1]);
  }
  const ox = Math.round(cx - ((maxX - minX + 1) * size) / 2);
  const oy = Math.round(cy - ((maxY - minY + 1) * size) / 2);
  for (let i = 0; i < 4; i += 1) {
    drawCell(ctx, ox + (shape[i * 2] - minX) * size, oy + (shape[i * 2 + 1] - minY) * size, size - 1, COLORS[kind]);
  }
}

function panelBox(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number): void {
  ctx.fillStyle = "#0b1433";
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = "#2a4a8f";
  ctx.lineWidth = 1;
  ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
}

/** A decorative half-built stack for the title screen (letters are piece kinds). */
const ATTRACT_ROWS = [
  "J.........",
  "JJJ....LOO",
  "ZZTT.LLLOO",
  "SZZT.ITTTS",
  "SSZT.IJTSS",
  "OOTT.IJJJS",
];
const KIND_OF: Record<string, number> = { I: 0, O: 1, T: 2, S: 3, Z: 4, J: 5, L: 6 };

function drawWell(ctx: CanvasRenderingContext2D, s: BlockDropState, info: RetroDrawInfo): void {
  const wellW = COLS * CELL, wellH = (ROWS - HIDDEN) * CELL;
  ctx.fillStyle = "#070c1f";
  ctx.fillRect(WELL_X, WELL_Y, wellW, wellH);
  ctx.fillStyle = "rgba(124,196,255,0.06)";
  for (let x = 1; x < COLS; x += 1) ctx.fillRect(WELL_X + x * CELL, WELL_Y, 1, wellH);
  for (let y = 1; y < ROWS - HIDDEN; y += 1) ctx.fillRect(WELL_X, WELL_Y + y * CELL, wellW, 1);
  // The frame glows up for a moment on a level-up, fading smoothly (no blinking).
  ctx.strokeStyle = s.levelGlow > 0 ? `rgba(124,196,255,${0.45 + s.levelGlow * 0.55})` : "rgba(63,162,255,0.45)";
  ctx.lineWidth = s.levelGlow > 0 ? 2 : 1;
  ctx.strokeRect(WELL_X - 1.5, WELL_Y - 1.5, wellW + 3, wellH + 3);

  const attract = info.idle && s.ticks === 0;
  if (attract) {
    const top = ROWS - ATTRACT_ROWS.length;
    for (let r = 0; r < ATTRACT_ROWS.length; r += 1) {
      for (let x = 0; x < COLS; x += 1) {
        const ch = ATTRACT_ROWS[r][x];
        if (ch === ".") continue;
        drawCell(ctx, WELL_X + x * CELL, WELL_Y + (top + r - HIDDEN) * CELL, CELL - 1, COLORS[KIND_OF[ch]]);
      }
    }
    // An upright I hovering over the gap it is about to fill.
    const bob = info.reduced ? 0 : Math.round(Math.sin(info.time * 2) * 3);
    for (let i = 0; i < 4; i += 1) drawCell(ctx, WELL_X + 4 * CELL, WELL_Y + (7 + i) * CELL + bob, CELL - 1, COLORS[PIECE_I]);
    ctx.strokeStyle = "rgba(79,216,255,0.35)";
    for (let i = 0; i < 4; i += 1) ctx.strokeRect(WELL_X + 4 * CELL + 0.5, WELL_Y + (top + 2 + i - HIDDEN) * CELL + 0.5, CELL - 2, CELL - 2);
    return;
  }

  const p = s.clearing > 0 ? 1 - s.clearing / CLEAR_TIME : 0;
  for (let y = HIDDEN; y < ROWS; y += 1) {
    const clearingRow = s.clearing > 0 && s.clearRows.includes(y);
    for (let x = 0; x < COLS; x += 1) {
      const v = s.cells[y * COLS + x];
      if (v === 0) continue;
      const px = WELL_X + x * CELL, py = WELL_Y + (y - HIDDEN) * CELL;
      if (clearingRow) {
        if (info.reduced) {
          // Reduced motion: the row simply fades away.
          ctx.globalAlpha = 1 - p;
          drawCell(ctx, px, py, CELL - 1, COLORS[v - 1]);
          ctx.globalAlpha = 1;
        } else {
          // Squeeze into a white line that wipes out from the middle.
          const h = Math.max(1, Math.round((CELL - 1) * (1 - p)));
          const reach = p * (COLS / 2 + 1);
          ctx.fillStyle = Math.abs(x + 0.5 - COLS / 2) < reach ? "#ffffff" : COLORS[v - 1];
          ctx.fillRect(px, py + Math.round((CELL - 1 - h) / 2), CELL - 1, h);
        }
        continue;
      }
      drawCell(ctx, px, py, CELL - 1, s.over ? STACK_GREY : COLORS[v - 1]);
    }
  }

  if (s.active && !s.over) {
    const shape = SHAPES[s.piece][s.rot];
    const ghost = s.py + dropDistance(s);
    ctx.strokeStyle = COLORS[s.piece];
    ctx.globalAlpha = 0.45;
    ctx.lineWidth = 1;
    for (let i = 0; i < 4; i += 1) {
      const gy = ghost + shape[i * 2 + 1];
      if (gy < HIDDEN) continue;
      ctx.strokeRect(WELL_X + (s.px + shape[i * 2]) * CELL + 0.5, WELL_Y + (gy - HIDDEN) * CELL + 0.5, CELL - 2, CELL - 2);
    }
    ctx.globalAlpha = 1;
    // A piece about to lock dims a little, so the lock delay can be read.
    const settle = grounded(s) ? Math.min(1, s.lockTimer / LOCK_DELAY) : 0;
    for (let i = 0; i < 4; i += 1) {
      const cy = s.py + shape[i * 2 + 1];
      if (cy < HIDDEN) continue;
      drawCell(ctx, WELL_X + (s.px + shape[i * 2]) * CELL, WELL_Y + (cy - HIDDEN) * CELL, CELL - 1, COLORS[s.piece]);
      if (settle > 0) {
        ctx.fillStyle = `rgba(7,12,31,${settle * 0.35})`;
        ctx.fillRect(WELL_X + (s.px + shape[i * 2]) * CELL, WELL_Y + (cy - HIDDEN) * CELL, CELL - 1, CELL - 1);
      }
    }
  }

  if (s.over) {
    ctx.fillStyle = "rgba(7,12,31,0.45)";
    ctx.fillRect(WELL_X, WELL_Y, wellW, wellH);
  }
}

function drawPanel(ctx: CanvasRenderingContext2D, s: BlockDropState, info: RetroDrawInfo): void {
  const attract = info.idle && s.ticks === 0;
  panelBox(ctx, PANEL_X, WELL_Y, PANEL_W, 46);
  // A small down-arrow marks the "next" box without words.
  ctx.fillStyle = "#7cc4ff";
  ctx.beginPath();
  ctx.moveTo(PANEL_X + 4, WELL_Y + 4); ctx.lineTo(PANEL_X + 10, WELL_Y + 4); ctx.lineTo(PANEL_X + 7, WELL_Y + 8);
  ctx.closePath();
  ctx.fill();
  drawPreview(ctx, attract ? PIECE_T : s.queue[0], PANEL_X + PANEL_W / 2, WELL_Y + 25, 10);
  panelBox(ctx, PANEL_X, WELL_Y + 50, 31, 30);
  panelBox(ctx, PANEL_X + 33, WELL_Y + 50, 31, 30);
  drawPreview(ctx, attract ? PIECE_L : s.queue[1], PANEL_X + 15.5, WELL_Y + 65, 6);
  drawPreview(ctx, attract ? PIECE_S : s.queue[2], PANEL_X + 48.5, WELL_Y + 65, 6);

  panelBox(ctx, PANEL_X, WELL_Y + 88, PANEL_W, 26);
  drawDigits(ctx, s.score, PANEL_X + PANEL_W - 5, WELL_Y + 94, 14, "#ffffff", "right", 600);

  // Level: a double chevron; lines: three stacked bars.
  panelBox(ctx, PANEL_X, WELL_Y + 120, PANEL_W, 20);
  ctx.strokeStyle = "#ffd84a";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(PANEL_X + 5, WELL_Y + 131); ctx.lineTo(PANEL_X + 9, WELL_Y + 127); ctx.lineTo(PANEL_X + 13, WELL_Y + 131);
  ctx.moveTo(PANEL_X + 5, WELL_Y + 135); ctx.lineTo(PANEL_X + 9, WELL_Y + 131); ctx.lineTo(PANEL_X + 13, WELL_Y + 135);
  ctx.stroke();
  drawDigits(ctx, s.level, PANEL_X + PANEL_W - 5, WELL_Y + 124, 12, "#ffd84a", "right");

  panelBox(ctx, PANEL_X, WELL_Y + 144, PANEL_W, 20);
  ctx.fillStyle = "#5dea6a";
  for (let i = 0; i < 3; i += 1) ctx.fillRect(PANEL_X + 5, WELL_Y + 149 + i * 4, 9, 2);
  drawDigits(ctx, s.lines, PANEL_X + PANEL_W - 5, WELL_Y + 148, 12, "#5dea6a", "right");

  // Progress to the next level as a thin bar.
  panelBox(ctx, PANEL_X, WELL_Y + 170, PANEL_W, 8);
  ctx.fillStyle = "#3fa2ff";
  ctx.fillRect(PANEL_X + 2, WELL_Y + 172, Math.round((PANEL_W - 4) * ((s.lines % 10) / 10)), 4);

  // A little decorative tower of cells at the bottom of the panel.
  for (let i = 0; i < 4; i += 1) {
    drawCell(ctx, PANEL_X + 8 + i * 13, WELL_Y + 200, 11, COLORS[(i + 2) % PIECE_COUNT]);
    if (i % 2 === 0) drawCell(ctx, PANEL_X + 8 + i * 13, WELL_Y + 188, 11, COLORS[(i + 5) % PIECE_COUNT]);
  }
}

export function drawBlockDrop(ctx: CanvasRenderingContext2D, s: BlockDropState, info: RetroDrawInfo): void {
  ctx.fillStyle = "#050916";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  ctx.save();
  if (!info.reduced && s.kick > 0) ctx.translate(0, Math.round((s.kick / 0.12) * 2));
  drawWell(ctx, s, info);
  drawSparks(ctx, s.sparks);
  if (s.popupTime > 0 && s.popupValue > 0 && !s.over) {
    const rise = info.reduced ? 0 : (1.1 - s.popupTime) * 14;
    ctx.globalAlpha = Math.min(1, s.popupTime * 2);
    drawDigits(ctx, `+${s.popupValue}`, WELL_X + (COLS * CELL) / 2, WELL_Y + (s.popupRow - HIDDEN) * CELL - 10 - rise, 12, "#ffffff", "center", 600);
    ctx.globalAlpha = 1;
  }
  ctx.restore();
  drawPanel(ctx, s, info);
}

const blockDrop: RetroGame<BlockDropState> = {
  id: "block-drop",
  width: WIDTH,
  height: HEIGHT,
  create: (random) => createBlockDrop(random),
  step: (state, input, dt, random) => stepBlockDrop(state, input, dt, random),
  draw: (ctx, state, info) => drawBlockDrop(ctx, state, info),
  status: (state): RetroStatus => ({ score: state.score, level: state.level, over: state.over }),
};

export default blockDrop;

