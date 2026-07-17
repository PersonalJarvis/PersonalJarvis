/**
 * Component tests for the "Personal recommendation" panel in the voice-engine
 * header band (RecommendedSetupPanel).
 *
 * The panel is a presentation-only hint (AP-21) for the REALTIME tab set: it
 * lists the maintainer's pick for Realtime / Tool Model / Jarvis-Agents, so it
 * renders ONLY while the Realtime tab set is being viewed (maintainer feedback
 * 2026-07-17: next to the Pipeline tabs it would point at tabs that are not on
 * screen). Each row navigates to the tab it names — VIEW-only navigation that
 * must never persist `[voice].mode` (only the key-gated segmented switch does).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

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

  it("lists the three maintainer picks once the Realtime tab set is viewed", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    expect(panel().getByText("OpenAI Realtime")).toBeTruthy();
    expect(panel().getByText("Gemini 3.5 Flash")).toBeTruthy();
    expect(panel().getByText("ChatGPT or Claude Max subscription")).toBeTruthy();
  });

  it("keeps the segment buttons uniquely addressable (no /^realtime/i collision)", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    // The two-mode tests select the engine segment via /^realtime/i; the
    // recommendation rows must not shadow that accessible name.
    expect(screen.getAllByRole("button", { name: /^realtime/i })).toHaveLength(1);
  });

  it("opens the Tool Model tab from its recommendation row", () => {
    render(<ApiKeysView />);
    openRealtimeView();
    // Rows are addressed by stable testids, not their labels — the tab labels
    // are i18n strings other work renames (e.g. Jarvis-Agents -> {name}-Agents).
    fireEvent.click(screen.getByTestId("reco-row-computer-use"));
    expect(
      (screen.getByRole("tab", { name: /tool model/i }) as HTMLElement).getAttribute(
        "aria-selected",
      ),
    ).toBe("true");
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
