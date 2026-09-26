import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const setVoiceMode = vi.fn();

vi.mock("@/hooks/useProviders", () => ({
  sectionHealthForSubject: () => undefined,
  useProviders: () => ({
    providers: [],
    loading: false,
    error: null,
    refetch: vi.fn(),
    setActiveOptimistic: vi.fn(),
  }),
  useSectionHealth: () => ({ health: {} }),
}));

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: true,
    setMode: setVoiceMode,
    isLoading: false,
    isSaving: false,
  }),
}));

import { ApiKeysView } from "@/views/ApiKeysView";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ApiKeysView model selection", () => {
  it("uses the active voice engine without a separate computer-use model tab", () => {
    render(<ApiKeysView />);

    expect(screen.getByRole("tab", { name: /^brain$/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /computer.use|tool model/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^realtime/i }));

    expect(screen.getByRole("tab", { name: /^realtime$/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /computer.use|tool model/i })).toBeNull();
    expect(screen.queryByTestId("recommended-setup-panel")).toBeNull();
    expect(setVoiceMode).toHaveBeenCalledWith("realtime");
  });
});
