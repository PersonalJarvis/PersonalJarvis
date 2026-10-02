import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/i18n";
import { EMPTY_PROMPTS, RESOLVED_HOLD_MS } from "@/lib/permissionPrompts";
import { useEventStore } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";

const bridge = vi.hoisted(() => ({ embedded: true }));
vi.mock("@/lib/embeddedDesktop", () => ({
  hasEmbeddedDesktopBridge: () => bridge.embedded,
}));
vi.mock("@/lib/bootStagger", () => ({ bootSettled: () => Promise.resolve() }));

import { PermissionPromptHost } from "./PermissionPromptHost";

const MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)";
let userAgent = MAC_UA;
let fetchMock: ReturnType<typeof vi.fn>;
let statusNeeded: unknown[] = [];

function episode(overrides: Record<string, unknown> = {}) {
  return {
    permissions: ["microphone"],
    feature: "dictation",
    reason: "denied",
    phase: "blocked",
    origin: "user",
    target: "",
    can_prompt: false,
    can_open_settings: true,
    outside_app: false,
    detail: "",
    trace_id: "srv",
    opened_at_ns: 1,
    ...overrides,
  };
}

beforeEach(() => {
  bridge.embedded = true;
  userAgent = MAC_UA;
  statusNeeded = [];
  vi.spyOn(window.navigator, "userAgent", "get").mockImplementation(() => userAgent);
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/permissions/status") {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          platform: "darwin",
          supported: true,
          headless: false,
          app_identity: { app_name: "Personal Jarvis", bundle_id: null, bundle_path: null, launched_as_bundle: true, stable: true },
          outside_installed_app: false,
          permissions: [],
          needed: statusNeeded,
        }),
      } as Response;
    }
    return { ok: true, status: 200, json: async () => ({ id: "microphone", status: "denied", can_reset: true, can_open_settings: true }) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  useI18nStore.getState().setUi("en", { push: false });
  useEventStore.setState({ solo: false });
  usePermissionsStore.setState({ ...EMPTY_PROMPTS, snapshot: null, owner: false, inline: {}, dictationNote: null });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PermissionPromptHost", () => {
  it("seeds from GET /status and mounts the card for an episode the window missed", async () => {
    statusNeeded = [episode()];

    render(<PermissionPromptHost />);

    expect(await screen.findByTestId("permission-prompt-card")).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/permissions/status")).toBe(true);
    expect(usePermissionsStore.getState().owner).toBe(true);
  });

  it("mounts nothing while there is no episode that needs the person", async () => {
    statusNeeded = [
      episode({ phase: "os_dialog", reason: "not_determined" }),
      episode({ feature: "wake_word", origin: "background" }),
    ];

    render(<PermissionPromptHost />);
    await waitFor(() => expect(usePermissionsStore.getState().episodes).toHaveLength(2));

    expect(screen.queryByTestId("permission-prompt-layer")).toBeNull();
  });

  it("opens when an event turns an episode into 'the person must act'", async () => {
    render(<PermissionPromptHost />);
    await waitFor(() => expect(usePermissionsStore.getState().owner).toBe(true));

    act(() => {
      usePermissionsStore.getState().ingest("PermissionNeeded", "t", episode({ phase: "os_dialog", reason: "not_determined" }), Date.now());
    });
    expect(screen.queryByTestId("permission-prompt-layer")).toBeNull();

    act(() => {
      usePermissionsStore.getState().ingest("PermissionNeeded", "t", episode(), Date.now() + 1);
    });
    expect(await screen.findByTestId("permission-prompt-card")).toBeTruthy();
  });

  it("is silent in a remote browser (no embedded bridge): no fetch, no card", async () => {
    bridge.embedded = false;
    statusNeeded = [episode()];

    render(<PermissionPromptHost />);
    act(() => {
      usePermissionsStore.getState().ingest("PermissionNeeded", "t", episode(), Date.now());
    });
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("permission-prompt-layer")).toBeNull();
    expect(usePermissionsStore.getState().owner).toBe(false);
  });

  it("is silent in a detached solo window", async () => {
    useEventStore.setState({ solo: true });
    statusNeeded = [episode()];

    render(<PermissionPromptHost />);
    act(() => {
      usePermissionsStore.getState().ingest("PermissionNeeded", "t", episode(), Date.now());
    });
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("permission-prompt-layer")).toBeNull();
  });

  it("does not touch the permission API off macOS (no boot-time /status fetch on Windows or Linux)", async () => {
    userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36";

    render(<PermissionPromptHost />);
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("draws no card on a headless backend", async () => {
    statusNeeded = [episode()];
    usePermissionsStore.setState({ snapshot: { platform: "darwin", headless: true } as never });
    fetchMock.mockImplementation(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ platform: "darwin", headless: true, app_identity: {}, permissions: [], needed: statusNeeded }),
    }) as Response);

    render(<PermissionPromptHost />);
    await waitFor(() => expect(usePermissionsStore.getState().episodes).toHaveLength(1));

    expect(screen.queryByTestId("permission-prompt-layer")).toBeNull();
  });

  it("keeps the layer for the 'Allowed' confirmation after the card resolved", async () => {
    statusNeeded = [episode()];
    render(<PermissionPromptHost />);
    await screen.findByTestId("permission-prompt-card");

    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "srv", { permissions: ["microphone"], feature: "dictation", granted: true }, Date.now());
    });

    expect(await screen.findByTestId("permission-allowed-card")).toBeTruthy();
  });

  it("keeps ONE layer node (and its live region) from the card to the confirmation, so 'Allowed' is announced", async () => {
    statusNeeded = [episode()];
    render(<PermissionPromptHost />);
    await screen.findByTestId("permission-prompt-card");
    const layer = screen.getByTestId("permission-prompt-layer");
    const live = screen.getByTestId("permission-live-region");
    const removed: Node[] = [];
    const observer = new MutationObserver((records) => {
      for (const record of records) record.removedNodes.forEach((node) => removed.push(node));
    });
    observer.observe(document.body, { childList: true, subtree: true });

    act(() => {
      usePermissionsStore
        .getState()
        .ingest("PermissionResolved", "srv", { permissions: ["microphone"], feature: "dictation", granted: true }, Date.now());
    });
    await screen.findByTestId("permission-allowed-card");
    await new Promise((resolve) => setTimeout(resolve, 20));
    observer.disconnect();

    expect(screen.getByTestId("permission-prompt-layer")).toBe(layer);
    expect(screen.getByTestId("permission-live-region")).toBe(live);
    expect(removed).not.toContain(layer);
    expect(removed).not.toContain(live);
    expect(live.textContent).toBe("Allowed. You can carry on.");
  });

  it("holds the 'Allowed' confirmation for the whole hold and then unmounts the layer", async () => {
    statusNeeded = [episode()];
    render(<PermissionPromptHost />);
    await screen.findByTestId("permission-prompt-card");
    vi.useFakeTimers({ shouldAdvanceTime: false });
    try {
      act(() => {
        usePermissionsStore
          .getState()
          .ingest("PermissionResolved", "srv", { permissions: ["microphone"], feature: "dictation", granted: true }, Date.now());
      });
      expect(screen.getByTestId("permission-allowed-card")).toBeTruthy();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(4_900);
      });
      expect(screen.getByTestId("permission-allowed-card")).toBeTruthy();
      expect(screen.getByTestId("permission-live-region").textContent).toBe("Allowed. You can carry on.");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(RESOLVED_HOLD_MS - 4_900 + 200);
      });
      expect(screen.queryByTestId("permission-allowed-card")).toBeNull();
      expect(screen.queryByTestId("permission-prompt-layer")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks nothing at launch: mount, seed and a return to the window send no POST", async () => {
    statusNeeded = [episode(), episode({ feature: "voice", permissions: ["microphone"], phase: "os_dialog", reason: "not_determined" })];
    render(<PermissionPromptHost />);
    await screen.findByTestId("permission-prompt-card");
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await new Promise((resolve) => setTimeout(resolve, 600));

    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toEqual([]);
  });

  it("becomes the owner when the shell injects its flag late and dispatches jarvis-token-ready", async () => {
    bridge.embedded = false;
    statusNeeded = [episode()];
    render(<PermissionPromptHost />);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(usePermissionsStore.getState().owner).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();

    bridge.embedded = true;
    act(() => {
      window.dispatchEvent(new Event("jarvis-token-ready"));
    });

    expect(await screen.findByTestId("permission-prompt-card")).toBeTruthy();
    expect(usePermissionsStore.getState().owner).toBe(true);
  });
});
