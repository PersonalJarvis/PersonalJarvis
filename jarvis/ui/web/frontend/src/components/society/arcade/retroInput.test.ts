import { describe, expect, it } from "vitest";
import {
  STICK_DEAD_ZONE, clearEdges, createInputTracker, dpadDirections, firstGamepad, hasStartGesture, keyButton, leavePointer, pressKey,
  readGamepad, releaseAll, releaseKey, screenScale, setPointer, setTouch, takeInput, toLogical, type GamepadLike,
} from "./retroInput";

function pad(buttons: number[] = [], axes: number[] = [0, 0]): GamepadLike {
  return { axes, buttons: Array.from({ length: 17 }, (_, i) => ({ pressed: buttons.includes(i) })) };
}

describe("retro keyboard mapping", () => {
  it("maps arrows/WASD, Space/J/Z and Shift/K/X", () => {
    expect(["ArrowLeft", "KeyA"].map(keyButton)).toEqual(["left", "left"]);
    expect(["ArrowRight", "KeyD", "ArrowUp", "KeyW", "ArrowDown", "KeyS"].map(keyButton)).toEqual(["right", "right", "up", "up", "down", "down"]);
    expect(["Space", "KeyJ", "KeyZ"].map(keyButton)).toEqual(["a", "a", "a"]);
    expect(["ShiftLeft", "ShiftRight", "KeyK", "KeyX"].map(keyButton)).toEqual(["b", "b", "b", "b"]);
    expect(keyButton("KeyP")).toBeNull();
    expect(keyButton("Enter")).toBeNull();
  });

  it("delivers a press to exactly one step and keeps the hold", () => {
    const t = createInputTracker();
    expect(pressKey(t, "Space")).toBe(true);
    const first = takeInput(t, false);
    expect(first.a).toBe(true);
    expect(first.pressed.a).toBe(true);
    const second = takeInput(t, false);
    expect(second.a).toBe(true);
    expect(second.pressed.a).toBe(false);
    releaseKey(t, "Space");
    expect(takeInput(t, false).a).toBe(false);
  });

  it("keeps a tap shorter than a frame", () => {
    const t = createInputTracker();
    pressKey(t, "KeyJ");
    releaseKey(t, "KeyJ");
    const input = takeInput(t, false);
    expect(input.a).toBe(false);
    expect(input.pressed.a).toBe(true);
  });

  it("never counts auto-repeat as a new press", () => {
    const t = createInputTracker();
    pressKey(t, "ArrowUp");
    takeInput(t, false);
    pressKey(t, "ArrowUp", true);
    expect(takeInput(t, false).pressed.up).toBe(false);
    // After a blur forgot the held key, a repeat holds it again but is still no press.
    releaseAll(t);
    pressKey(t, "ArrowUp", true);
    const input = takeInput(t, false);
    expect(input.up).toBe(true);
    expect(input.pressed.up).toBe(false);
  });

  it("does not release a button still held on another source", () => {
    const t = createInputTracker();
    pressKey(t, "KeyX");
    setTouch(t, "b", true);
    takeInput(t, false);
    releaseKey(t, "KeyX");
    expect(takeInput(t, false).b).toBe(true);
    setTouch(t, "b", false);
    expect(takeInput(t, false).b).toBe(false);
  });

  it("counts a second source joining a held button as no new press", () => {
    const t = createInputTracker();
    pressKey(t, "KeyZ");
    takeInput(t, false);
    setTouch(t, "a", true);
    expect(takeInput(t, false).pressed.a).toBe(false);
  });

  it("forgets keyboard and touch on release-all, and pending edges with them", () => {
    const t = createInputTracker();
    pressKey(t, "ArrowLeft");
    setTouch(t, "a", true);
    releaseAll(t);
    const input = takeInput(t, false);
    expect(input.left || input.a || input.pressed.left || input.pressed.a).toBe(false);
  });

  it("clears pending edges but keeps holds", () => {
    const t = createInputTracker();
    pressKey(t, "Space");
    expect(hasStartGesture(t)).toBe(true);
    clearEdges(t);
    expect(hasStartGesture(t)).toBe(false);
    const input = takeInput(t, false);
    expect(input.a).toBe(true);
    expect(input.pressed.a).toBe(false);
  });

  it("reuses one input object between steps", () => {
    const t = createInputTracker();
    expect(takeInput(t, false)).toBe(takeInput(t, false));
  });
});

