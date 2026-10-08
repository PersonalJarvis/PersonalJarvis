import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";
import {
  browserVoiceCallLive,
  startBrowserVoiceCall,
  stopBrowserVoiceCall,
} from "@/lib/browserVoiceCall";
import {
  setBrowserVoiceInputOwnership,
  setVoiceInputLevel,
  voiceInputLevelRef,
} from "@/lib/voiceInputLevel";

import { BrowserRealtimeControl, waveformPhase } from "./BrowserRealtimeControl";

const fakes = vi.hoisted(() => ({
  prepareAudio: vi.fn(() => vi.fn()),
  native: false,
  mode: "realtime",
  available: true,
  requiresWebRtcOffer: false,
  browserAudio: false,
  connect: vi.fn(async () => undefined),
  disconnect: vi.fn(async () => undefined),
  supportIssue: null as
    | null
    | "secure_context"
    | "microphone_unavailable"
    | "audio_worklet_unavailable",
  callbacks: null as null | {
    onAudio?: () => void;
    onInputLevel?: (level: number) => void;
    onStatus?: (status: string, payload: Record<string, unknown>) => void;
  },
  options: null as null | { requiresWebRtcOffer?: boolean; browserAudio?: boolean },
}));

vi.mock("@/lib/realtimeAudioPreparation", () => ({ registerRealtimeAudioPreparation: fakes.prepareAudio }));

vi.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ data: { native_file_actions: fakes.native, platform: "linux" } }),
}));

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: fakes.mode,
    realtimeAvailable: fakes.available,
    requiresWebRtcOffer: fakes.requiresWebRtcOffer,
    browserAudio: fakes.browserAudio,
    setMode: vi.fn(),
    isLoading: false,
    isSaving: false,
  }),
}));

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

vi.mock("@/lib/realtimeAudio", () => ({
  browserRealtimeSupportIssue: () => fakes.supportIssue,
  RealtimeAudioSupportError: class extends Error {},
  RealtimeAudioClient: class {
    constructor(
      callbacks: NonNullable<typeof fakes.callbacks>,
      options: NonNullable<typeof fakes.options>,
    ) {
      fakes.callbacks = callbacks;
      fakes.options = options;
    }

    connect = fakes.connect;
    disconnect = fakes.disconnect;
  },
}));

