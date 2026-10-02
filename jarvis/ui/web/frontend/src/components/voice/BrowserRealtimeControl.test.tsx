import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { usePermissionsStore } from "@/store/permissions";
import {
  setBrowserVoiceInputOwnership,
  setVoiceInputLevel,
  voiceInputLevelRef,
} from "@/lib/voiceInputLevel";

import { BrowserRealtimeControl, waveformPhase } from "./BrowserRealtimeControl";

const fakes = vi.hoisted(() => ({
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
  options: null as null | { requiresWebRtcOffer?: boolean },
}));

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

// Identity translator (assertions match i18n keys); the rest of the module stays
// real because the permission copy helpers use `fill` and `useUiLanguage`.
vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
}));

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
});

/*
 * The embedded desktop window reaches the SAME macOS microphone permission the
 * native pipeline uses, so the click asks the host first (the request is the
 * gesture) and only then opens the stream. A remote browser keeps the browser's
 * own prompt and message.
 */
describe("BrowserRealtimeControl host microphone", () => {
  let requests: Array<{ url: string; body: unknown }> = [];
  let answer: Record<string, unknown> = {};
  let userAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)";

  function embedDesktop() {
    (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
  }

  beforeEach(() => {
    // The same clean slate the first suite starts from.
    fakes.native = false;
    fakes.mode = "realtime";
    fakes.available = true;
    fakes.requiresWebRtcOffer = false;
    fakes.browserAudio = false;
    fakes.connect.mockReset();
    fakes.connect.mockImplementation(async () => undefined);
    fakes.disconnect.mockClear();
    fakes.supportIssue = null;
    fakes.callbacks = null;
    fakes.options = null;
    setBrowserVoiceInputOwnership(false);
    useEventStore.setState({
      events: [],
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      solo: false,
      activeSection: "chats",
      detachedViews: [],
    });
    requests = [];
    answer = { outcome: "granted", granted: true, reason: "" };
    userAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)";
    vi.spyOn(window.navigator, "userAgent", "get").mockImplementation(() => userAgent);
    usePermissionsStore.setState({ ...EMPTY_PROMPTS, inline: {}, snapshot: null });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        requests.push({
          url: String(input),
          body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
        });
        return { ok: true, status: 200, json: async () => answer } as Response;
      }),
    );
  });
  afterEach(() => {
    delete (window as unknown as { pywebview?: unknown }).pywebview;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("requests nothing from the host on Windows or Linux, even in the embedded window", async () => {
    userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36";
    embedDesktop();
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
    expect(requests).toEqual([]);
  });

  it("asks macOS from the click BEFORE the stream opens", async () => {
    embedDesktop();
    let release: (value: unknown) => void = () => undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        requests.push({
          url: String(input),
          body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
        });
        await new Promise((resolve) => {
          release = resolve;
        });
        return { ok: true, status: 200, json: async () => answer } as Response;
      }),
    );
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].url).toBe("/api/permissions/microphone/request?dry_run=false");
    expect(requests[0].body).toEqual({ feature: "browser_voice" });
    expect(fakes.connect).not.toHaveBeenCalled();

    release(undefined);
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
  });

  it("does not open the stream while macOS is asking, and says so", async () => {
    embedDesktop();
    answer = { outcome: "pending", granted: false, reason: "not_determined" };
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await screen.findByText("permissions.inline.os_dialog");
    expect(fakes.connect).not.toHaveBeenCalled();
    // The button is back to Start: the person answers macOS, then presses again.
    expect(screen.getByRole("button", { name: "sidebar.realtime_start" })).toBeTruthy();
    // This control explains it; the floating card stays quiet.
    expect(usePermissionsStore.getState().inline).toEqual({ browser_voice: 1 });
  });

  it("says 'allowed - press again' once the grant arrives, and still starts nothing", async () => {
    embedDesktop();
    answer = { outcome: "pending", granted: false, reason: "not_determined" };
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await screen.findByText("permissions.inline.os_dialog");

    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "", { permissions: ["microphone"], feature: "browser_voice", granted: true }, Date.now() + 5);
    });

    await screen.findByText("permissions.inline.browser_voice.allowed");
    expect(fakes.connect).not.toHaveBeenCalled();
    expect(usePermissionsStore.getState().inline).toEqual({});
  });

  it("a blocked microphone gets the full per-feature sentence and a Retry, never the browser message", async () => {
    embedDesktop();
    answer = { outcome: "denied", granted: false, reason: "denied" };
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await screen.findByText("permissions.prompt.browser_voice.denied");
    expect(screen.queryByText("sidebar.realtime_microphone_denied")).toBeNull();
    expect(fakes.connect).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "sidebar.realtime_retry" })).toBeTruthy();
  });

  it("goes on to the stream when the host route fails, so voice is not made impossible by it", async () => {
    embedDesktop();
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("offline"))));
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
  });

  it("maps a refused stream in the desktop window to the same permission episode", async () => {
    embedDesktop();
    // The first answer (before the stream) says fine; the stream is refused anyway,
    // and the second look finds the microphone blocked.
    const answers = [
      { outcome: "granted", granted: true, reason: "" },
      { outcome: "needs_settings", granted: false, reason: "needs_settings" },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        requests.push({
          url: String(input),
          body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
        });
        return { ok: true, status: 200, json: async () => answers.shift() ?? answers[0] } as Response;
      }),
    );
    fakes.connect.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await screen.findByText("permissions.prompt.browser_voice.needs_settings");
    expect(requests).toHaveLength(2);
    expect(screen.queryByText("sidebar.realtime_microphone_denied")).toBeNull();
  });

  it("keeps the browser's site-settings message when macOS says the microphone is fine", async () => {
    embedDesktop();
    fakes.connect.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await screen.findByText("sidebar.realtime_microphone_denied");
  });

  it("a remote browser never asks the host and keeps the browser message", async () => {
    fakes.connect.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    render(<BrowserRealtimeControl />);

    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));

    await screen.findByText("sidebar.realtime_microphone_denied");
    expect(requests).toEqual([]);
  });

  it("a start the desktop made by itself (a wake) never asks macOS", async () => {
    embedDesktop();
    fakes.browserAudio = true;
    useEventStore.setState({
      events: [{ id: "wake-1", name: "BrowserVoiceRequested", ts: Date.now(), payload: { action: "start" } }],
    });
    render(<BrowserRealtimeControl controlOnly />);

    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));
    expect(requests).toEqual([]);
  });
});
