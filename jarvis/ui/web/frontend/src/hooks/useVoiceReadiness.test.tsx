/**
 * useVoiceReadiness is the single source of truth for "can the user speak yet?".
 * Before it existed, the sidebar, the warming banner and the chat empty-state
 * each derived readiness on their own (and the empty-state ignored it), so the
 * banner could say "starting up" while the centre said "Ready for commands".
 * These tests lock the four readiness states.
 */
import { renderHook, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useEventStore } from "@/store/events";

function set(connected: boolean, wsWarming: boolean, voiceReady: boolean) {
  useEventStore.setState({ connected, wsWarming, voiceReady });
}

describe("useVoiceReadiness (single source of truth)", () => {
  afterEach(() => cleanup());

  it("warms while the socket is up but voice is not ready yet", () => {
    set(true, false, false);
    const { result } = renderHook(() => useVoiceReadiness());
    expect(result.current.warming).toBe(true);
    expect(result.current.voiceWarming).toBe(true);
    expect(result.current.bootWarming).toBe(false);
    expect(result.current.ready).toBe(false);
  });

  it("warms while the fast-boot socket is still binding (not connected)", () => {
    set(false, true, false);
    const { result } = renderHook(() => useVoiceReadiness());
    expect(result.current.warming).toBe(true);
    expect(result.current.bootWarming).toBe(true);
    expect(result.current.voiceWarming).toBe(false);
    expect(result.current.ready).toBe(false);
  });

  it("is ready only when connected AND voice is ready", () => {
    set(true, false, true);
    const { result } = renderHook(() => useVoiceReadiness());
    expect(result.current.warming).toBe(false);
    expect(result.current.ready).toBe(true);
  });

  it("is offline (not warming, not ready) when disconnected and not warming", () => {
    set(false, false, false);
    const { result } = renderHook(() => useVoiceReadiness());
    expect(result.current.warming).toBe(false);
    expect(result.current.ready).toBe(false);
    expect(result.current.connected).toBe(false);
  });
});
