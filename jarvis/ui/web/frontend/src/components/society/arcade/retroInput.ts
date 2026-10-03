/**
 * The cabinet's control panel: keyboard, gamepad, touch pad and pointer,
 * folded into the one RetroInput a game steps with.
 *
 * Every source keeps its own held buttons, so letting go of a key never
 * releases the same button still held on the gamepad. A press is latched as
 * an edge when a button goes from up (on all sources) to down, and
 * `takeInput` hands each edge to exactly one game step, then forgets it; a
 * tap shorter than a frame is therefore never lost and never seen twice.
 * Pure state and functions: no DOM, so all of it is unit-tested.
 */
import type { RetroButtons, RetroInput, RetroPointer } from "./retroGame";

export type RetroButton = keyof RetroButtons;

export const RETRO_BUTTONS: readonly RetroButton[] = ["left", "right", "up", "down", "a", "b"];

/** Keyboard codes (layout-independent `event.code`) of the six buttons. */
const KEY_BUTTONS: Readonly<Record<string, RetroButton>> = {
  ArrowLeft: "left", KeyA: "left",
  ArrowRight: "right", KeyD: "right",
  ArrowUp: "up", KeyW: "up",
  ArrowDown: "down", KeyS: "down",
  Space: "a", KeyJ: "a", KeyZ: "a",
  ShiftLeft: "b", ShiftRight: "b", KeyK: "b", KeyX: "b",
};

/** The button a key code drives, or null for keys the cabinet ignores. */
export function keyButton(code: string): RetroButton | null {
  return KEY_BUTTONS[code] ?? null;
}

/** The part of the Gamepad API the cabinet reads; a real `Gamepad` fits. */
export interface GamepadLike {
  axes: readonly number[];
  buttons: readonly { pressed: boolean }[];
}

/** The left stick ignores this much travel, so a worn stick does not drift. */
export const STICK_DEAD_ZONE = 0.35;

/** Standard-mapping button indices. */
const PAD_A = 0;
const PAD_B = [1, 2] as const;
const PAD_START = 9;
const PAD_UP = 12;
const PAD_DOWN = 13;
const PAD_LEFT = 14;
const PAD_RIGHT = 15;

export interface InputTracker {
  keys: RetroButtons;
  touch: RetroButtons;
  pad: RetroButtons;
  /** Rising edges latched since the last `takeInput`. */
  edges: RetroButtons;
  /** Gamepad Start is held (its edge is reported by `readGamepad`). */
  padStart: boolean;
  /** The pointer in logical pixels; `clicked` is latched like an edge. */
  pointer: RetroPointer;
  /** The pointer is over the screen. */
  pointerOn: boolean;
  /** The object `takeInput` fills and returns; reused so the loop allocates nothing. */
  input: RetroInput;
  /** The pointer copy handed to a step (reused like `input`). */
  stepPointer: RetroPointer;
}

function noButtons(): RetroButtons {
  return { left: false, right: false, up: false, down: false, a: false, b: false };
}

export function createInputTracker(): InputTracker {
  return {
    keys: noButtons(), touch: noButtons(), pad: noButtons(), edges: noButtons(),
    padStart: false,
    pointer: { x: 0, y: 0, down: false, clicked: false },
    pointerOn: false,
    input: { ...noButtons(), pressed: noButtons(), pointer: null },
    stepPointer: { x: 0, y: 0, down: false, clicked: false },
  };
}

/** The button is held on any source. */
export function isHeld(t: InputTracker, b: RetroButton): boolean {
  return t.keys[b] || t.touch[b] || t.pad[b];
}

function setHeld(t: InputTracker, source: RetroButtons, b: RetroButton, down: boolean): void {
  if (source[b] === down) return;
  const before = isHeld(t, b);
  source[b] = down;
  if (down && !before) t.edges[b] = true;
}

/**
 * A key went down. Auto-repeat never counts as a new press, but it does mark
 * the button held again (after `releaseAll` while the key stayed down).
 * Returns whether the key belongs to the cabinet.
 */
export function pressKey(t: InputTracker, code: string, repeat = false): boolean {
  const b = keyButton(code);
  if (!b) return false;
  if (repeat) t.keys[b] = true;
  else setHeld(t, t.keys, b, true);
  return true;
}

export function releaseKey(t: InputTracker, code: string): boolean {
  const b = keyButton(code);
  if (!b) return false;
  t.keys[b] = false;
  return true;
}

/** A button of the on-screen touch pad. */
export function setTouch(t: InputTracker, b: RetroButton, down: boolean): void {
  setHeld(t, t.touch, b, down);
}

function padPressed(pad: GamepadLike, index: number): boolean {
  return !!pad.buttons[index]?.pressed;
}

/**
 * Read one gamepad snapshot (or null when none is connected): d-pad and left
 * stick steer, button 0 is A, 1 or 2 is B. Returns true when Start went down
 * since the previous read; the overlay turns that into start or pause.
 */
