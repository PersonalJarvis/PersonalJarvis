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
