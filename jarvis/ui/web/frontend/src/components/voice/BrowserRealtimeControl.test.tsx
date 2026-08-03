import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";
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
    fakes.native = false;
    fakes.mode = "realtime";
    fakes.available = true;
    fakes.requiresWebRtcOffer = false;
    fakes.connect.mockClear();
    fakes.disconnect.mockClear();
    fakes.supportIssue = null;
    fakes.callbacks = null;
    fakes.options = null;
    setBrowserVoiceInputOwnership(false);
    delete (window as unknown as { pywebview?: unknown }).pywebview;
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
    });
  });

  it("is hidden in the desktop shell to prevent a second microphone", () => {
    fakes.native = true;
    (window as unknown as { pywebview?: unknown }).pywebview = { api: {} };
    render(<BrowserRealtimeControl />);
    expect(screen.queryByRole("button")).toBeNull();
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
    expect(fakes.options).toEqual({ requiresWebRtcOffer: true });
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
