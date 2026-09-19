/**
 * Component tests for the "Personal recommendation" panel in the voice-engine
 * scrollable engine context (RecommendedSetupPanel).
 *
 * The panel is a presentation-only hint (AP-21) for the REALTIME tab set: it
 * lists the maintainer's picks for Realtime and Jarvis-Agents, so it
 * renders ONLY while the Realtime tab set is being viewed (maintainer feedback
 * 2026-07-17: next to the Pipeline tabs it would point at tabs that are not on
 * screen). Each row navigates to the tab it names — VIEW-only navigation that
 * must never persist `[voice].mode` (only the key-gated segmented switch does).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

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

function panel() {
  return within(screen.getByTestId("recommended-setup-panel"));
}

describe("ApiKeysView recommended-setup panel", () => {
  it("stays hidden while the Pipeline tab set is viewed", () => {
    render(<ApiKeysView />);
    expect(screen.queryByTestId("recommended-setup-panel")).toBeNull();
  });

  it("lists the current maintainer picks once the Realtime tab set is viewed", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    expect(
      screen
        .getByTestId("api-keys-provider-scroll")
        .contains(screen.getByTestId("recommended-setup-panel")),
    ).toBe(true);
    expect(panel().getByText("OpenAI GPT-Live")).toBeTruthy();
    expect(panel().getByText("ChatGPT or Claude Max subscription")).toBeTruthy();
    expect(panel().getAllByRole("button")).toHaveLength(2);
  });

  it("keeps the segment buttons uniquely addressable (no /^realtime/i collision)", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    // The two-mode tests select the engine segment via /^realtime/i; the
    // recommendation rows must not shadow that accessible name.
    expect(screen.getAllByRole("button", { name: /^realtime/i })).toHaveLength(1);
  });

  it("keeps the Tool Model tab reachable alongside the current recommendation rows", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    // GPT-Live has its own thinking-model settings. The global Computer-Use
    // selection remains reachable without inventing a removed recommendation.
    expect(screen.queryByTestId("reco-row-computer-use")).toBeNull();
    putVoiceMode.mockClear();
    fireEvent.click(screen.getByRole("tab", { name: /tool model/i }));
    expect(
      (screen.getByRole("tab", { name: /tool model/i }) as HTMLElement).getAttribute(
        "aria-selected",
      ),
    ).toBe("true");
    expect(putVoiceMode).not.toHaveBeenCalled();
  });

  it("opens the agents tab from its recommendation row", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    fireEvent.click(screen.getByTestId("reco-row-subagents"));
    // Label-agnostic: both "Jarvis-Agents" and a "{name}-Agents" rebrand end
    // in "Agents", and no other tab does.
    expect(
      (screen.getByRole("tab", { name: /agents$/i }) as HTMLElement).getAttribute(
        "aria-selected",
      ),
    ).toBe("true");
  });

  it("returns to the Realtime tab from its row WITHOUT persisting voice mode", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    // Wander off to another tab of the realtime set first.
    fireEvent.click(screen.getByRole("tab", { name: /tool model/i }));
    putVoiceMode.mockClear();

    fireEvent.click(screen.getByTestId("reco-row-realtime"));

    // The Realtime tab is selected again…
    expect(
      (screen.getByRole("tab", { name: /realtime/i }) as HTMLElement).getAttribute(
        "aria-selected",
      ),
    ).toBe("true");
    // …and `[voice].mode` stays untouched — only the segmented switch persists.
    expect(putVoiceMode).not.toHaveBeenCalled();
  });
});
