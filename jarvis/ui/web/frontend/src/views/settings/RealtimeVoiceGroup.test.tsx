import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RealtimeVoiceGroup } from "./RealtimeVoiceGroup";

const fakes = vi.hoisted(() => ({
  mode: "pipeline",
  available: false,
  setMode: vi.fn(),
}));

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: fakes.mode,
    realtimeAvailable: fakes.available,
    sessionActive: false,
    activeSessionMode: null,
    activeSessionProvider: "",
    activeSessionModel: "",
    transitioning: false,
    setMode: fakes.setMode,
    isLoading: false,
    isSaving: false,
  }),
}));

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

afterEach(() => {
  cleanup();
  fakes.mode = "pipeline";
  fakes.available = false;
  fakes.setMode.mockClear();
});

describe("RealtimeVoiceGroup", () => {
  it("blocks enabling Realtime when no provider key is available", () => {
    render(<RealtimeVoiceGroup />);

    expect((screen.getByRole("switch") as HTMLButtonElement).disabled).toBe(true);
  });

  it("allows a stale unavailable Realtime mode to be switched off", () => {
    fakes.mode = "realtime";
    render(<RealtimeVoiceGroup />);

    const toggle = screen.getByRole("switch") as HTMLButtonElement;
    expect(toggle.disabled).toBe(false);
    fireEvent.click(toggle);
    expect(fakes.setMode).toHaveBeenCalledWith("pipeline");
  });
});
