/**
 * A bar that says LISTENING over a microphone nobody is holding is the bug
 * this hook exists for: `SystemStateChanged` is one-shot, so a window that
 * missed the closing transition keeps the last state forever. These tests pin
 * the three properties that make the correction safe — it only moves the store
 * to idle, an unreadable answer changes nothing, and a live event beats the
 * snapshot that was already in flight.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useVoiceStateResync } from "@/hooks/useVoiceStateResync";
import { useEventStore } from "@/store/events";

function Harness() {
  useVoiceStateResync();
  return null;
}

function answer(body: unknown, ok = true) {
  return vi.fn(async () => ({ ok, json: async () => body })) as unknown as typeof fetch;
}

describe("useVoiceStateResync", () => {
  beforeEach(() => {
    useEventStore.setState({
      connected: true,
      voiceState: "listening",
      transcription: "left over from the dead session",
      transcriptionFinal: false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    useEventStore.setState({ voiceState: "idle", transcription: "", transcriptionFinal: true });
  });

  it("drops a stale listening state when the backend reports no session", async () => {
    vi.stubGlobal("fetch", answer({ available: true, state: "idle", voice_state: "idle" }));

    render(<Harness />);

    await waitFor(() => expect(useEventStore.getState().voiceState).toBe("idle"));
    // The frozen live transcript goes with it — same session boundary.
    expect(useEventStore.getState().transcription).toBe("");
    expect(fetch).toHaveBeenCalledWith("/api/voice/state", { cache: "no-store" });
  });

  it("leaves a running call alone", async () => {
    vi.stubGlobal(
      "fetch",
      answer({ available: true, state: "active", voice_state: "listening" }),
    );

    render(<Harness />);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(useEventStore.getState().voiceState).toBe("listening");
  });

  it("asks nothing at all while the store is already idle", async () => {
    useEventStore.setState({ voiceState: "idle" });
    vi.stubGlobal("fetch", answer({ available: true, state: "idle", voice_state: "idle" }));

    render(<Harness />);

    await waitFor(() => expect(useEventStore.getState().voiceState).toBe("idle"));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("changes nothing when the backend cannot be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }) as unknown as typeof fetch,
    );

    render(<Harness />);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(useEventStore.getState().voiceState).toBe("listening");
  });

  it("yields to a live event that arrived while the snapshot was in flight", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        // The next call starts before this answer is read — exactly the race.
        useEventStore.getState().setVoice("speaking");
        return { ok: true, json: async () => ({ available: true, state: "idle", voice_state: "idle" }) };
      }) as unknown as typeof fetch,
    );

    render(<Harness />);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(useEventStore.getState().voiceState).toBe("speaking");
  });

  it("still corrects against a backend that predates the fine-grained field", async () => {
    vi.stubGlobal("fetch", answer({ available: true, state: "idle" }));

    render(<Harness />);

    await waitFor(() => expect(useEventStore.getState().voiceState).toBe("idle"));
  });

  it("leaves a call alone when an older backend only says the pipeline is active", async () => {
    vi.stubGlobal("fetch", answer({ available: true, state: "active" }));

    render(<Harness />);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(useEventStore.getState().voiceState).toBe("listening");
  });

  it("waits for the socket before correcting anything", async () => {
    useEventStore.setState({ connected: false });
    vi.stubGlobal("fetch", answer({ available: true, state: "idle", voice_state: "idle" }));

    render(<Harness />);

    await waitFor(() => expect(useEventStore.getState().voiceState).toBe("listening"));
    expect(fetch).not.toHaveBeenCalled();
  });
});
