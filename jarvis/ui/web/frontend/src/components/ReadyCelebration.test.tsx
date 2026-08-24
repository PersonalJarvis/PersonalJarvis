/**
 * The "all lights green" note shows exactly once: when readiness is true and
 * the backend has not recorded the moment yet. "Let's go" records it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

const state = vi.hoisted(() => ({
  readiness: { mode: "pipeline", required: [], sections: {}, ready: false, celebrated: false, starter_plan: null },
  markReadyCelebrated: vi.fn(async () => undefined),
}));
vi.mock("@/hooks/useStarterPlans", () => ({
  getReadiness: async () => state.readiness,
  markReadyCelebrated: state.markReadyCelebrated,
}));

import { ReadyCelebration } from "./ReadyCelebration";

afterEach(() => {
  cleanup();
  state.markReadyCelebrated.mockClear();
});

describe("ReadyCelebration", () => {
  it("stays silent while something is not ready", async () => {
    state.readiness = { ...state.readiness, ready: false, celebrated: false };
    render(<ReadyCelebration />);
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId("ready-celebration")).toBeNull();
  });

  it("stays silent once the moment was already recorded", async () => {
    state.readiness = { ...state.readiness, ready: true, celebrated: true };
    render(<ReadyCelebration />);
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId("ready-celebration")).toBeNull();
  });

  it("shows once everything answers, and 'go' records it", async () => {
    state.readiness = { ...state.readiness, ready: true, celebrated: false };
    render(<ReadyCelebration inline />);
    await screen.findByTestId("ready-celebration");
    expect(screen.getByText("ready_note.title")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "ready_note.go" }));
    await waitFor(() => expect(state.markReadyCelebrated).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("ready-celebration")).toBeNull();
  });
});
