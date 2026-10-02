import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import type { RetroGame, RetroDrawInfo } from "./retroGame";
import { RetroArcadeOverlay } from "./RetroArcadeOverlay";
import { bestKey, clearBestMemory } from "./retroScores";

/** A tiny game: A scores 10 per press, B ends the run (B with up held wins). */
interface FakeState { score: number; over: boolean; won: boolean; steps: number }
const draws: RetroDrawInfo[] = [];
let lastState: FakeState | null = null;
const fakeGame: RetroGame<FakeState> = {
  id: "neon-snake",
  width: 40,
  height: 30,
  create: () => (lastState = { score: 0, over: false, won: false, steps: 0 }),
  step: (s, input) => {
    s.steps++;
    if (input.pressed.a) s.score += 10;
    if (input.b) { s.over = true; s.won = input.up; }
  },
  draw: (_ctx, _s, info) => { draws.push({ ...info }); },
  status: (s) => ({ score: s.score, lives: 3, over: s.over, won: s.won }),
};
const loadFake = async () => fakeGame as RetroGame<unknown>;

/** Animation frames run only when the test says so, with explicit timestamps. */
let frames = new Map<number, FrameRequestCallback>();
let nextFrame = 1;
let clock = 0;
function frame(ms = 1000 / 60) {
  clock += ms;
  const due = frames;
  frames = new Map();
  act(() => { for (const cb of due.values()) cb(clock); });
}

const key = (type: "keyDown" | "keyUp", code: string, keyName = code) => fireEvent[type](window, { code, key: keyName });
const phase = () => screen.getByRole("dialog").getAttribute("data-phase");

beforeAll(async () => { await loadLocaleChunk("society"); });

beforeEach(() => {
  frames = new Map();
  clock = 0;
  draws.length = 0;
  lastState = null;
  clearBestMemory();
  window.localStorage.clear();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { frames.set(nextFrame, cb); return nextFrame++; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames.delete(id); });
  // jsdom has no 2D canvas; the fake game only needs a context to be handed one.
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function mount(onClose = vi.fn(), loadGame = loadFake) {
  render(<RetroArcadeOverlay gameId="neon-snake" onClose={onClose} loadGame={loadGame} />);
  const dialog = screen.getByRole("dialog", { name: "Neon Snake" });
  await vi.waitFor(() => expect(dialog.getAttribute("data-phase")).toBe("ready"));
  return { dialog, onClose };
}

describe("retro arcade overlay", () => {
  test("opens as a modal on the title screen and draws the idle game", async () => {
    const { dialog } = await mount();
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("data-state")).toBe("open");
    expect(document.activeElement).toBe(dialog);
    frame();
    expect(draws.at(-1)?.idle).toBe(true);
    expect(dialog.querySelector("canvas")?.width).toBe(40);
  });

  test("starts on Space, hands each press to exactly one step, and pauses on P", async () => {
    await mount();
    frame();
    key("keyDown", "Space", " ");
    frame();
    expect(phase()).toBe("playing");
    key("keyUp", "Space", " ");
    // The press that started the run never reaches the game.
    frame();
    expect(lastState?.score).toBe(0);
    expect(draws.at(-1)?.idle).toBe(false);

    key("keyDown", "KeyJ", "j");
    frame(3 * (1000 / 60));
    expect(lastState?.score).toBe(10);
    frame();
    expect(lastState?.score).toBe(10);
    key("keyUp", "KeyJ", "j");

    key("keyDown", "KeyP", "p");
    expect(phase()).toBe("paused");
    const steps = lastState?.steps;
    frame();
    frame();
    expect(lastState?.steps).toBe(steps);
    expect(draws.at(-1)?.idle).toBe(true);
    key("keyUp", "KeyP", "p");
    key("keyDown", "KeyP", "p");
    frame();
    expect(phase()).toBe("playing");
  });

  test("ends a run, keeps the best score and waits before a restart", async () => {
    await mount();
    frame();
    key("keyDown", "Enter", "Enter");
    frame();
    key("keyUp", "Enter", "Enter");
    for (let i = 0; i < 2; i++) {
      key("keyDown", "Space", " ");
      frame();
      key("keyUp", "Space", " ");
    }
    key("keyDown", "KeyX", "x");
    frame();
    key("keyUp", "KeyX", "x");
    expect(phase()).toBe("over");
    expect(window.localStorage.getItem(bestKey("neon-snake"))).toBe("20");
    expect(document.querySelector(".retro-cab-newbest")).toBeTruthy();

    // A fire key still hammered right after the end does not skip the result.
    key("keyDown", "Space", " ");
    frame(100);
    expect(phase()).toBe("over");
    key("keyUp", "Space", " ");
    key("keyDown", "Enter", "Enter");
    frame(700);
    expect(phase()).toBe("playing");
    expect(lastState?.score).toBe(0);
  });

  test("Escape leaves, the office never sees the key, and focus returns", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const officeSaw = vi.fn();
    document.addEventListener("keydown", officeSaw, true);
    const { onClose } = await mount();
    frame();
    key("keyDown", "Escape", "Escape");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(officeSaw).not.toHaveBeenCalled();
    cleanup();
    expect(frames.size).toBe(0);
    expect(document.activeElement).toBe(opener);
    document.removeEventListener("keydown", officeSaw, true);
    opener.remove();
  });

  test("E and the backdrop leave too", async () => {
    const { onClose } = await mount();
    key("keyDown", "KeyE", "e");
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.pointerDown(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  test("losing the window pauses and forgets held keys", async () => {
    await mount();
    frame();
    key("keyDown", "Enter", "Enter");
    frame();
    key("keyDown", "KeyX", "x");
    fireEvent.blur(window);
    expect(phase()).toBe("paused");
    key("keyDown", "Enter", "Enter");
    frame();
    frame();
    expect(phase()).toBe("playing");
    expect(lastState?.over).toBe(false);
  });

  test("shows an error with a retry when the game cannot load", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    let fail = true;
    const flaky = async () => {
      if (fail) { fail = false; throw new Error("chunk missing"); }
      return fakeGame as RetroGame<unknown>;
    };
    render(<RetroArcadeOverlay gameId="neon-snake" onClose={vi.fn()} loadGame={flaky} />);
    await vi.waitFor(() => expect(phase()).toBe("error"));
    fireEvent.click(screen.getByRole("button", { name: /retry|try again/i }));
    await vi.waitFor(() => expect(phase()).toBe("ready"));
  });

  test("keeps a new best score when closed mid-run", async () => {
    await mount();
    frame();
    key("keyDown", "Enter", "Enter");
    frame();
    key("keyDown", "Space", " ");
    frame();
    expect(lastState?.score).toBe(10);
    cleanup();
    expect(window.localStorage.getItem(bestKey("neon-snake"))).toBe("10");
  });
});
