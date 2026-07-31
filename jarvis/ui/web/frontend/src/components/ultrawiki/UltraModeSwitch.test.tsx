/**
 * UltraModeSwitch — the one-time wizard stays one-time (regression).
 *
 * The gate used to be `status.slots.embedding.provider`, which only exists
 * once the UltraWiki service is running. Every restart therefore looked like
 * a fresh install: the wizard reopened, asking again for the storage backend
 * and — the expensive part — the embedding model, whose re-pick re-embeds the
 * whole corpus. The gate is now the backend's `configured` flag, answered
 * from the stored config and true from the first `/status` of a session.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { UltraModeSwitch } from "@/components/ultrawiki/UltraModeSwitch";
import type { UltraWikiStatus } from "@/lib/ultrawikiApi";

const activateSpy = vi.fn(async () => ({ ok: true, next_steps: "" }));

vi.mock("@/lib/ultrawikiApi", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    activateUltraWiki: (...args: unknown[]) => activateSpy(...(args as [])),
    deactivateUltraWiki: async () => ({ ok: true }),
  };
});

// The wizard is lazy-loaded; a stub keeps this test about the GATE.
vi.mock("@/components/ultrawiki/ActivationWizard", () => ({
  ActivationWizard: () => <div data-testid="wizard-stub" />,
}));

function status(overrides: Partial<UltraWikiStatus>): UltraWikiStatus {
  return {
    enabled: false,
    configured: true,
    started: false,
    db_backend: "sqlite",
    backend_in_use: "",
    slots: {
      embedding: {
        provider: "gemini",
        model: "gemini-embedding-001",
        ready: true,
        reason: "",
      },
    },
    counts: {},
    pipeline: { running: false, processed: {} },
    sources: [],
    jobs: [],
    search_legs: {},
    degradations: [],
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  activateSpy.mockClear();
});

describe("UltraModeSwitch — the activation wizard", () => {
  it("re-activates with the stored choices instead of reopening the wizard", async () => {
    render(<UltraModeSwitch status={status({})} onModeChanged={() => {}} />);

    fireEvent.click(screen.getByTestId("wiki-mode-ultra"));

    await waitFor(() => {
      expect(activateSpy).toHaveBeenCalledTimes(1);
    });
    // Only the provider travels: every other stored value — model above all —
    // stays exactly as the user picked it, so nothing is re-embedded.
    expect(activateSpy).toHaveBeenCalledWith({ embedding_provider: "gemini" });
    expect(screen.queryByTestId("wizard-stub")).toBeNull();
  });

  it("still re-activates when the service has not reported its slots yet", async () => {
    // What a booting backend answers: the mode and the stored choices are
    // known, the live readiness probes are not.
    render(
      <UltraModeSwitch
        status={status({
          slots: {
            embedding: {
              provider: "gemini",
              model: "gemini-embedding-001",
              ready: false,
              reason: "the UltraWiki service has not started yet",
            },
          },
        })}
        onModeChanged={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("wiki-mode-ultra"));

    await waitFor(() => {
      expect(activateSpy).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByTestId("wizard-stub")).toBeNull();
  });

  it("opens the wizard on a genuinely unconfigured install", async () => {
    render(
      <UltraModeSwitch
        status={status({ configured: false, slots: {} })}
        onModeChanged={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("wiki-mode-ultra"));

    await waitFor(() => {
      expect(screen.getByTestId("wizard-stub")).toBeDefined();
    });
    expect(activateSpy).not.toHaveBeenCalled();
  });

  it("asks again instead of opening the wizard when the status is unknown", async () => {
    const onModeChanged = vi.fn();
    render(<UltraModeSwitch status={null} onModeChanged={onModeChanged} />);

    fireEvent.click(screen.getByTestId("wiki-mode-ultra"));

    await waitFor(() => {
      expect(onModeChanged).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("wizard-stub")).toBeNull();
    expect(activateSpy).not.toHaveBeenCalled();
  });
});
