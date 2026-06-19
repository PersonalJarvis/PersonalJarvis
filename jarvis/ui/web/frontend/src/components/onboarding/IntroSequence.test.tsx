import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="gigi" /> }));

import { IntroSequence } from "./IntroSequence";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("renders Gigi, the first caption, and 4 progress dots", () => {
  render(<IntroSequence />);
  expect(screen.getByTestId("gigi")).toBeDefined();
  expect(screen.getByText("onboarding.intro.scene_1")).toBeDefined();
});

it("auto-advances through the scenes over time", () => {
  vi.useFakeTimers();
  render(<IntroSequence />);
  expect(screen.getByText("onboarding.intro.scene_1")).toBeDefined();
  act(() => {
    vi.advanceTimersByTime(2800);
  });
  expect(screen.getByText("onboarding.intro.scene_2")).toBeDefined();
  act(() => {
    vi.advanceTimersByTime(2800 * 5);
  });
  // Clamps at the last scene (does not loop past scene_4).
  expect(screen.getByText("onboarding.intro.scene_4")).toBeDefined();
});

it("respects prefers-reduced-motion (renders the final scene, no timer)", () => {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: q.includes("reduce"),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
  render(<IntroSequence />);
  expect(screen.getByText("onboarding.intro.scene_4")).toBeDefined();
});
