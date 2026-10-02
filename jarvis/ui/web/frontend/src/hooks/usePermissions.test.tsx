import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetConnectBudgetForTests } from "@/lib/connectBudget";
import { usePermissionsStore } from "@/store/permissions";
import { usePermissions } from "./usePermissions";

interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[] = [];
let snapshotBody: Record<string, unknown>;
let failNext: { status: number; body: unknown } | null = null;

function row(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    label: id,
    status: "not_determined",
    used_for: ["voice"],
    can_request: true,
    can_open_settings: true,
    can_reset: true,
    restart_hint: false,
    detail: "",
    settings_path: "System Settings > Privacy & Security > Microphone",
    ...overrides,
  };
}

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    platform: "darwin",
    supported: true,
    headless: false,
    app_identity: { app_name: "Personal Jarvis", bundle_id: "x", bundle_path: null, launched_as_bundle: true, stable: true },
    outside_installed_app: false,
    permissions: [row("microphone")],
    needed: [],
    ...overrides,
  };
}

beforeEach(() => {
  calls = [];
  failNext = null;
  snapshotBody = snapshot();
  resetConnectBudgetForTests();
  usePermissionsStore.setState({ snapshot: null });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });
      if (failNext && (init?.method ?? "GET") === "POST") {
        const failure = failNext;
        failNext = null;
        return { ok: false, status: failure.status, json: async () => failure.body } as Response;
      }
      if (url.includes("/request")) {
        return { ok: true, status: 200, json: async () => ({ permission: "microphone", outcome: "pending", granted: false, state: "not_determined", asked: true }) } as Response;
      }
      if (url.includes("/open-settings") || url.includes("/reset")) {
        return { ok: true, status: 200, json: async () => ({ ok: true, permission_id: "microphone", action: "x", performed: true, dry_run: false, message: "", permission: null }) } as Response;
      }
      return { ok: true, status: 200, json: async () => snapshotBody } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const statusReads = () =>
  calls.filter((call) => call.method === "GET" && call.url.split("?")[0] === "/api/permissions/status");
const reads = () => statusReads().length;

describe("usePermissions (a passive read)", () => {
  it("reads the snapshot once on mount and shares the platform with the store", async () => {
    const { result } = renderHook(() => usePermissions());

    await waitFor(() => expect(result.current.snapshot?.permissions).toHaveLength(1));

    expect(result.current.loading).toBe(false);
    expect(reads()).toBe(1);
    expect(usePermissionsStore.getState().snapshot?.platform).toBe("darwin");
  });

  it("does not poll: nothing is read on a timer", async () => {
    vi.useFakeTimers();
    try {
      renderHook(() => usePermissions());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(reads()).toBe(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10 * 60_000);
      });

      expect(reads()).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refreshes once, jittered, when the person comes back to the window", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());

    act(() => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(reads()).toBe(2), { timeout: 2_000 });
    // Only the first refetch after the window regained focus carries the hint.
    expect(statusReads().map((call) => call.url)).toEqual([
      "/api/permissions/status",
      "/api/permissions/status?activated=1",
    ]);
  });

  it("counts a return only once the page was read again (the page uses it for 'back from Settings')", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    expect(result.current.returns).toBe(0);

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => expect(result.current.returns).toBe(1), { timeout: 2_000 });
    expect(reads()).toBe(2);
  });

  it("does not ask the backend anything but reads on mount and on a return (nothing is asked at launch)", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(reads()).toBe(2), { timeout: 2_000 });

    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
  });

  it("joins a read that is already running instead of starting another", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    const before = reads();

    await act(async () => {
      await Promise.all([result.current.refetch(), result.current.refetch(), result.current.refetch()]);
    });

    expect(reads()).toBe(before + 1);
  });

  it("request() posts the options as a body and reads the page again afterwards", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    const before = reads();

    let answer: unknown;
    await act(async () => {
      answer = await result.current.request("microphone", { feature: "dictation", allow_outside_app: true });
    });

    const post = calls.find((call) => call.method === "POST")!;
    expect(post.url).toBe("/api/permissions/microphone/request?dry_run=false");
    expect(post.body).toEqual({ allow_outside_app: true, feature: "dictation" });
    expect(answer).toMatchObject({ outcome: "pending", asked: true });
    expect(reads()).toBe(before + 1);
  });

  it("request() without options sends no body at all", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());

    await act(async () => {
      await result.current.request("microphone");
    });

    expect(calls.find((call) => call.method === "POST")!.body).toBeUndefined();
  });

  it("openSettings() and reset() hit their routes", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());

    await act(async () => {
      await result.current.openSettings("screen_recording");
      await result.current.reset("accessibility");
    });

    expect(calls.map((call) => call.url)).toEqual(
      expect.arrayContaining([
        "/api/permissions/screen_recording/open-settings?dry_run=false",
        "/api/permissions/accessibility/reset?dry_run=false",
      ]),
    );
  });

  it("a failed action throws a typed error and still refreshes the page", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    const before = reads();
    failNext = { status: 429, body: { error: "rate_limited", retry_after_s: 4 } };

    await act(async () => {
      await expect(result.current.request("microphone")).rejects.toMatchObject({ status: 429, retryAfterS: 4 });
    });

    expect(reads()).toBe(before + 1);
    expect(result.current.pendingId).toBeNull();
  });

  it("keeps the last snapshot when a later read fails", async () => {
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.snapshot?.permissions).toHaveLength(1);
    expect(result.current.error).toBe("offline");
  });

  it("treats an answer that is not a v2 snapshot as unreadable, not as 'nothing needed'", async () => {
    snapshotBody = { platform: "darwin", permissions: [], features: {} };

    const { result } = renderHook(() => usePermissions());

    await waitFor(() => expect(result.current.error).toBe("unreadable"));
    expect(result.current.snapshot).toBeNull();
  });
});
