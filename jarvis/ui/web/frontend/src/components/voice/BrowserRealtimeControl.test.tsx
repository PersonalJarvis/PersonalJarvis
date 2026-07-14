import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";

import { BrowserRealtimeControl } from "./BrowserRealtimeControl";

const fakes = vi.hoisted(() => ({
  native: false,
  mode: "realtime",
  available: true,
  connect: vi.fn(async () => undefined),
  disconnect: vi.fn(async () => undefined),
  supportIssue: null as
    | null
    | "secure_context"
    | "microphone_unavailable"
    | "audio_worklet_unavailable",
  callbacks: null as null | {
    onAudio?: () => void;
    onStatus?: (status: string, payload: Record<string, unknown>) => void;
  },
}));

vi.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ data: { native_file_actions: fakes.native, platform: "linux" } }),
}));

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: fakes.mode,
    realtimeAvailable: fakes.available,
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
    constructor(callbacks: NonNullable<typeof fakes.callbacks>) {
      fakes.callbacks = callbacks;
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
    fakes.connect.mockClear();
    fakes.disconnect.mockClear();
    fakes.supportIssue = null;
    fakes.callbacks = null;
    useEventStore.setState({ voiceState: "idle" });
  });

  it("is hidden in the desktop shell to prevent a second microphone", () => {
    fakes.native = true;
    render(<BrowserRealtimeControl />);
    expect(screen.queryByRole("button")).toBeNull();
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

  it("returns to thinking after an interim realtime sentence", async () => {
    render(<BrowserRealtimeControl />);
    fireEvent.click(screen.getByRole("button", { name: "sidebar.realtime_start" }));
    await waitFor(() => expect(fakes.connect).toHaveBeenCalledTimes(1));

    act(() => fakes.callbacks?.onAudio?.());
    expect(useEventStore.getState().voiceState).toBe("speaking");

    act(() => fakes.callbacks?.onStatus?.("thinking", {}));
    expect(useEventStore.getState().voiceState).toBe("thinking");
  });

  it("explains that a key is required instead of opening the microphone", () => {
    fakes.available = false;
    render(<BrowserRealtimeControl />);

    const button = screen.getByRole("button", { name: "sidebar.realtime_unavailable" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(fakes.connect).not.toHaveBeenCalled();
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
