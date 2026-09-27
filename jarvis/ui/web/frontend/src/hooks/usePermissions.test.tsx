import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usePermissions, type PermissionSnapshot } from "./usePermissions";

vi.mock("@/lib/bootStagger", () => ({ bootSettled: () => Promise.resolve() }));

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
        can_reset: status !== "granted",
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

function twoRowSnapshot(
  microphone: "not_determined" | "granted",
  accessibility: "not_granted" | "granted",
  restartRequired = false,
): PermissionSnapshot {
  return {
    platform: "darwin",
    supported: true,
    headless: false,
    app_identity: { stable: true, foreground: true, launched_as_bundle: true },
    permissions: [
      {
        id: "microphone",
        status: microphone,
        required: ["voice"],
        can_request: microphone !== "granted",
        can_open_settings: true,
        can_reset: false,
        restart_required: false,
      },
      {
        // Accessibility has no prompt left here: only the Settings switch.
        id: "accessibility",
        status: accessibility,
        required: ["global_hotkeys"],
        can_request: false,
        can_open_settings: accessibility !== "granted",
        can_reset: false,
        restart_required: restartRequired,
      },
    ],
    features: {
      voice: { ready: microphone === "granted", missing: [] },
      global_hotkeys: { ready: accessibility === "granted", missing: [] },
    },
    restart_required: restartRequired,
  };
}

it("walks every missing row in order, waits for each grant, then restarts once", async () => {
  // The fake macOS: the microphone dialog is answered on the request; the
  // Accessibility switch flips two polls after System Settings opened, and
  // that grant only applies to a fresh process.
  let microphone: "not_determined" | "granted" = "not_determined";
  let accessibility: "not_granted" | "granted" = "not_granted";
  let pollsSinceSettings = -1;
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST") calls.push(url);
      if (url === "/api/settings/restart-app") {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
      }
      if (url.includes("/microphone/request")) microphone = "granted";
      if (url.includes("/accessibility/open-settings")) pollsSinceSettings = 0;
      else if (url === "/api/permissions/status" && pollsSinceSettings >= 0) {
        pollsSinceSettings += 1;
        if (pollsSinceSettings >= 2) accessibility = "granted";
      }
      const restart = accessibility === "granted" && pollsSinceSettings >= 0;
      const body = twoRowSnapshot(microphone, accessibility, restart);
      const payload = init?.method === "POST" ? { ok: true, snapshot: body } : body;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
    }),
  );

  const { result } = renderHook(() => usePermissions());
  await waitFor(() => expect(result.current.setupNeeded).toBe(true));

  let outcome: string | undefined;
  await act(async () => {
    outcome = await result.current.setupAll({ autoRestart: true, pollMs: 1 });
  });

  expect(outcome).toBe("restart");
  expect(calls).toEqual([
    "/api/permissions/microphone/request?dry_run=false",
    "/api/permissions/accessibility/open-settings?dry_run=false",
    "/api/settings/restart-app",
  ]);
  expect(result.current.setupProgress).toBeNull();
});

it("stops when asked and never restarts without being allowed to", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST") calls.push(url);
      const body = twoRowSnapshot("granted", "not_granted");
      const payload = init?.method === "POST" ? { ok: true, snapshot: body } : body;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
    }),
  );

  const { result } = renderHook(() => usePermissions());
  await waitFor(() => expect(result.current.setupNeeded).toBe(true));

  let run: Promise<string> | undefined;
  act(() => {
    run = result.current.setupAll({ pollMs: 1 });
  });
  // The Settings pane is open and the switch never flips: the user gives up.
  await waitFor(() => expect(result.current.setupProgress?.phase).toBe("settings"));
  act(() => {
    result.current.cancelSetup();
  });
  let outcome: string | undefined;
  await act(async () => {
    outcome = await run;
  });

  expect(outcome).toBe("cancelled");
  expect(calls).toEqual(["/api/permissions/accessibility/open-settings?dry_run=false"]);
});
