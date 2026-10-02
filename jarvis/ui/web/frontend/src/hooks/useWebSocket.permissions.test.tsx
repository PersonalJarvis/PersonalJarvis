/**
 * The two permission events and the dictation refusal reach the stores through
 * the real useWebSocket -> WSClient path (via a MockWebSocket, like
 * useWebSocket.test.tsx).
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useWebSocket } from "@/hooks/useWebSocket";
import { EMPTY_PROMPTS } from "@/lib/permissionPrompts";
import { useEventStore } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";

vi.mock("@/lib/bootStagger", () => ({ bootSettled: () => Promise.resolve() }));

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

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Harness() {
  return (
    <QueryClientProvider client={queryClient}>
      <Inner />
    </QueryClientProvider>
  );
}
function Inner() {
  useWebSocket();
  return null;
}

describe("useWebSocket permission wiring", () => {
  const OriginalWS = globalThis.WebSocket;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;
    (window as unknown as { location: unknown }).location = { protocol: "http:", host: "localhost:5173" };
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/permissions/status") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            platform: "darwin",
            supported: true,
            headless: false,
            app_identity: { app_name: "Personal Jarvis" },
            outside_installed_app: false,
            permissions: [],
            needed: [{ ...needed, feature: "voice", trace_id: "srv", opened_at_ns: 1 }],
          }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    useEventStore.setState({ dictating: false, toasts: [] });
    usePermissionsStore.setState({ ...EMPTY_PROMPTS, snapshot: null, owner: false, inline: {}, dictationNote: null });
    queryClient.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket = OriginalWS;
    MockWebSocket.last = null;
  });

  it("PermissionNeeded opens an episode and PermissionResolved closes it", async () => {
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("PermissionNeeded", needed));
    expect(usePermissionsStore.getState().episodes).toHaveLength(1);
    expect(usePermissionsStore.getState().episodes[0]).toMatchObject({
      feature: "dictation",
      reason: "denied",
      trace_id: "trace-1",
    });

    MockWebSocket.last!.deliver(
      envelope("PermissionResolved", { permissions: ["microphone"], feature: "dictation", granted: true }),
    );
    expect(usePermissionsStore.getState().episodes).toEqual([]);
    expect(usePermissionsStore.getState().resolved[0]).toMatchObject({ feature: "dictation", granted: true });
  });

  it("the welcome frame re-seeds the open episodes from the REST list (owner window only)", async () => {
    usePermissionsStore.setState({ owner: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver({ type: "welcome", session_id: "s1" });

    await waitFor(() => expect(usePermissionsStore.getState().episodes.map((e) => e.feature)).toEqual(["voice"]));
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/permissions/status")).toBe(true);
    // Nothing is asked at launch: a welcome frame only READS.
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) =>
          String(url).startsWith("/api/permissions/") && (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toEqual([]);
  });

  it("the welcome frame does not touch the permission API in a window that is not the owner", async () => {
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver({ type: "welcome", session_id: "s1" });
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(fetchMock.mock.calls.some(([url]) => url === "/api/permissions/status")).toBe(false);
  });

  it("DictationRefused ends a recording the composer started and leaves a note", async () => {
    useEventStore.setState({ dictating: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(
      envelope("DictationRefused", { reason: "microphone_unavailable", detail: "Microphone access is off." }),
    );

    expect(useEventStore.getState().dictating).toBe(false);
    expect(usePermissionsStore.getState().dictationNote).toMatchObject({
      source: "refused",
      reason: "microphone_unavailable",
    });
  });

  it.each(["already_running", "nothing_to_paste", "paste_unavailable", "history_disabled"])(
    "a %s refusal does not end a recording that is live in this window",
    async (reason) => {
      useEventStore.setState({ dictating: true });
      render(<Harness />);
      await Promise.resolve();

      MockWebSocket.last!.deliver(envelope("DictationRefused", { reason, detail: "" }));

      expect(useEventStore.getState().dictating).toBe(true);
      expect(usePermissionsStore.getState().dictationNote).toBeNull();
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

  it("only a window that IS dictating reacts", async () => {
    useEventStore.setState({ dictating: false });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("DictationRefused", { reason: "microphone_unavailable", detail: "" }));

    expect(usePermissionsStore.getState().dictationNote).toBeNull();
  });

  it("ErrorOccurred from ui.web.dictation also stops the recording pill, and keeps the better reason", async () => {
    useEventStore.setState({ dictating: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("DictationRefused", { reason: "no_stt", detail: "" }));
    useEventStore.setState({ dictating: true });
    MockWebSocket.last!.deliver(
      envelope("ErrorOccurred", { layer: "ui.web.dictation", error_type: "DictationBusy", message: "busy", recoverable: true }),
    );

    expect(useEventStore.getState().dictating).toBe(false);
    expect(usePermissionsStore.getState().dictationNote?.reason).toBe("no_stt");
  });

  it("ErrorOccurred(DictationUnavailable) says the pipeline is not running", async () => {
    useEventStore.setState({ dictating: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(
      envelope("ErrorOccurred", { layer: "ui.web.dictation", error_type: "DictationUnavailable", message: "x", recoverable: true }),
    );

    expect(usePermissionsStore.getState().dictationNote).toMatchObject({ source: "error", reason: "pipeline_not_running" });
  });

  it("an ErrorOccurred from another layer is not a dictation refusal", async () => {
    useEventStore.setState({ dictating: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("ErrorOccurred", { layer: "brain", message: "x", recoverable: true }));

    expect(useEventStore.getState().dictating).toBe(true);
    expect(usePermissionsStore.getState().dictationNote).toBeNull();
  });
});
