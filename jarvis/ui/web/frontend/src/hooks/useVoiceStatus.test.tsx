/**
 * WS events are not persistent: a client that mounts after the backend already
 * reported voice readiness would miss the VoiceBootStatus frame. useVoiceStatus
 * seeds the initial value once on mount via GET /api/voice/status (mirrors
 * useBrainStatus). This test stubs fetch and asserts the seed lands in the store.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useVoiceStatus } from "@/hooks/useVoiceStatus";
import { useEventStore } from "@/store/events";

function Harness() {
  useVoiceStatus();
  return null;
}

describe("useVoiceStatus mount seed", () => {
  beforeEach(() => {
    useEventStore.setState({ voiceReady: false });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    // stubGlobal is NOT reverted by restoreAllMocks — without this the fetch
    // stub would leak into later suites (cross-suite flake).
    vi.unstubAllGlobals();
  });

  it("seeds voiceReady from GET /api/voice/status { ready: true }", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ ready: true }),
      })) as unknown as typeof fetch,
    );

    render(<Harness />);

    await waitFor(() => expect(useEventStore.getState().voiceReady).toBe(true));
    expect(fetch).toHaveBeenCalledWith(
      "/api/voice/status",
      expect.objectContaining({ signal: expect.anything() }),
    );
  });

  it("leaves voiceReady false when the endpoint reports not ready", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ ready: false }),
      })) as unknown as typeof fetch,
    );

    render(<Harness />);

    // Give the fetch microtasks a chance to run, then assert it stayed false.
    await Promise.resolve();
    await Promise.resolve();
    expect(useEventStore.getState().voiceReady).toBe(false);
  });

  it("keeps the current value when the fetch fails (offline / headless)", async () => {
    useEventStore.setState({ voiceReady: false });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }) as unknown as typeof fetch,
    );

    render(<Harness />);
    await Promise.resolve();

    expect(useEventStore.getState().voiceReady).toBe(false);
  });
});
