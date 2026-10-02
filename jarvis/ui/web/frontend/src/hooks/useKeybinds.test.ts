import { renderHook, waitFor, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useKeybinds } from "./useHotkey";

const KEYBINDS = {
  call: "f3+f4",
  hangup: "f1+f2",
  dictate: "ctrl+right_alt+j",
  dictate_toggle: "ctrl+right_alt+space",
};

const FULL = {
  keybinds: KEYBINDS,
  defaults: { ...KEYBINDS },
  suggestions: ["ctrl+shift+space", "ctrl+shift+d"],
  restart_required: true,
};

afterEach(() => vi.restoreAllMocks());

describe("useKeybinds", () => {
  it("loads keybinds from the API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => FULL }),
    );
    const { result } = renderHook(() => useKeybinds());
    await waitFor(() => expect(result.current.config).not.toBeNull());
    expect(result.current.config?.keybinds.call).toBe("f3+f4");
    // The two dictation actions travel over the same route as call/hangup —
    // one config, four actions, no second endpoint.
    expect(result.current.config?.keybinds.dictate).toBe("ctrl+right_alt+j");
    expect(result.current.config?.keybinds.dictate_toggle).toBe(
      "ctrl+right_alt+space",
    );
    expect(result.current.config?.suggestions).toContain("ctrl+shift+space");
  });

  it("PUTs the hands-free action under its own id", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => FULL });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useKeybinds());
    await waitFor(() => expect(result.current.config).not.toBeNull());
    await act(async () => {
      await result.current.saveKeybind("dictate_toggle", "ctrl+shift+d");
    });

    const putCall = fetchMock.mock.calls.find((c) => c[1]?.method === "PUT");
    expect(JSON.parse(putCall?.[1].body)).toMatchObject({
      action: "dictate_toggle",
      hotkey: "ctrl+shift+d",
    });
  });

  it("PUTs the chosen action + combo on save", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => FULL })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ok: true,
          action: "hangup",
          hotkey: "ctrl+shift+h",
          persisted: true,
          restart_required: true,
        }),
      })
      .mockResolvedValue({ ok: true, json: async () => FULL });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useKeybinds());
    await waitFor(() => expect(result.current.config).not.toBeNull());
    await act(async () => {
      await result.current.saveKeybind("hangup", "ctrl+shift+h");
    });

    const putCall = fetchMock.mock.calls.find((c) => c[1]?.method === "PUT");
    expect(putCall?.[0]).toBe("/api/settings/keybinds");
    expect(JSON.parse(putCall?.[1].body)).toMatchObject({
      action: "hangup",
      hotkey: "ctrl+shift+h",
    });
  });

  describe("saving a global shortcut on macOS asks for Input Monitoring", () => {
    const NEEDS = { ...FULL, shortcuts_status: { state: "needs_input_monitoring", detail: "" } };
    const SAVED = { ok: true, action: "call", hotkey: "ctrl+j", persisted: true, restart_required: false };

    function stubFetch(config: unknown, requestOk = true) {
      const calls: Array<{ url: string; method: string; body?: unknown }> = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = String(input);
          const method = init?.method ?? "GET";
          calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
          if (method === "PUT") return { ok: true, status: 200, json: async () => SAVED } as Response;
          if (url.includes("/api/permissions/")) {
            return { ok: requestOk, status: requestOk ? 200 : 429, json: async () => ({ outcome: "pending" }) } as Response;
          }
          return { ok: true, status: 200, json: async () => config } as Response;
        }),
      );
      return calls;
    }

    afterEach(() => {
      delete (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP;
    });

    async function save(embedded: boolean) {
      if (embedded) (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = true;
      const { result } = renderHook(() => useKeybinds());
      await waitFor(() => expect(result.current.config).not.toBeNull());
      await act(async () => {
        await result.current.saveKeybind("call", "ctrl+j");
      });
    }

    it("asks once, from the save, with the feature", async () => {
      const calls = stubFetch(NEEDS);
      await save(true);

      const asks = calls.filter((c) => c.url.includes("/api/permissions/input_monitoring/request"));
      expect(asks).toHaveLength(1);
      expect(asks[0].body).toEqual({ feature: "global_shortcuts" });
    });

    it("asks nothing when the shortcuts already work, or off macOS (status ready)", async () => {
      const calls = stubFetch({ ...FULL, shortcuts_status: { state: "ready", detail: "" } });
      await save(true);

      expect(calls.some((c) => c.url.includes("/api/permissions/"))).toBe(false);
    });

    it("never asks from a remote browser (the dialog would open on another computer)", async () => {
      const calls = stubFetch(NEEDS);
      await save(false);

      expect(calls.some((c) => c.url.includes("/api/permissions/"))).toBe(false);
    });

    it("keeps the save when the ask fails", async () => {
      stubFetch(NEEDS, false);
      await save(true); // does not throw
    });
  });
});
