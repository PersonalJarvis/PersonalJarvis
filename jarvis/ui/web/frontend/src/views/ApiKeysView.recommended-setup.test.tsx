/**
 * Component tests for the "Personal recommendation" panel in the voice-engine
 * header band (RecommendedSetupPanel).
 *
 * The panel is a presentation-only hint (AP-21): it lists the maintainer's
 * pick for Realtime / Tool Model / Jarvis-Agents, and each row navigates to
 * the tab it names. Navigation is VIEW-only — opening the Realtime tab set
 * from a recommendation row must never persist `[voice].mode` (only the
 * key-gated segmented switch does that).
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

function panel() {
  return within(screen.getByTestId("recommended-setup-panel"));
}

describe("ApiKeysView recommended-setup panel", () => {
  it("lists the three maintainer picks", () => {
    render(<ApiKeysView />);
    expect(panel().getByText("OpenAI Realtime")).toBeTruthy();
    expect(panel().getByText("Gemini 3.5 Flash")).toBeTruthy();
    expect(panel().getByText("ChatGPT or Claude Max subscription")).toBeTruthy();
  });

  it("keeps the segment buttons uniquely addressable (no /^realtime/i collision)", () => {
    render(<ApiKeysView />);
    // The two-mode tests select the engine segment via /^realtime/i; the
    // recommendation rows must not shadow that accessible name.
    expect(screen.getAllByRole("button", { name: /^realtime/i })).toHaveLength(1);
  });

  it("opens the Tool Model tab from its recommendation row", () => {
    render(<ApiKeysView />);
    fireEvent.click(
      panel().getByRole("button", { name: /personal recommendation: tool model/i }),
    );
    expect(
      (screen.getByRole("tab", { name: /tool model/i }) as HTMLElement).getAttribute(
        "aria-selected",
      ),
    ).toBe("true");
  });

  it("opens the Jarvis-Agents tab from its recommendation row", () => {
    render(<ApiKeysView />);
    fireEvent.click(
      panel().getByRole("button", { name: /personal recommendation: jarvis-agents/i }),
    );
    expect(
      (screen.getByRole("tab", { name: /jarvis-agents/i }) as HTMLElement).getAttribute(
        "aria-selected",
      ),
    ).toBe("true");
  });

  it("opens the Realtime tab set from its row WITHOUT persisting voice mode", () => {
    render(<ApiKeysView />);
    // Pipeline is the default view: no Realtime tab yet.
    expect(screen.queryByRole("tab", { name: /realtime/i })).toBeNull();

    fireEvent.click(
      panel().getByRole("button", { name: /personal recommendation: realtime/i }),
    );

    // The view switched to the Realtime tab set with the Realtime tab selected…
    expect(
      (screen.getByRole("tab", { name: /realtime/i }) as HTMLElement).getAttribute(
        "aria-selected",
      ),
    ).toBe("true");
    // …but `[voice].mode` stays untouched — only the segmented switch persists.
    expect(putVoiceMode).not.toHaveBeenCalled();
  });
});
