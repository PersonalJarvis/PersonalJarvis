import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usePermissions, type PermissionSnapshot } from "./usePermissions";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function snapshot(status: "not_determined" | "granted"): PermissionSnapshot {
  return {
    platform: "darwin",
    supported: true,
    headless: false,
    app_identity: { stable: true, foreground: true, launched_as_bundle: true },
    permissions: [
      {
        id: "microphone",
        status,
        required: ["voice"],
        can_request: status === "not_determined",
        can_open_settings: true,
        restart_required: false,
      },
    ],
    features: {
      voice: { ready: status === "granted", missing: status === "granted" ? [] : ["microphone"] },
    },
    restart_required: false,
  };
}

it("loads a fresh snapshot and unwraps a request operation", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      calls.push([url, init]);
      const payload = init?.method === "POST"
        ? { ok: true, snapshot: snapshot("granted") }
        : snapshot("not_determined");
      return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
    }),
  );

  const { result } = renderHook(() => usePermissions());
  await waitFor(() => expect(result.current.snapshot?.permissions[0].status).toBe("not_determined"));

  await act(async () => {
    await result.current.request("microphone");
  });

  expect(result.current.snapshot?.features.voice.ready).toBe(true);
  expect(calls).toContainEqual([
    "/api/permissions/microphone/request?dry_run=false",
    { method: "POST" },
  ]);
});

it("surfaces a failed native request without losing the last snapshot", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(snapshot("not_determined")) })
    .mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ message: "Bring Personal Jarvis to the foreground." }),
    });
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderHook(() => usePermissions());
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());
  await act(async () => {
    await expect(result.current.request("microphone")).rejects.toThrow("foreground");
  });

  expect(result.current.error).toContain("foreground");
  expect(result.current.snapshot?.permissions[0].status).toBe("not_determined");
});