describe("retro gamepad mapping", () => {
  it("reads the d-pad, A and both B buttons", () => {
    const t = createInputTracker();
    readGamepad(t, pad([12, 15, 0]));
    let input = takeInput(t, false);
    expect([input.up, input.right, input.a, input.down, input.left, input.b]).toEqual([true, true, true, false, false, false]);
    expect(input.pressed.a).toBe(true);
    readGamepad(t, pad([2]));
    input = takeInput(t, false);
    expect([input.up, input.a, input.b]).toEqual([false, false, true]);
    readGamepad(t, pad([1]));
    expect(takeInput(t, false).pressed.b).toBe(false);
  });

  it("steers with the left stick outside its dead zone", () => {
    const t = createInputTracker();
    readGamepad(t, pad([], [STICK_DEAD_ZONE * 0.9, -STICK_DEAD_ZONE * 0.9]));
    let input = takeInput(t, false);
    expect(input.right || input.up).toBe(false);
    readGamepad(t, pad([], [-0.8, 0.9]));
    input = takeInput(t, false);
    expect([input.left, input.down, input.right, input.up]).toEqual([true, true, false, false]);
  });

  it("reports Start once per press", () => {
    const t = createInputTracker();
    expect(readGamepad(t, pad([9]))).toBe(true);
    expect(readGamepad(t, pad([9]))).toBe(false);
    expect(readGamepad(t, pad([]))).toBe(false);
    expect(readGamepad(t, pad([9]))).toBe(true);
  });

  it("releases everything when the pad disconnects", () => {
    const t = createInputTracker();
    readGamepad(t, pad([0, 14]));
    readGamepad(t, null);
    const input = takeInput(t, false);
    expect(input.a || input.left).toBe(false);
  });

  it("picks the first connected pad from the browser's list", () => {
    const one = { ...pad(), connected: false };
    const two = { ...pad([0]), connected: true };
    expect(firstGamepad([null, one, two])).toBe(two);
    expect(firstGamepad([])).toBeNull();
    expect(firstGamepad(null)).toBeNull();
  });
});

describe("retro pointer and touch", () => {
  it("delivers a click once and the pointer only to pointer games", () => {
    const t = createInputTracker();
    setPointer(t, 10, 20, true);
    expect(takeInput(t, false).pointer).toBeNull();
    setPointer(t, 12, 20, false);
    setPointer(t, 12, 21, true);
    const first = takeInput(t, true);
    expect(first.pointer).toEqual({ x: 12, y: 21, down: true, clicked: true });
    expect(takeInput(t, true).pointer?.clicked).toBe(false);
  });

  it("still delivers a click that left the screen before the step", () => {
    const t = createInputTracker();
    setPointer(t, 5, 5, true);
    leavePointer(t);
    expect(takeInput(t, true).pointer?.clicked).toBe(true);
    expect(takeInput(t, true).pointer).toBeNull();
  });

  it("maps client positions through the CSS scale", () => {
    const rect = { left: 100, top: 50, width: 960, height: 720 };
    expect(toLogical(100, 50, rect, 320, 240)).toEqual({ x: 0, y: 0 });
    expect(toLogical(580, 410, rect, 320, 240)).toEqual({ x: 160, y: 120 });
    expect(toLogical(99, 60, rect, 320, 240)).toBeNull();
    expect(toLogical(200, 60, { ...rect, width: 0 }, 320, 240)).toBeNull();
    const edge = toLogical(1060, 770, rect, 320, 240);
    expect(edge && edge.x < 320 && edge.y < 240).toBe(true);
  });

  it("splits the d-pad into 8 directions with a quiet middle", () => {
    expect(dpadDirections(2, 2, 40)).toEqual({ left: false, right: false, up: false, down: false });
    expect(dpadDirections(30, 3, 40)).toEqual({ left: false, right: true, up: false, down: false });
    expect(dpadDirections(-3, -30, 40)).toEqual({ left: false, right: false, up: true, down: false });
    expect(dpadDirections(-20, 20, 40)).toEqual({ left: true, right: false, up: false, down: true });
  });
});

describe("retro screen scale", () => {
  it("picks the largest whole factor that fits", () => {
    expect(screenScale(1000, 800, 320, 240)).toBe(3);
    expect(screenScale(640, 480, 320, 240)).toBe(2);
  });

  it("counts whole device pixels on dense displays", () => {
    expect(screenScale(1000, 800, 320, 240, 2)).toBe(3);
    expect(screenScale(700, 500, 320, 240, 1.5)).toBe(2);
    expect(screenScale(700, 500, 320, 240, 1.25)).toBe(1.6);
    expect(screenScale(500, 400, 320, 240, 1.25)).toBeCloseTo(1.5625);
  });

  it("falls back to a fractional fit on tiny screens", () => {
    expect(screenScale(480, 400, 320, 240)).toBeCloseTo(1.5);
    expect(screenScale(160, 400, 320, 240)).toBeCloseTo(0.5);
  });

  it("survives an unmeasured box", () => {
    expect(screenScale(0, 0, 320, 240)).toBe(1);
  });
});
