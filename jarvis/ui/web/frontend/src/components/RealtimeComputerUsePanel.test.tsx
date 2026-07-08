/**
 * Component tests for `RealtimeComputerUsePanel` (Feature B — the Computer-Use
 * panel embedded in Realtime mode on the API-Keys screen).
 *
 * Realtime speech-to-speech models can't see the screen, so Computer-Use
 * during a realtime turn falls back to the ACTIVE Brain provider — exactly
 * what CU already runs on today. These tests pin: (1) the panel names the
 * active Brain provider and forwards its id (never a realtime id) to
 * `CuModelSelector`, (2) an active REALTIME provider is ignored when
 * resolving "the active Brain provider", and (3) with no active Brain
 * provider the panel shows a hint instead of the selector.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

// Stub CuModelSelector so this test asserts exactly what the panel passes it
// (providerId / recommendedModel), without pulling in its own network calls —
// that component has its own tests.
vi.mock("@/components/CuModelSelector", () => ({
  CuModelSelector: ({
    providerId,
    recommendedModel,
  }: {
    providerId: string;
    recommendedModel?: string | null;
  }) => (
    <div
      data-testid="cu-model-selector-stub"
      data-provider-id={providerId}
      data-recommended-model={recommendedModel ?? ""}
    />
  ),
}));

let mockProviders: Array<Record<string, unknown>> = [];
vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => ({
    providers: mockProviders,
    loading: false,
    error: null,
    refetch: vi.fn(),
    setActiveOptimistic: vi.fn(),
  }),
}));

import { RealtimeComputerUsePanel } from "@/components/RealtimeComputerUsePanel";

afterEach(() => {
  cleanup();
  mockProviders = [];
});

function brainProvider(overrides: Record<string, unknown> = {}) {
  return {
    id: "openrouter",
    label: "OpenRouter",
    tier: "brain",
    active: true,
    recommended_model: "some-model",
    ...overrides,
  };
}

function realtimeProvider(overrides: Record<string, unknown> = {}) {
  return {
    id: "openai-realtime",
    label: "OpenAI Realtime",
    tier: "realtime",
    active: true,
    ...overrides,
  };
}

describe("RealtimeComputerUsePanel", () => {
  it("names the active Brain provider and passes the BRAIN id + recommended model to CuModelSelector", () => {
    mockProviders = [brainProvider(), realtimeProvider()];
    render(<RealtimeComputerUsePanel />);

    expect(screen.getByTestId("realtime-cu-panel").textContent).toMatch(/OpenRouter/);
    const stub = screen.getByTestId("cu-model-selector-stub");
    expect(stub.getAttribute("data-provider-id")).toBe("openrouter");
    expect(stub.getAttribute("data-recommended-model")).toBe("some-model");
  });

  it("ignores an active REALTIME provider — only the active BRAIN provider resolves", () => {
    mockProviders = [
      brainProvider({ id: "gemini", label: "Gemini", active: true }),
      realtimeProvider({ active: true }),
    ];
    render(<RealtimeComputerUsePanel />);

    const stub = screen.getByTestId("cu-model-selector-stub");
    expect(stub.getAttribute("data-provider-id")).toBe("gemini");
    expect(stub.getAttribute("data-provider-id")).not.toBe("openai-realtime");
  });

  it("shows a hint pointing back to Pipeline -> Brain instead of CuModelSelector when no Brain provider is active", () => {
    mockProviders = [brainProvider({ active: false }), realtimeProvider()];
    render(<RealtimeComputerUsePanel />);

    expect(screen.queryByTestId("cu-model-selector-stub")).toBeNull();
    const hint = screen.getByTestId("realtime-cu-no-brain-hint");
    expect(hint.textContent).toMatch(/Brain/i);
  });

  it("shows the hint when there are no providers at all", () => {
    mockProviders = [];
    render(<RealtimeComputerUsePanel />);

    expect(screen.queryByTestId("cu-model-selector-stub")).toBeNull();
    expect(screen.getByTestId("realtime-cu-no-brain-hint")).toBeTruthy();
  });
});
