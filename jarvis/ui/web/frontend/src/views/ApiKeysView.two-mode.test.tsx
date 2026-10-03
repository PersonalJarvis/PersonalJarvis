/** Existing voice configurations remain readable without silently switching engines. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";

function renderKeys(ui: ReactElement = <ApiKeysView />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// Mock the data hooks so the view renders deterministically, without a
// network round-trip.
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

// The real `useVoiceMode` hook (jarvis/ui/web/frontend/src/hooks/useVoiceMode.ts)
// returns { mode, realtimeAvailable, setMode, isLoading, isSaving } — mock that
// exact shape so the "Active" badge + the setMode assertions below are real.
// `mockRealtimeAvailable` is mutable per-test (declared via `let` above the
// `vi.mock` call, matching this file's existing hoisting pattern) so the
// "realtime unavailable" describe block below can flip it.
let mockRealtimeAvailable = true;
let mockVoiceMode = "pipeline";
let mockSessionActive = false;
let mockActiveSessionMode: "pipeline" | "realtime" | null = null;
let mockTransitioning = false;
const putVoiceMode = vi.fn();
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: mockVoiceMode,
    realtimeAvailable: mockRealtimeAvailable,
    statusKnown: true,
    sessionActive: mockSessionActive,
    activeSessionMode: mockActiveSessionMode,
    activeSessionProvider: "",
    activeSessionModel: "",
    transitioning: mockTransitioning,
    setMode: putVoiceMode,
    isLoading: false,
    isSaving: false,
  }),
}));

import { ApiKeysView } from "@/views/ApiKeysView";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mockRealtimeAvailable = true;
  mockVoiceMode = "pipeline";
  mockSessionActive = false;
  mockActiveSessionMode = null;
  mockTransitioning = false;
});

describe("ApiKeysView existing voice configurations", () => {
  it.each(["pipeline", "realtime"])("opens Realtime settings without changing a %s installation", (mode) => {
    mockVoiceMode = mode;
    renderKeys();
    expect(screen.getByRole("tab", { name: /^realtime$/i }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByTestId("voice-engine-header-control")).toBeNull();
    expect(screen.queryByRole("tab", { name: /^brain$|voice output|voice input/i })).toBeNull();
    expect(putVoiceMode).not.toHaveBeenCalled();
  });
  it.each([true, false])("keeps setup reachable with realtime availability %s", (available) => {
    mockRealtimeAvailable = available;
    renderKeys();
    expect(screen.getByRole("tab", { name: /^realtime$/i })).toBeTruthy();
    expect(screen.getByTestId("api-keys-provider-scroll")).toBeTruthy();
    expect(putVoiceMode).not.toHaveBeenCalled();
  });
  it("shows an active fallback separately from the selected setup page", () => {
    mockVoiceMode = "realtime";
    mockSessionActive = true;
    mockActiveSessionMode = "pipeline";
    renderKeys();
    expect(screen.getByTestId("voice-now-note-backup")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /^realtime$/i }).getAttribute("aria-selected")).toBe("true");
    expect(putVoiceMode).not.toHaveBeenCalled();
  });
});
