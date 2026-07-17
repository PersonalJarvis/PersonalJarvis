/**
 * The header wordmark + every assistant byline read `assistantName` from the
 * store. useAssistantNameSeed fills it once on mount via GET
 * /api/settings/assistant-name and refreshes it on a Settings rename
 * (jarvis:assistant-name-changed). This test stubs fetch and asserts the seed
 * + the live-refresh path land in the store.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAssistantNameSeed } from "@/hooks/useAssistantNameSeed";
import { useEventStore } from "@/store/events";

function Harness() {
  useAssistantNameSeed();
  return null;
}

describe("useAssistantNameSeed mount seed", () => {
  beforeEach(() => {
    localStorage.clear();
    useEventStore.setState({ assistantName: "Assistant" });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("seeds assistantName from GET /api/settings/assistant-name { resolved }", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ name: "Ruben", resolved: "Ruben", default: "Assistant" }),
      })) as unknown as typeof fetch,
    );

    render(<Harness />);

    await waitFor(() => expect(useEventStore.getState().assistantName).toBe("Ruben"));
    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/assistant-name",
      expect.objectContaining({ signal: expect.anything() }),
    );
  });

  it("refreshes on the jarvis:assistant-name-changed event", async () => {
    let resolved = "Ruben";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ resolved }),
      })) as unknown as typeof fetch,
    );

    render(<Harness />);
    await waitFor(() => expect(useEventStore.getState().assistantName).toBe("Ruben"));

    resolved = "Athena";
    window.dispatchEvent(new CustomEvent("jarvis:assistant-name-changed"));

    await waitFor(() => expect(useEventStore.getState().assistantName).toBe("Athena"));
  });

  it("keeps the current value when the fetch fails (offline / headless)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }) as unknown as typeof fetch,
    );

    render(<Harness />);
    await Promise.resolve();

    expect(useEventStore.getState().assistantName).toBe("Assistant");
  });

  it("retries after a failed boot fetch and seeds once the backend is up", async () => {
    // The autostart race: the app mounts while the backend is still binding,
    // the first fetch throws, a later retry succeeds. The neutral fallback
    // must not stick until a manual reload.
    vi.useFakeTimers();
    try {
      let calls = 0;
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          calls += 1;
          if (calls === 1) throw new Error("backend not up yet");
          return {
            ok: true,
            json: async () => ({ resolved: "Nova", default: "Assistant" }),
          };
        }) as unknown as typeof fetch,
      );

      render(<Harness />);
      // Let the first (failing) fetch settle and schedule its retry.
      await vi.advanceTimersByTimeAsync(0);
      expect(useEventStore.getState().assistantName).toBe("Assistant");

      // First backoff step is 1s — advancing past it fires the retry.
      await vi.advanceTimersByTimeAsync(1_100);
      expect(useEventStore.getState().assistantName).toBe("Nova");
    } finally {
      vi.useRealTimers();
    }
  });

  it("retries after a non-OK warmup response (503) instead of giving up", async () => {
    vi.useFakeTimers();
    try {
      let calls = 0;
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          calls += 1;
          if (calls === 1) return { ok: false, status: 503 };
          return {
            ok: true,
            json: async () => ({ resolved: "Nova", default: "Assistant" }),
          };
        }) as unknown as typeof fetch,
      );

      render(<Harness />);
      await vi.advanceTimersByTimeAsync(0);
      expect(useEventStore.getState().assistantName).toBe("Assistant");

      await vi.advanceTimersByTimeAsync(1_100);
      expect(useEventStore.getState().assistantName).toBe("Nova");
    } finally {
      vi.useRealTimers();
    }
  });

  it("never caches the neutral fallback name in localStorage", async () => {
    // A resolved "Assistant" (no wake word configured, or a warmup artifact)
    // must not be persisted: an empty cache already yields the fallback, and
    // persisting it would poison later boots that DO have a real name.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ resolved: "Assistant", default: "Assistant" }),
      })) as unknown as typeof fetch,
    );

    render(<Harness />);
    await waitFor(() =>
      expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0),
    );
    await Promise.resolve();
    await Promise.resolve();

    expect(localStorage.getItem("jarvis.assistantName")).toBeNull();
  });

  it("ignores an empty resolved value (does not blank the wordmark)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ resolved: "   " }),
      })) as unknown as typeof fetch,
    );

    render(<Harness />);
    await Promise.resolve();
    await Promise.resolve();

    expect(useEventStore.getState().assistantName).toBe("Assistant");
  });

  it("caches the resolved name in localStorage for an instant next-boot paint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ resolved: "Nico" }),
      })) as unknown as typeof fetch,
    );

    render(<Harness />);

    await waitFor(() => expect(useEventStore.getState().assistantName).toBe("Nico"));
    expect(localStorage.getItem("jarvis.assistantName")).toBe("Nico");
  });
});
