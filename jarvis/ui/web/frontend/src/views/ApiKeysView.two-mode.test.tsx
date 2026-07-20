/**
 * Component tests for the Pipeline|Realtime segmented switch on the
 * API-Keys screen.
 *
 * Feature A (supersedes D1): the segment is a real mode control. Clicking a
 * segment still switches the local VIEW, but now ALSO persists
 * `[voice].mode` via `useVoiceMode().setMode` — Pipeline always, Realtime
 * only when `realtimeAvailable` is true (a key is actually configured for
 * some realtime family). These tests pin (1) the mode-derived tab sets and
 * (2) the setMode call pattern for both availability states.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

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

describe("ApiKeysView two-mode", () => {
  it("keeps the mode switch in the header and scrolls its context with providers", () => {
    render(<ApiKeysView />);

    const headerControl = screen.getByTestId("voice-engine-header-control");
    const providerScroll = screen.getByTestId("api-keys-provider-scroll");
    const context = screen.getByTestId("voice-engine-context");
    const categoryTabs = screen.getByTestId("api-keys-category-tabs");

    expect(headerControl.closest("header")).not.toBeNull();
    expect(providerScroll.contains(context)).toBe(true);
    expect(providerScroll.className).toContain("min-h-0");
    expect(categoryTabs.className).toContain("overflow-x-auto");
    expect(screen.getByRole("tablist").className).toContain("min-w-max");
    expect(screen.getAllByTestId("voice-engine-runtime-status")).toHaveLength(1);
  });

  it("defaults to Pipeline mode showing Brain/Voice/Subagents tabs, no Realtime tab", () => {
    render(<ApiKeysView />);
    expect(screen.getByRole("tab", { name: /brain/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /voice output/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /voice input/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /-agents$/i })).toBeTruthy();
    // The dedicated key tab is named after the assistant ({name} token; the
    // uncached test default resolves to "Assistant").
    expect(screen.getByRole("tab", { name: /assistant key/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /advanced/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /realtime/i })).toBeNull();
  });

  it("shows the segmented Pipeline|Realtime switch with an Active badge on the live mode", () => {
    render(<ApiKeysView />);
    const pipelineSegment = screen.getByRole("button", { name: /pipeline/i });
    const realtimeSegment = screen.getByRole("button", { name: /^realtime/i });
    expect(pipelineSegment).toBeTruthy();
    expect(realtimeSegment).toBeTruthy();
    // The live [voice].mode from the mocked useVoiceMode is "pipeline", so
    // only the Pipeline segment carries the "Active" badge.
    expect(pipelineSegment.textContent).toMatch(/active/i);
    expect(realtimeSegment.textContent).not.toMatch(/active/i);
  });

  it("marks Realtime as recommended and Pipeline as not recommended", () => {
    render(<ApiKeysView />);
    const pipelineSegment = screen.getByRole("button", { name: /pipeline/i });
    const realtimeSegment = screen.getByRole("button", { name: /^realtime/i });
    expect(pipelineSegment.textContent).toMatch(/not recommended/i);
    expect(realtimeSegment.textContent).toMatch(/recommended/i);
    expect(realtimeSegment.textContent).not.toMatch(/not recommended/i);
  });

  it("shows a research-preview disclaimer when Realtime is selected", () => {
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /^realtime/i }));
    expect(screen.getByText(/research preview/i)).toBeTruthy();
  });

  it("shows when the selected Realtime mode is still served by Pipeline", () => {
    mockVoiceMode = "realtime";
    mockSessionActive = true;
    mockActiveSessionMode = "pipeline";

    render(<ApiKeysView />);

    expect(screen.getByTestId("voice-engine-runtime-status").textContent).toMatch(
      /fell back to Pipeline/i,
    );
  });

  it("switching to Realtime mode shows only Realtime/Subagents/Advanced and persists voice-mode (available)", () => {
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /^realtime/i })); // the segment

    expect(screen.getByRole("tab", { name: /realtime/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /-agents$/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /assistant key/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /advanced/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /^brain$/i })).toBeNull();
    expect(screen.queryByRole("tab", { name: /voice output/i })).toBeNull();
    expect(screen.queryByRole("tab", { name: /voice input/i })).toBeNull();

    // Feature A (supersedes D1): with a realtime provider actually reachable
    // (mocked realtimeAvailable=true), the segment now persists the mode.
    expect(putVoiceMode).toHaveBeenCalledWith("realtime");
  });

  it("switching back to Pipeline restores the five pipeline tabs and always persists voice-mode", () => {
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /^realtime/i }));
    putVoiceMode.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /pipeline/i }));

    expect(screen.getByRole("tab", { name: /brain/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /realtime/i })).toBeNull();
    // Pipeline always persists — it is always reachable (no key gate needed).
    expect(putVoiceMode).toHaveBeenCalledWith("pipeline");
  });
});

describe("ApiKeysView two-mode — realtime unavailable (no key in any family)", () => {
  it("does not paint an unavailable configured Realtime mode as active", () => {
    mockVoiceMode = "realtime";
    mockRealtimeAvailable = false;
    render(<ApiKeysView />);

    const realtimeSegment = screen.getByRole("button", { name: /^realtime/i });
    expect(realtimeSegment.textContent).not.toMatch(/active/i);
    expect(screen.queryByTestId("voice-engine-live-thumb")).toBeNull();
  });

  it("switching to Realtime still switches the view, but does NOT persist voice-mode", () => {
    mockRealtimeAvailable = false;
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /^realtime/i }));

    // The view still switches, so the user can add a key from the Realtime tab.
    expect(screen.getByRole("tab", { name: /realtime/i })).toBeTruthy();
    // But nothing is reachable yet — never pin [voice].mode to a dead engine.
    expect(putVoiceMode).not.toHaveBeenCalled();
  });

  it("switching back to Pipeline still persists voice-mode even when realtime is unavailable", () => {
    mockRealtimeAvailable = false;
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /^realtime/i }));
    putVoiceMode.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /pipeline/i }));

    expect(putVoiceMode).toHaveBeenCalledWith("pipeline");
  });
});
