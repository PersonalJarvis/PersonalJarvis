/** Provider navigation no longer exposes the retired recommendation panel. */
import { renderWithQueryClient as render } from "@/test/queryRender";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";

// Profile editing has its own QueryClient-backed component tests.
vi.mock("@/components/providers/LiveProfile", () => ({ LiveProfile: () => null }));

// Mock the data hooks so the view renders deterministically, without a
// network round-trip (same pattern as ApiKeysView.two-mode.test.tsx).
vi.mock("@/hooks/useProviders", () => ({
  sectionHealthForSubject: (
    health: { subject_id?: string } | undefined,
    subjectId?: string,
  ) => (subjectId && health?.subject_id === subjectId ? health : undefined),
  useProviders: () => ({
    providers: [],
    loading: false,
    error: null,
    refetch: vi.fn(),
    setActiveOptimistic: vi.fn(),
  }),
  useSectionHealth: () => ({ health: {} }),
}));

const putVoiceMode = vi.fn();
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: true,
    statusKnown: true,
    sessionActive: false,
    activeSessionMode: null,
    activeSessionProvider: "",
    activeSessionModel: "",
    transitioning: false,
    setMode: putVoiceMode,
    isLoading: false,
    isSaving: false,
  }),
}));

import { ApiKeysView } from "@/views/ApiKeysView";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Switch the VIEW to the Realtime tab set via the engine segment. */
function openRealtimeView() {
  fireEvent.click(screen.getByRole("button", { name: /^realtime/i }));
}

describe("ApiKeysView simplified setup", () => {
  it("keeps provider recommendations and a separate tool-model tab out of both modes", () => {
    render(<ApiKeysView />);
    expect(screen.queryByTestId("recommended-setup-panel")).toBeNull();
    expect(screen.queryByRole("tab", { name: /tool model/i })).toBeNull();
    openRealtimeView();
    expect(screen.queryByTestId("recommended-setup-panel")).toBeNull();
    expect(screen.queryByRole("tab", { name: /tool model/i })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^realtime/i })).toHaveLength(1);
  });

  it("allows provider tab navigation without changing the voice mode", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    putVoiceMode.mockClear();
    fireEvent.click(screen.getByRole("tab", { name: /agents$/i }));
    expect(screen.getByRole("tab", { name: /agents$/i }).getAttribute("aria-selected")).toBe("true");
    expect(putVoiceMode).not.toHaveBeenCalled();
  });
});