export function readGamepad(t: InputTracker, pad: GamepadLike | null): boolean {
  if (!pad) {
    for (const b of RETRO_BUTTONS) t.pad[b] = false;
    t.padStart = false;
    return false;
  }
  const x = pad.axes[0] ?? 0;
  const y = pad.axes[1] ?? 0;
  setHeld(t, t.pad, "left", padPressed(pad, PAD_LEFT) || x < -STICK_DEAD_ZONE);
  setHeld(t, t.pad, "right", padPressed(pad, PAD_RIGHT) || x > STICK_DEAD_ZONE);
  setHeld(t, t.pad, "up", padPressed(pad, PAD_UP) || y < -STICK_DEAD_ZONE);
  setHeld(t, t.pad, "down", padPressed(pad, PAD_DOWN) || y > STICK_DEAD_ZONE);
  setHeld(t, t.pad, "a", padPressed(pad, PAD_A));
  setHeld(t, t.pad, "b", padPressed(pad, PAD_B[0]) || padPressed(pad, PAD_B[1]));
  const start = padPressed(pad, PAD_START);
  const edge = start && !t.padStart;
  t.padStart = start;
  return edge;
}

/** The first connected pad in a `navigator.getGamepads()` list. */
export function firstGamepad(pads: readonly (GamepadLike | null)[] | null | undefined): GamepadLike | null {
  if (!pads) return null;
  for (const pad of pads) if (pad && (pad as { connected?: boolean }).connected !== false) return pad;
  return null;
}

/** The pointer moved or its button changed, in logical canvas pixels. */
export function setPointer(t: InputTracker, x: number, y: number, down: boolean): void {
  if (down && !t.pointer.down) t.pointer.clicked = true;
  t.pointer.x = x;
  t.pointer.y = y;
  t.pointer.down = down;
  t.pointerOn = true;
}

/** The pointer left the screen; a pending click is still delivered. */
export function leavePointer(t: InputTracker): void {
  t.pointer.down = false;
  t.pointerOn = false;
}

/** Forget pending presses (e.g. the key that started the run). Held buttons stay held. */
export function clearEdges(t: InputTracker): void {
  for (const b of RETRO_BUTTONS) t.edges[b] = false;
  t.pointer.clicked = false;
}

/** A pending press of A, or a click: the "start the game" gesture on title screens. */
export function hasStartGesture(t: InputTracker): boolean {
  return t.edges.a || t.pointer.clicked;
}

/**
 * Forget everything held on the keyboard and touch pad (window blur, pause):
 * their release events may never arrive. The gamepad is polled, so it heals
 * by itself on the next read.
 */
export function releaseAll(t: InputTracker): void {
  for (const b of RETRO_BUTTONS) { t.keys[b] = false; t.touch[b] = false; }
  t.pointer.down = false;
  clearEdges(t);
}

/**
 * The input for exactly one game step. Pending edges go into `pressed` and
 * are cleared, so the next step only sees new presses. The returned object is
 * reused by the next call.
 */
export function takeInput(t: InputTracker, withPointer: boolean): RetroInput {
  const input = t.input;
  for (const b of RETRO_BUTTONS) {
    input[b] = isHeld(t, b);
    input.pressed[b] = t.edges[b];
    t.edges[b] = false;
  }
  if (withPointer && (t.pointerOn || t.pointer.clicked)) {
    const p = t.stepPointer;
    p.x = t.pointer.x; p.y = t.pointer.y; p.down = t.pointer.down; p.clicked = t.pointer.clicked;
    input.pointer = p;
  } else {
    input.pointer = null;
  }
  t.pointer.clicked = false;
  return input;
}

/**
 * The 8-way direction of a touch on the d-pad, from its offset to the pad's
 * centre. A small middle zone means "no direction"; a diagonal needs both
 * axes within 22.5 degrees of it.
 */
export function dpadDirections(dx: number, dy: number, radius: number): Pick<RetroButtons, "left" | "right" | "up" | "down"> {
  const off = { left: false, right: false, up: false, down: false };
  if (!(radius > 0) || Math.hypot(dx, dy) < radius * 0.25) return off;
  const tan = Math.tan(Math.PI / 8); // 22.5 degrees
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (ax > ay * tan) { off.left = dx < 0; off.right = dx > 0; }
  if (ay > ax * tan) { off.up = dy < 0; off.down = dy > 0; }
  return off;
}

export interface ScreenRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** A client position over the scaled canvas in logical pixels, or null when it is outside. */
export function toLogical(clientX: number, clientY: number, rect: ScreenRect, width: number, height: number): { x: number; y: number } | null {
  if (!(rect.width > 0) || !(rect.height > 0)) return null;
  const x = ((clientX - rect.left) / rect.width) * width;
  const y = ((clientY - rect.top) / rect.height) * height;
  if (x < 0 || y < 0 || x > width || y > height) return null;
  return { x: Math.min(x, width - 1e-6), y: Math.min(y, height - 1e-6) };
}

/**
 * The CSS scale that shows a `width` x `height` game in the available box:
 * the largest whole number of DEVICE pixels per game pixel (so every game
 * pixel is equally sharp on any display density). Below two device pixels per
 * game pixel a whole factor would waste too much room, so tiny windows get the
 * exact fractional fit instead.
 */
export function screenScale(availWidth: number, availHeight: number, width: number, height: number, dpr = 1): number {
  if (!(availWidth > 0) || !(availHeight > 0) || !(width > 0) || !(height > 0)) return 1;
  const ratio = dpr > 0 && Number.isFinite(dpr) ? dpr : 1;
  const fit = Math.min(availWidth / width, availHeight / height);
  const whole = Math.floor(fit * ratio + 1e-6);
  return whole >= 2 ? whole / ratio : fit;
}
