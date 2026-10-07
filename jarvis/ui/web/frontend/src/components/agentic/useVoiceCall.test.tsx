/**
 * The one start/stop path every voice surface shares. A start always asks the
 * host first; only a host with no speech pipeline at all (a VPS, `jarvis
 * serve`) that says so hands the call to this browser (issue #399).
 */
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";
import { useVoiceCall } from "./useVoiceCall";

const fakes = vi.hoisted(() => ({
  requestVoiceCall: vi.fn(),
  requestVoiceHangup: vi.fn(),
  fetchVoiceRuntimeState: vi.fn(),
  startBrowserVoiceCall: vi.fn(),
  stopBrowserVoiceCall: vi.fn(),
}));

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

vi.mock("@/lib/voiceApi", () => ({
  requestVoiceCall: () => fakes.requestVoiceCall(),
  requestVoiceHangup: () => fakes.requestVoiceHangup(),
  fetchVoiceRuntimeState: () => fakes.fetchVoiceRuntimeState(),
}));

vi.mock("@/lib/browserVoiceCall", () => ({
  startBrowserVoiceCall: () => fakes.startBrowserVoiceCall(),
  stopBrowserVoiceCall: () => fakes.stopBrowserVoiceCall(),
}));

const toast = vi.fn();

function noPipeline(): Error {
  return Object.assign(new Error("Voice is not running on this computer."), { status: 503 });
}

describe("useVoiceCall", () => {
  beforeEach(() => {
    for (const fake of Object.values(fakes)) fake.mockReset();
    fakes.requestVoiceCall.mockResolvedValue({ armed: true });
    fakes.requestVoiceHangup.mockResolvedValue({ stopped: true });
    fakes.startBrowserVoiceCall.mockReturnValue(true);
    fakes.stopBrowserVoiceCall.mockReturnValue(false);
    toast.mockClear();
    useEventStore.setState({ voiceState: "idle", pushToast: toast });
  });

  afterEach(() => {
    cleanup();
  });

  it("asks the host and nothing else when the host arms the call", async () => {
    const { result } = renderHook(() => useVoiceCall());

    await act(() => result.current.toggleCall());

    expect(fakes.requestVoiceCall).toHaveBeenCalledTimes(1);
    expect(fakes.fetchVoiceRuntimeState).not.toHaveBeenCalled();
    expect(fakes.startBrowserVoiceCall).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
  });

  it("lets this browser hold the call on a host with no speech pipeline", async () => {
    fakes.requestVoiceCall.mockRejectedValue(noPipeline());
    fakes.fetchVoiceRuntimeState.mockResolvedValue({
      available: false,
      voiceState: "idle",
      browserCall: true,
    });
    const { result } = renderHook(() => useVoiceCall());

    await act(() => result.current.toggleCall());

    expect(fakes.startBrowserVoiceCall).toHaveBeenCalledTimes(1);
    expect(toast).not.toHaveBeenCalled();
  });

  it("keeps the honest error when the host does not offer a browser call", async () => {
    fakes.requestVoiceCall.mockRejectedValue(noPipeline());
    fakes.fetchVoiceRuntimeState.mockResolvedValue({
      available: false,
      voiceState: "idle",
      browserCall: false,
    });
    const { result } = renderHook(() => useVoiceCall());

    await act(() => result.current.toggleCall());

    expect(fakes.startBrowserVoiceCall).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith("error", "Voice is not running on this computer.");
  });

  it("never reads another failure as a missing pipeline", async () => {
    fakes.requestVoiceCall.mockRejectedValue(
      Object.assign(new Error("Voice request failed (500)."), { status: 500 }),
    );
    const { result } = renderHook(() => useVoiceCall());

    await act(() => result.current.toggleCall());

    expect(fakes.fetchVoiceRuntimeState).not.toHaveBeenCalled();
    expect(fakes.startBrowserVoiceCall).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith("error", "Voice request failed (500).");
  });

  it("ends a call this browser holds without asking the host", async () => {
    useEventStore.setState({ voiceState: "listening" });
    fakes.stopBrowserVoiceCall.mockReturnValue(true);
    const { result } = renderHook(() => useVoiceCall());

    await act(() => result.current.toggleCall());

    expect(fakes.stopBrowserVoiceCall).toHaveBeenCalledTimes(1);
    expect(fakes.requestVoiceHangup).not.toHaveBeenCalled();
  });

  it("hangs up through the host when this browser holds no call", async () => {
    useEventStore.setState({ voiceState: "speaking" });
    const { result } = renderHook(() => useVoiceCall());

    await act(() => result.current.toggleCall());

    expect(fakes.requestVoiceHangup).toHaveBeenCalledTimes(1);
  });
});