describe("BrowserRealtimeControl", () => {
  beforeEach(() => {
    fakes.prepareAudio.mockClear();
    fakes.native = false;
    fakes.mode = "realtime";
    fakes.available = true;
    fakes.requiresWebRtcOffer = false;
    fakes.browserAudio = false;
    fakes.connect.mockClear();
    fakes.disconnect.mockClear();
    fakes.supportIssue = null;
    fakes.callbacks = null;
    fakes.options = null;
    setBrowserVoiceInputOwnership(false);
    delete (window as unknown as { pywebview?: unknown }).pywebview;
    useEventStore.setState({
      events: [],
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      solo: false,
      activeSection: "chats",
      detachedViews: [],
    });
  });

  it("prepares only the enabled voice owner's local audio and disposes it when disabled", () => {
    fakes.native = true;
    fakes.browserAudio = true;
    (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
    const view = render(<BrowserRealtimeControl controlOnly />);
    expect(fakes.prepareAudio).toHaveBeenCalledOnce();
    const dispose = fakes.prepareAudio.mock.results[0].value;
    fakes.mode = "pipeline";
    view.rerender(<BrowserRealtimeControl controlOnly />);
    expect(dispose).toHaveBeenCalledOnce();
    expect(fakes.connect).not.toHaveBeenCalled();
  });

  it("does not prepare in a detached non-owner desktop view or unavailable mode", () => {
    fakes.native = true;
    fakes.browserAudio = true;
    (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
    useEventStore.setState({ solo: true, activeSection: "settings" });
    const view = render(<BrowserRealtimeControl controlOnly />);
    expect(fakes.prepareAudio).not.toHaveBeenCalled();
    fakes.available = false;
    view.rerender(<BrowserRealtimeControl />);
    expect(fakes.prepareAudio).not.toHaveBeenCalled();
  });

  it("offers a browser recovery link when the hidden media host cannot start", async () => {
    fakes.browserAudio = true;
    fakes.connect.mockRejectedValueOnce(new Error("Microphone unavailable"));
    useEventStore.setState({ events: [{
      id: "live-start-test", name: "BrowserVoiceRequested", ts: Date.now(),
      payload: { action: "start" },
    }] });
    render(<BrowserRealtimeControl controlOnly />);
    expect(await screen.findByRole("link", { name: "live.open_browser" })).toBeTruthy();
  });

  describe("correlated native startup failure", () => {
    const firstRequest = "1a468a93-a7c3-43f6-a95e-7b1a6b49c28b";
    const secondRequest = "4fd86de5-0c5b-4351-8871-fb43ab708ac7";
    let fetchSpy: ReturnType<typeof vi.fn>;
    const request = (id: string, action: string, requestId?: string) => act(() => useEventStore.getState().pushEvent({
      id, name: "BrowserVoiceRequested", ts: Date.now(),
      payload: { action, ...(requestId ? { request_id: requestId } : {}) },
    }));
    beforeEach(() => {
      fakes.browserAudio = true;
      fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
      vi.stubGlobal("fetch", fetchSpy);
    });
    afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

    it.each([200, 404])("reports only the current request once and preserves its error (HTTP %s)", async status => {
      fetchSpy.mockResolvedValueOnce(new Response("{}", { status }));
      fakes.connect.mockRejectedValueOnce(new Error("Local audio device did not become ready"));
      render(<BrowserRealtimeControl controlOnly />);
      request("native-failure", "start", firstRequest);
      await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
      expect(fetchSpy.mock.calls[0]).toEqual(["/api/voice/startup-failed", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: firstRequest }),
      }]);
      expect(screen.getByRole("alert").textContent).toContain("sidebar.realtime_error");
      act(() => fakes.callbacks?.onStatus?.("disconnected", { reason: "late close" }));
      expect(fetchSpy).toHaveBeenCalledOnce();
    });

    it("preserves request correlation while a hidden browser waits for visibility", async () => {
      const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      fakes.connect.mockRejectedValueOnce(new Error("Device failed"));
      render(<BrowserRealtimeControl controlOnly />);
      request("hidden-failure", "start", firstRequest);
      expect(fakes.connect).not.toHaveBeenCalled();
      visibility.mockReturnValue("visible");
      act(() => document.dispatchEvent(new Event("visibilitychange")));
      await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
      expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ request_id: firstRequest });
    });

    it("ignores an old rejected attempt and its stop after a newer call starts", async () => {
      let rejectOld!: (error: Error) => void;
      fakes.connect.mockImplementationOnce(() => new Promise<undefined>((_resolve, reject) => { rejectOld = reject; }));
      render(<BrowserRealtimeControl controlOnly />);
      request("old-start", "start", firstRequest);
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
      request("old-stop", "stop", firstRequest);
      await waitFor(() => expect(fakes.disconnect).toHaveBeenCalledTimes(1));
      request("new-start", "start", secondRequest);
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(2));
      await act(async () => { rejectOld(new Error("Late old device failure")); });
      request("late-old-stop", "stop", firstRequest);
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(fakes.disconnect).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("alert")).toBeNull();
      request("new-stop", "stop", secondRequest);
      await waitFor(() => expect(fakes.disconnect).toHaveBeenCalledTimes(2));
    });

    it.each(["resolve", "reject"])("replaces a pending native request without an old stop and ignores its late %s", async settle => {
      let resolveOld!: () => void, rejectOld!: (error: Error) => void, rejectNew!: (error: Error) => void;
      fakes.connect.mockImplementationOnce(() => new Promise<undefined>((resolve, reject) => {
        resolveOld = () => resolve(undefined);
        rejectOld = reject;
      }));
      fakes.connect.mockImplementationOnce(() => new Promise<undefined>((_resolve, reject) => { rejectNew = reject; }));
      render(<BrowserRealtimeControl controlOnly />);
      request("pending-old-start", "start", firstRequest);
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
      const oldCallbacks = fakes.callbacks;
      request("replacement-start", "start", secondRequest);
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(2));
      expect(fakes.disconnect).toHaveBeenCalledTimes(1);
      await act(async () => {
        if (settle === "resolve") resolveOld(); else rejectOld(new Error("Old attempt failed"));
      });
      act(() => oldCallbacks?.onStatus?.("provider_error", { error: "Late old callback" }));
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(fakes.disconnect).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("alert")).toBeNull();
      await act(async () => { rejectNew(new Error("New attempt failed")); });
      await waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
      expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ request_id: secondRequest });
    });

    it("retires a superseded connecting request while hidden and starts its replacement on visibility", async () => {
      const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
      let finishOld!: () => void;
      fakes.connect.mockImplementationOnce(() => new Promise<undefined>(resolve => { finishOld = () => resolve(undefined); }));
      render(<BrowserRealtimeControl controlOnly />);
      request("visible-old", "start", firstRequest);
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledOnce());
      visibility.mockReturnValue("hidden");
      request("hidden-replacement", "start", secondRequest);
      await waitFor(() => expect(fakes.disconnect).toHaveBeenCalledOnce());
      expect(fakes.connect).toHaveBeenCalledOnce();
      visibility.mockReturnValue("visible");
      act(() => document.dispatchEvent(new Event("visibilitychange")));
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(2));
      await act(async () => { finishOld(); });
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(fakes.disconnect).toHaveBeenCalledOnce();
      request("replacement-stop", "stop", secondRequest);
      await waitFor(() => expect(fakes.disconnect).toHaveBeenCalledTimes(2));
    });

    it("keeps an established native call through reconnecting and preserves its original stop correlation", async () => {
      render(<BrowserRealtimeControl controlOnly />);
      request("established-start", "start", firstRequest);
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledOnce());
      act(() => fakes.callbacks?.onStatus?.("audio_ready", {}));
      act(() => fakes.callbacks?.onStatus?.("reconnecting", {}));
      request("new-during-reconnect", "start", secondRequest);
      await act(async () => undefined);
      expect(fakes.disconnect).not.toHaveBeenCalled();
      expect(fakes.connect).toHaveBeenCalledOnce();
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(useEventStore.getState().voiceState).toBe("connecting");
      request("unrelated-stop", "stop", secondRequest);
      expect(fakes.disconnect).not.toHaveBeenCalled();
      request("original-stop", "stop", firstRequest);
      await waitFor(() => expect(fakes.disconnect).toHaveBeenCalledOnce());
    });

    it("does not acknowledge an uncorrelated older backend request", async () => {
      fakes.connect.mockRejectedValueOnce(new Error("Device failed"));
      render(<BrowserRealtimeControl controlOnly />);
      request("legacy-start", "start");
      await screen.findByRole("alert");
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("does not acknowledge a browser-held user call", async () => {
      fakes.connect.mockRejectedValueOnce(new Error("Device failed"));
      render(<BrowserRealtimeControl controlOnly />);
      act(() => { expect(startBrowserVoiceCall()).toBe(true); });
      await waitFor(() => expect(fakes.disconnect).toHaveBeenCalled());
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(browserVoiceCallLive()).toBe(false);
    });

    it("does not acknowledge a provider failure after native startup succeeded", async () => {
      render(<BrowserRealtimeControl controlOnly />);
      request("connected-start", "start", firstRequest);
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledOnce());
      act(() => fakes.callbacks?.onStatus?.("audio_ready", {}));
      act(() => fakes.callbacks?.onStatus?.("provider_error", { error: "Provider disconnected" }));
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  it("parks a wake start while hidden and fires it when the tab returns", async () => {
    fakes.browserAudio = true;
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    try {
      const { unmount } = render(<BrowserRealtimeControl controlOnly />);
      act(() => {
        useEventStore.setState({ events: [{
          id: "wake-hidden", name: "BrowserVoiceRequested", ts: Date.now(),
          payload: { action: "start" },
        }] });
      });
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
      expect(fakes.connect).not.toHaveBeenCalled();
      visibility.mockReturnValue("visible");
      act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
      unmount();
    } finally {
      visibility.mockRestore();
    }
  });

  it("is hidden in the desktop shell to prevent a second microphone", () => {
    fakes.native = true;
    (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
    render(<BrowserRealtimeControl />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("starts a desktop wake immediately even when the WebView is hidden", async () => {
    fakes.native = true;
    fakes.browserAudio = true;
    (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    try {
      render(<BrowserRealtimeControl controlOnly />);
      act(() => useEventStore.getState().pushEvent({
        id: "background-wake", name: "BrowserVoiceRequested", ts: Date.now(),
        payload: { action: "start" },
      }));
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
    } finally {
      visibility.mockRestore();
    }
  });

  it("handles the latest stop and a second wake without replaying the first", async () => {
    fakes.browserAudio = true;
    render(<BrowserRealtimeControl controlOnly />);
    const request = (id: string, action: string) => act(() => useEventStore.getState().pushEvent({
      id, name: "BrowserVoiceRequested", ts: Date.now(), payload: { action },
    }));
    request("start-1", "start");
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
    request("stop-1", "stop");
    await waitFor(() => expect(fakes.disconnect).toHaveBeenCalledTimes(1));
    request("start-2", "start");
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(2));
  });

  it("does not consume a wake before the provider becomes available", async () => {
    fakes.browserAudio = true;
    fakes.available = false;
    const view = render(<BrowserRealtimeControl controlOnly />);
    act(() => useEventStore.getState().pushEvent({
      id: "early-wake", name: "BrowserVoiceRequested", ts: Date.now(), payload: { action: "start" },
    }));
    expect(fakes.connect).not.toHaveBeenCalled();
    fakes.available = true;
    view.rerender(<BrowserRealtimeControl controlOnly />);
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
  });

  it.each([false, true])("keeps other desktop windows from claiming the microphone (embedded=%s)", async embedded => {
    fakes.native = true;
    fakes.browserAudio = true;
    if (embedded) {
      (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
      useEventStore.setState({ solo: true, activeSection: "settings" });
    }
    render(<BrowserRealtimeControl controlOnly />);
    act(() => useEventStore.getState().pushEvent({
      id: "wrong-owner", name: "BrowserVoiceRequested", ts: Date.now(), payload: { action: "start" },
    }));
    await act(async () => undefined);
    expect(fakes.connect).not.toHaveBeenCalled();
  });

  it("stays visible in external Chrome connected to the desktop backend", () => {
    fakes.native = true;
    render(<BrowserRealtimeControl />);

    expect(screen.getByRole("button", { name: "sidebar.realtime_start" })).toBeTruthy();
  });

  it("is hidden while the classic pipeline is selected", () => {
    fakes.mode = "pipeline";
    render(<BrowserRealtimeControl />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("starts browser-owned realtime audio from an explicit user gesture", async () => {
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
    expect(
      screen.getByRole("button", { name: "sidebar.realtime_stop" }).getAttribute(
        "aria-pressed",
      ),
    ).toBe("true");
  });

  it("requests WebRTC signalling only for a provider that declares it", async () => {
    fakes.requiresWebRtcOffer = true;
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
    expect(fakes.options).toMatchObject({ requiresWebRtcOffer: true, browserAudio: false });
  });

  it("returns to thinking after an interim realtime sentence", async () => {
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    act(() => fakes.callbacks?.onAudio?.());
    expect(useEventStore.getState().voiceState).toBe("speaking");

    act(() => fakes.callbacks?.onStatus?.("thinking", {}));
    expect(useEventStore.getState().voiceState).toBe("thinking");
  });

  it("shows speaking on the live tts_start frame without binary audio", async () => {
    // GPT-Live talks over WebRTC: no PCM sideband, no onAudio — the
    // backend's explicit frame is the only speaking signal the bar gets.
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    act(() => fakes.callbacks?.onStatus?.("thinking", {}));
    expect(useEventStore.getState().voiceState).toBe("thinking");

    act(() => fakes.callbacks?.onStatus?.("tts_start", {}));
    expect(useEventStore.getState().voiceState).toBe("speaking");
  });

  it("returns to listening when the live session clears audio on barge-in", async () => {
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    act(() => fakes.callbacks?.onStatus?.("speaking", {}));
    expect(useEventStore.getState().voiceState).toBe("speaking");

    act(() => fakes.callbacks?.onStatus?.("audio_clear", {}));
    expect(useEventStore.getState().voiceState).toBe("listening");
  });

  it("keeps thinking after a progress surface line finishes speaking", async () => {
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    act(() =>
      fakes.callbacks?.onStatus?.("error_spoken", {
        text: "I'll play that.",
        spoken_kind: "progress",
      }),
    );
    act(() => fakes.callbacks?.onStatus?.("tts_end", {}));
    expect(useEventStore.getState().voiceState).toBe("thinking");

    act(() => fakes.callbacks?.onStatus?.("turn_complete", {}));
    expect(useEventStore.getState().voiceState).toBe("listening");
  });

  it("requires a configured Realtime provider before opening the microphone", () => {
    fakes.available = false;
    render(<BrowserRealtimeControl />);

    const button = screen.getByRole("button", { name: "sidebar.realtime_unavailable" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(fakes.connect).not.toHaveBeenCalled();
  });

  it("shows the live visualizer only once the microphone is actually open", async () => {
    render(<BrowserRealtimeControl />);
    expect(screen.queryByTestId("voice-waveform")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    expect(screen.getByTestId("voice-waveform").getAttribute("data-phase")).toBe(
      "listening",
    );
  });

  it("shares browser microphone samples with the orb", async () => {
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    act(() => fakes.callbacks?.onInputLevel?.(0.64));
    setVoiceInputLevel(1, "native");

    expect(voiceInputLevelRef.current).toBe(0.64);
  });

  it("owns microphone levels only while a browser connection is active", async () => {
    render(<BrowserRealtimeControl />);
    setVoiceInputLevel(0.25, "native");
    expect(voiceInputLevelRef.current).toBe(0.25);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
    act(() => fakes.callbacks?.onInputLevel?.(0.7));
    setVoiceInputLevel(0.9, "native");
    expect(voiceInputLevelRef.current).toBe(0.7);

    act(() => fakes.callbacks?.onStatus?.("provider_error", {}));
    await waitFor(() => expect(fakes.disconnect).toHaveBeenCalledTimes(1));
    setVoiceInputLevel(0.4, "native");
    expect(voiceInputLevelRef.current).toBe(0.4);
  });

  it("ignores a late connect and stale callbacks after unmount", async () => {
    let finishConnect: (() => void) | undefined;
    fakes.connect.mockImplementationOnce(
      () =>
        new Promise<undefined>((resolve) => {
          finishConnect = () => resolve(undefined);
        }),
    );
    const { unmount } = render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    unmount();
    act(() => fakes.callbacks?.onInputLevel?.(0.9));
    await act(async () => {
      finishConnect?.();
      await Promise.resolve();
    });

    expect(voiceInputLevelRef.current).toBe(0);
  });

  it("swaps the measured waveform for the activity sweep once the turn is committed", async () => {
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    act(() => fakes.callbacks?.onStatus?.("thinking", {}));

    expect(screen.getByTestId("voice-waveform").getAttribute("data-phase")).toBe(
      "working",
    );
    expect(screen.getByText(/sidebar\.realtime_working/)).toBeTruthy();
  });

  it("names the transcription while interim words are still arriving", async () => {
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    act(() => {
      useEventStore.setState({
        transcription: "wie spät", // i18n-allow: simulated German interim transcript is the content under test
        transcriptionFinal: false,
      });
    });

    expect(screen.getByText(/sidebar\.realtime_transcribing/)).toBeTruthy();
    // The microphone is still open, so the pill keeps drawing real samples.
    expect(screen.getByTestId("voice-waveform").getAttribute("data-phase")).toBe(
      "listening",
    );
  });

  describe("a refused microphone", () => {
    const MAC_UA =
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)";
    let fetchSpy: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
      vi.stubGlobal("fetch", fetchSpy);
      fakes.connect.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    });
    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it("reports a Mac desktop denial so the permission toast can show, with a desktop sentence", async () => {
      vi.spyOn(navigator, "userAgent", "get").mockReturnValue(MAC_UA);
      (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
      fakes.native = false;
      render(<BrowserRealtimeControl />);
      fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

      expect(await screen.findByText("sidebar.realtime_microphone_denied_desktop")).toBeTruthy();
      expect(screen.queryByText("sidebar.realtime_microphone_denied")).toBeNull();
      await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
      const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("/api/permissions/microphone/request?dry_run=false");
      expect(init.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toEqual({ feature: "browser_voice" });
    });

    it("keeps the browser site-settings sentence, and reports nothing, in a plain browser", async () => {
      vi.spyOn(navigator, "userAgent", "get").mockReturnValue(MAC_UA);
      render(<BrowserRealtimeControl />);
      fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

      expect(await screen.findByText("sidebar.realtime_microphone_denied")).toBeTruthy();
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("does not report a denial of a call the wake word started", async () => {
      vi.spyOn(navigator, "userAgent", "get").mockReturnValue(MAC_UA);
      (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
      fakes.browserAudio = true;
      useEventStore.setState({
        events: [
          {
            id: "wake-denied",
            name: "BrowserVoiceRequested",
            ts: Date.now(),
            payload: { action: "start" },
          },
        ],
      });
      render(<BrowserRealtimeControl controlOnly />);

      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
      });
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  it("maps every connection/voice combination onto exactly one look", () => {
    expect(waveformPhase("idle", "idle")).toBe("idle");
    expect(waveformPhase("connecting", "idle")).toBe("connecting");
    expect(waveformPhase("error", "listening")).toBe("error");
    // A voice-side error must reach the pill even when the socket is fine.
    expect(waveformPhase("connected", "error")).toBe("error");
    expect(waveformPhase("connected", "listening")).toBe("listening");
    expect(waveformPhase("connected", "thinking")).toBe("working");
    expect(waveformPhase("connected", "speaking")).toBe("speaking");
  });

  it("disables browser voice with HTTPS guidance on an insecure origin", () => {
    fakes.supportIssue = "secure_context";
    render(<BrowserRealtimeControl />);

    const button = screen.getByRole("button", {
      name: "sidebar.realtime_browser_unavailable",
    });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("sidebar.realtime_https_required")).toBeTruthy();
    expect(fakes.connect).not.toHaveBeenCalled();
  });

  // Issue #399: on a host with no speech pipeline the browser that presses
  // Start holds the call itself, in pipeline mode through the classic chain.
  describe("call held by this browser", () => {
    beforeEach(() => {
      fakes.mode = "pipeline";
      fakes.available = false;
      useEventStore.setState({ toasts: [] });
    });

    it("starts in pipeline mode with plain PCM and keeps the call through the mode gate", async () => {
      // A realtime transport pinned in the settings must not leak into a
      // classic call: it has no WebRTC peer and no browser-audio contract.
      fakes.requiresWebRtcOffer = true;
      fakes.browserAudio = true;
      render(<BrowserRealtimeControl controlOnly />);

      act(() => {
        expect(startBrowserVoiceCall()).toBe(true);
      });

      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
      expect(fakes.options).toMatchObject({ requiresWebRtcOffer: false, browserAudio: false });
      expect(useEventStore.getState().voiceState).toBe("connecting");
      expect(browserVoiceCallLive()).toBe(true);

      act(() => fakes.callbacks?.onStatus?.("audio_ready", {}));
      expect(useEventStore.getState().voiceState).toBe("listening");
      // Pipeline mode hides the realtime surface; it must not hang this up.
      expect(fakes.disconnect).not.toHaveBeenCalled();

      act(() => {
        expect(stopBrowserVoiceCall()).toBe(true);
      });
      await waitFor(() => expect(fakes.disconnect).toHaveBeenCalledTimes(1));
      expect(useEventStore.getState().voiceState).toBe("idle");
      expect(browserVoiceCallLive()).toBe(false);
    });

    it("reports a failed start as a toast and frees the controls", async () => {
      fakes.connect.mockRejectedValueOnce(new Error("socket closed"));
      render(<BrowserRealtimeControl controlOnly />);

      act(() => {
        startBrowserVoiceCall();
      });

      await waitFor(() => expect(useEventStore.getState().voiceState).toBe("idle"));
      // A classic call has no realtime provider to blame; the line points at
      // the chain it actually runs on.
      expect(
        useEventStore.getState().toasts.map(({ kind, message }) => ({ kind, message })),
      ).toEqual([{ kind: "error", message: "sidebar.browser_voice_error" }]);
      expect(screen.queryByRole("alert")).toBeNull();
      expect(browserVoiceCallLive()).toBe(false);
    });

    it("shows the server's sentence when the server closes the call", async () => {
      render(<BrowserRealtimeControl controlOnly />);
      act(() => {
        startBrowserVoiceCall();
      });
      await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

      act(() =>
        fakes.callbacks?.onStatus?.("disconnected", {
          code: 1011,
          reason: "Voice could not start: a provider is missing. Check API Keys.",
        }),
      );

      await waitFor(() => expect(useEventStore.getState().voiceState).toBe("idle"));
      expect(useEventStore.getState().toasts.map((toast) => toast.message)).toEqual([
        "Voice could not start: a provider is missing. Check API Keys.",
      ]);
      expect(fakes.disconnect).toHaveBeenCalled();
    });

    it("is owned only by the app-root control", () => {
      render(<BrowserRealtimeControl />);
      expect(startBrowserVoiceCall()).toBe(false);
      expect(stopBrowserVoiceCall()).toBe(false);
    });
  });
});
