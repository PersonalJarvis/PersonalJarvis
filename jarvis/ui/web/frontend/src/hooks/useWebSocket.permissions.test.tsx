/**
 * The permission events and the dictation refusal reach the UI through the real
 * useWebSocket -> WSClient path (via a MockWebSocket, like useWebSocket.test.tsx):
 * a user-started feature that a macOS permission stopped becomes ONE toast with
 * one button in the owner window, and a refused dictation start resets the
 * recording pill.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastLayer } from "@/components/ToastLayer";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useI18nStore } from "@/i18n";
import { resetPermissionToastState } from "@/lib/permissionToast";
import { useEventStore } from "@/store/events";

class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = MockWebSocket.OPEN;
  static last: MockWebSocket | null = null;
  private listeners: Record<string, Array<(ev: unknown) => void>> = {};
  url: string;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
    queueMicrotask(() => this.fire("open", {}));
  }

  addEventListener(type: string, fn: (ev: unknown) => void) {
    (this.listeners[type] ??= []).push(fn);
  }

  send = vi.fn();

  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.fire("close", {});
  });

  fire(type: string, ev: unknown) {
    (this.listeners[type] ?? []).forEach((fn) => fn(ev));
  }

  deliver(data: unknown) {
    this.fire("message", { data: JSON.stringify(data) });
  }
}

function envelope(eventName: string, payload: Record<string, unknown>, trace = "trace-1") {
  return {
    type: "event",
    event_name: eventName,
    source_layer: "platform.permissions",
    timestamp_ns: Date.now() * 1_000_000,
    trace_id: trace,
    payload,
  };
}

vi.mock("@/lib/bootStagger", () => ({ bootSettled: () => Promise.resolve() }));

const needed = {
  permissions: ["microphone"],
  feature: "dictation",
  reason: "denied",
  phase: "blocked",
  origin: "user",
  target: "",
  can_prompt: false,
  can_open_settings: true,
  outside_app: false,
  detail: "Microphone access is off.",
};

const MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Harness() {
  return (
    <QueryClientProvider client={queryClient}>
      <Inner />
      <ToastLayer />
    </QueryClientProvider>
  );
}
function Inner() {
  useWebSocket();
  return null;
}

function embeddedMac() {
  (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = true;
  vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(MAC_UA);
}

describe("useWebSocket permission wiring", () => {
  const OriginalWS = globalThis.WebSocket;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;
    (window as unknown as { location: unknown }).location = { protocol: "http:", host: "localhost:5173" };
    fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    useI18nStore.getState().setUi("en", { push: false });
    useEventStore.setState({ dictating: false, toasts: [], solo: false });
    resetPermissionToastState();
    queryClient.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket = OriginalWS;
    MockWebSocket.last = null;
  });

  it("a user-started feature stopped by a denied microphone becomes one toast with one action", async () => {
    embeddedMac();
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("PermissionNeeded", needed));

    expect(await screen.findByText("Personal Jarvis needs microphone access to hear you.")).toBeTruthy();
    expect(screen.getAllByTestId("toast-action")).toHaveLength(1);
    expect(screen.getByTestId("toast-action").textContent).toBe("Open System Settings");
  });

  it("the same episode is told once, and again after PermissionResolved ends it", async () => {
    embeddedMac();
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("PermissionNeeded", needed));
    MockWebSocket.last!.deliver(envelope("PermissionNeeded", needed));
    expect(useEventStore.getState().toasts).toHaveLength(1);
    expect(useEventStore.getState().toasts[0].count).toBe(1);

    // A grant takes the stale sentence down ...
    MockWebSocket.last!.deliver(
      envelope("PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true }),
    );
    expect(useEventStore.getState().toasts).toEqual([]);

    // ... and a later denial of the same episode is news again.
    MockWebSocket.last!.deliver(envelope("PermissionNeeded", needed));
    expect(useEventStore.getState().toasts).toHaveLength(1);
  });

  it("says nothing while macOS is asking, for a background consumer, or outside the owner window", async () => {
    embeddedMac();
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("PermissionNeeded", { ...needed, phase: "os_dialog", reason: "not_determined" }));
    MockWebSocket.last!.deliver(envelope("PermissionNeeded", { ...needed, feature: "screen_context", origin: "background" }));
    expect(useEventStore.getState().toasts).toEqual([]);

    useEventStore.setState({ solo: true });
    MockWebSocket.last!.deliver(envelope("PermissionNeeded", needed));
    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("says nothing in a plain browser, even on a Mac (it would open Settings on another computer)", async () => {
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(MAC_UA);
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("PermissionNeeded", needed));

    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("on connect the owner window only READS the open episodes: nothing is asked at launch", async () => {
    embeddedMac();
    fetchMock.mockImplementation(
      async () =>
        ({ ok: true, status: 200, json: async () => ({ needed: [] }) }) as Response,
    );
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver({ type: "welcome", session_id: "s1" });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/permissions/status")).toBe(true),
    );

    const permissionCalls = fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/permissions"));
    expect(permissionCalls.map(([url, init]) => [String(url), (init as RequestInit | undefined)?.method ?? "GET"])).toEqual([
      ["/api/permissions/status", "GET"],
    ]);
  });

  it("does not read the permission API at all in a window that cannot toast", async () => {
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver({ type: "welcome", session_id: "s1" });
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/permissions"))).toBe(false);
  });

  it("tells an episode that opened while no window was listening, once per page load", async () => {
    embeddedMac();
    const wake = { ...needed, feature: "wake_word", origin: "background" };
    fetchMock.mockImplementation(
      async (url: unknown) =>
        ({
          ok: true,
          status: 200,
          json: async () => (String(url) === "/api/permissions/status" ? { needed: [needed, wake] } : {}),
        }) as Response,
    );
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver({ type: "welcome", session_id: "s1" });
    await waitFor(() => expect(useEventStore.getState().toasts.length).toBeGreaterThan(0));
    expect(await screen.findByText("Personal Jarvis needs microphone access to hear you.")).toBeTruthy();
    expect(screen.getByText(/The wake word cannot listen/)).toBeTruthy();

    // The person lets them expire; the socket reconnects: nothing is repeated.
    useEventStore.setState({ toasts: [] });
    MockWebSocket.last!.deliver({ type: "welcome", session_id: "s1" });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("DictationRefused ends a recording the composer started", async () => {
    useEventStore.setState({ dictating: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(
      envelope("DictationRefused", { reason: "microphone_unavailable", detail: "Microphone access is off." }),
    );

    expect(useEventStore.getState().dictating).toBe(false);
  });

  it.each(["already_running", "nothing_to_paste", "paste_unavailable", "history_disabled"])(
    "a %s refusal does not end a recording that is live in this window",
    async (reason) => {
      useEventStore.setState({ dictating: true });
      render(<Harness />);
      await Promise.resolve();

      MockWebSocket.last!.deliver(envelope("DictationRefused", { reason, detail: "" }));

      expect(useEventStore.getState().dictating).toBe(true);
    },
  );

  it.each(["microphone_unavailable", "no_stt", "handover_failed", "pipeline_not_running", "voice_session_active"])(
    "a %s refusal means the start failed and resets the pill",
    async (reason) => {
      useEventStore.setState({ dictating: true });
      render(<Harness />);
      await Promise.resolve();

      MockWebSocket.last!.deliver(envelope("DictationRefused", { reason, detail: "" }));

      expect(useEventStore.getState().dictating).toBe(false);
    },
  );

  it("ErrorOccurred from ui.web.dictation also stops the recording pill", async () => {
    useEventStore.setState({ dictating: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(
      envelope("ErrorOccurred", { layer: "ui.web.dictation", error_type: "DictationBusy", message: "busy", recoverable: true }),
    );

    expect(useEventStore.getState().dictating).toBe(false);
  });

  it("an ErrorOccurred from another layer is not a dictation refusal", async () => {
    useEventStore.setState({ dictating: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("ErrorOccurred", { layer: "brain", message: "x", recoverable: true }));

    expect(useEventStore.getState().dictating).toBe(true);
  });
});
