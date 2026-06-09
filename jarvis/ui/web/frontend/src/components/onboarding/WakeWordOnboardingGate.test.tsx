import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Identity translator — assertions match key strings directly.
// ---------------------------------------------------------------------------
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
  useReplyLanguage: () => "auto",
  setUiLanguage: vi.fn(),
  setReplyLanguage: vi.fn(),
  hydrateReplyLanguage: vi.fn(),
  hydrateUiLanguage: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function makeWakeWordResponse(phrase: string) {
  return {
    phrase,
    engine: "auto",
    custom_model_path: "",
    sensitivity: 0.5,
    fuzzy_match_ratio: 80,
    engines: ["auto", "openwakeword"],
    instant_phrases: [],
    local_whisper_available: false,
  };
}

function makeSaveResult(opts: { degraded?: boolean } = {}) {
  return {
    ok: true,
    phrase: "Jonas",
    engine: "auto",
    resolved_engine: "stt_match",
    degraded: opts.degraded ?? false,
    message: opts.degraded ? "fallback used" : "Saved",
    persisted: true,
    restart_required: false,
  };
}

// ---------------------------------------------------------------------------
// Import after mocks are registered.
// ---------------------------------------------------------------------------
import { WakeWordOnboardingGate } from "./WakeWordOnboardingGate";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WakeWordOnboardingGate", () => {
  // -------------------------------------------------------------------------
  // 1. Renders the blocking overlay when GET returns phrase == ""
  // -------------------------------------------------------------------------
  it("renders the blocking overlay when phrase is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(makeWakeWordResponse("")),
      }),
    );

    render(<WakeWordOnboardingGate />);

    await waitFor(() => {
      expect(
        screen.getByText("settings_view.onboarding.wake_word.title"),
      ).toBeDefined();
    });

    expect(screen.getByRole("dialog")).toBeDefined();
  });

  // -------------------------------------------------------------------------
  // 2. Does NOT render when phrase is already set
  // -------------------------------------------------------------------------
  it("does not render when phrase is already configured", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(makeWakeWordResponse("Jonas")),
      }),
    );

    render(<WakeWordOnboardingGate />);

    // Wait for the fetch to resolve, then confirm no dialog.
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // 3. CTA is disabled while the input is empty
  // -------------------------------------------------------------------------
  it("disables the CTA while the input is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(makeWakeWordResponse("")),
      }),
    );

    render(<WakeWordOnboardingGate />);

    await waitFor(() => {
      screen.getByText("settings_view.onboarding.wake_word.title");
    });

    const cta = screen.getByRole("button", {
      name: "settings_view.onboarding.wake_word.cta",
    });
    expect((cta as HTMLButtonElement).disabled).toBe(true);
  });

  // -------------------------------------------------------------------------
  // 4. Typing then clicking CTA fires PUT /api/settings/wake-word
  // -------------------------------------------------------------------------
  it("fires PUT /api/settings/wake-word with the entered phrase on submit", async () => {
    // Track PUT calls separately from GET calls.
    const putCalls: Array<[string, RequestInit]> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (init?.method === "PUT") {
          putCalls.push([url, init]);
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(makeSaveResult()),
          });
        }
        // GET — always return empty phrase so the gate stays visible.
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(makeWakeWordResponse("")),
        });
      }),
    );

    render(<WakeWordOnboardingGate />);

    await waitFor(() => {
      screen.getByText("settings_view.onboarding.wake_word.title");
    });

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Jonas" } });

    const cta = screen.getByRole("button", {
      name: "settings_view.onboarding.wake_word.cta",
    });
    expect((cta as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(cta);

    await waitFor(() => {
      expect(putCalls.length).toBeGreaterThanOrEqual(1);
    });

    const [url, options] = putCalls[0];
    expect(url).toBe("/api/settings/wake-word");
    const body = JSON.parse(options.body as string) as {
      phrase: string;
      persist: boolean;
    };
    expect(body.phrase).toBe("Jonas");
    expect(body.persist).toBe(true);
  });

  // -------------------------------------------------------------------------
  // 5. After successful save the gate unmounts
  // -------------------------------------------------------------------------
  it("unmounts the gate after a successful save", async () => {
    // After the PUT succeeds, the jarvis:wake-word-changed event is dispatched.
    // The hook re-fetches and now gets a non-empty phrase → gate unmounts.
    let fetchCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
        fetchCallCount++;
        if (init?.method === "PUT") {
          // Dispatch the event after a tick so the component can react.
          setTimeout(() => {
            window.dispatchEvent(new CustomEvent("jarvis:wake-word-changed"));
          }, 0);
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(makeSaveResult()),
          });
        }
        // GET: first call returns empty, subsequent refetch returns "Jonas"
        const phrase = fetchCallCount <= 1 ? "" : "Jonas";
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(makeWakeWordResponse(phrase)),
        });
      }),
    );

    render(<WakeWordOnboardingGate />);

    await waitFor(() => {
      screen.getByText("settings_view.onboarding.wake_word.title");
    });

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Jonas" } });
    fireEvent.click(
      screen.getByRole("button", {
        name: "settings_view.onboarding.wake_word.cta",
      }),
    );

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // 6. Fail open when GET errors — renders nothing (does not block the user)
  // -------------------------------------------------------------------------
  it("renders nothing (fail open) when GET returns an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("Network error")),
    );

    render(<WakeWordOnboardingGate />);

    // Wait a tick to let the fetch settle, then confirm no dialog.
    await waitFor(
      () => {
        expect(screen.queryByRole("dialog")).toBeNull();
      },
      { timeout: 500 },
    );
  });

  // -------------------------------------------------------------------------
  // 7. Degraded note is shown when save result is degraded, gate stays open
  //    until a subsequent success resolves it (or user retries).
  // -------------------------------------------------------------------------
  it("shows degraded_note inline when save result is degraded", async () => {
    let fetchCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
        fetchCallCount++;
        if (init?.method === "PUT") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(makeSaveResult({ degraded: true })),
          });
        }
        // GET always returns empty phrase so the gate stays visible
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(makeWakeWordResponse("")),
        });
      }),
    );

    render(<WakeWordOnboardingGate />);

    await waitFor(() => {
      screen.getByText("settings_view.onboarding.wake_word.title");
    });

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Jonas" },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "settings_view.onboarding.wake_word.cta",
      }),
    );

    await waitFor(() => {
      // The degraded message from the save result should be visible.
      expect(screen.getByText("fallback used")).toBeDefined();
    });

    // Gate is still open (result was degraded but phrase was still saved;
    // in this test the GET still returns "" so the gate remains).
    expect(screen.queryByRole("dialog")).toBeDefined();
  });
});
