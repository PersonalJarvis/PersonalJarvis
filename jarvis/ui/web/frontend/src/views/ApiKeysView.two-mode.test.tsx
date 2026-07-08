/**
 * Component tests for the Pipeline|Realtime segmented VIEW switch on the
 * API-Keys screen.
 *
 * D1 (binding): the switch is VIEW-ONLY. It must never write `[voice].mode`
 * or call any voice-mode mutation — only the existing, separately-gated
 * activation path does that. These tests pin (1) the mode-derived tab sets
 * and (2) that clicking a segment never fires the `useVoiceMode` mutation.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

// Mock the data hooks so the view renders deterministically, without a
// network round-trip.
vi.mock("@/hooks/useProviders", () => ({
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
// exact shape so the "Active" badge + the D1 no-mutation assertion are real.
const putVoiceMode = vi.fn();
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: true,
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

describe("ApiKeysView two-mode", () => {
  it("defaults to Pipeline mode showing Brain/Voice/Subagents tabs, no Realtime tab", () => {
    render(<ApiKeysView />);
    expect(screen.getByRole("tab", { name: /brain/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /voice output/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /voice input/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /jarvis-agents/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /advanced/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /realtime/i })).toBeNull();
  });

  it("shows the segmented Pipeline|Realtime switch with an Active badge on the live mode", () => {
    render(<ApiKeysView />);
    const pipelineSegment = screen.getByRole("button", { name: /pipeline/i });
    const realtimeSegment = screen.getByRole("button", { name: /^realtime$/i });
    expect(pipelineSegment).toBeTruthy();
    expect(realtimeSegment).toBeTruthy();
    // The live [voice].mode from the mocked useVoiceMode is "pipeline", so
    // only the Pipeline segment carries the "Active" badge.
    expect(pipelineSegment.textContent).toMatch(/active/i);
    expect(realtimeSegment.textContent).not.toMatch(/active/i);
  });

  it("switching to Realtime mode shows only Realtime/Subagents/Advanced and NEVER writes voice-mode", () => {
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /^realtime$/i })); // the segment

    expect(screen.getByRole("tab", { name: /realtime/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /jarvis-agents/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /advanced/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /^brain$/i })).toBeNull();
    expect(screen.queryByRole("tab", { name: /voice output/i })).toBeNull();
    expect(screen.queryByRole("tab", { name: /voice input/i })).toBeNull();

    // D1 (binding): the segment switch is view-only — it must never mutate
    // the live voice engine.
    expect(putVoiceMode).not.toHaveBeenCalled();
  });

  it("switching back to Pipeline restores the five pipeline tabs, still without mutating voice-mode", () => {
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /^realtime$/i }));
    fireEvent.click(screen.getByRole("button", { name: /pipeline/i }));

    expect(screen.getByRole("tab", { name: /brain/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /realtime/i })).toBeNull();
    expect(putVoiceMode).not.toHaveBeenCalled();
  });
});
