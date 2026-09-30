import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const motion = vi.hoisted(() => ({ reduced: false }));
vi.mock("framer-motion", () => ({ useReducedMotion: () => motion.reduced }));

import { PetSprite, integerScaleFor } from "@/components/pets/PetSprite";
import { resolvePetAnimation, type PetAnimation } from "@/lib/petStates";

const ANIMATIONS: Record<string, PetAnimation> = {
  idle: { row: 0, frames: 4, fps: 4, loop: true },
  listening: { row: 1, frames: 2, fps: 8, loop: true },
  success: { row: 4, frames: 3, fps: 10, loop: false },
};

const PET = { sheet_url: "/api/pets/gigi/sheet.png", frame_size: 48, animations: ANIMATIONS };

function cell(): HTMLElement {
  return screen.getByTestId("pet-sprite-cell");
}

beforeEach(() => {
  motion.reduced = false;
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("resolvePetAnimation", () => {
  it("uses a state's own row when the manifest has one", () => {
    expect(resolvePetAnimation(ANIMATIONS, "listening")?.row).toBe(1);
  });

  it("walks the fallback chain for a missing state", () => {
    // thinking → listening; talking → listening; sleeping → idle.
    expect(resolvePetAnimation(ANIMATIONS, "thinking")?.row).toBe(1);
    expect(resolvePetAnimation(ANIMATIONS, "talking")?.row).toBe(1);
    expect(resolvePetAnimation(ANIMATIONS, "sleeping")?.row).toBe(0);
    expect(resolvePetAnimation({ idle: ANIMATIONS.idle }, "thinking")?.row).toBe(0);
  });

  it("draws nothing for a manifest without idle", () => {
    expect(resolvePetAnimation({}, "idle")).toBeNull();
  });
});

describe("PetSprite", () => {
  it("cuts one cell at source size and enlarges it by a whole factor", () => {
    render(<PetSprite pet={PET} state="listening" scale={2.6} label="Gigi" />);
    const box = screen.getByRole("img", { name: "Gigi" });
    expect(box.style.width).toBe("144px");
    expect(cell().style.transform).toBe("scale(3)");
    expect(cell().style.backgroundPosition).toBe("0px -48px");
    expect(cell().style.backgroundImage).toContain("/api/pets/gigi/sheet.png");
  });

  it("steps a looping row at the manifest's fps and wraps", () => {
    render(<PetSprite pet={PET} state="idle" scale={1} />);
    expect(cell().style.backgroundPosition).toBe("0px 0px");
    act(() => vi.advanceTimersByTime(250));
    expect(cell().style.backgroundPosition).toBe("-48px 0px");
    act(() => vi.advanceTimersByTime(750));
    expect(cell().style.backgroundPosition).toBe("0px 0px");
  });

  it("holds the last frame of a one-shot row", () => {
    render(<PetSprite pet={PET} state="success" scale={1} />);
    act(() => vi.advanceTimersByTime(1000));
    expect(cell().style.backgroundPosition).toBe("-96px -192px");
  });

  it("shows frame 0 still under reduced motion", () => {
    motion.reduced = true;
    render(<PetSprite pet={PET} state="idle" scale={1} />);
    act(() => vi.advanceTimersByTime(1000));
    expect(cell().style.backgroundPosition).toBe("0px 0px");
  });

  it("is decorative without a label", () => {
    render(<PetSprite pet={PET} state="idle" scale={1} />);
    expect(screen.getByTestId("pet-sprite").getAttribute("aria-hidden")).toBe("true");
  });
});

describe("integerScaleFor", () => {
  it("fits a frame into a box with a whole-number factor", () => {
    expect(integerScaleFor(48, 168)).toBe(3);
    expect(integerScaleFor(32, 96)).toBe(3);
    expect(integerScaleFor(64, 40)).toBe(1);
  });
});
