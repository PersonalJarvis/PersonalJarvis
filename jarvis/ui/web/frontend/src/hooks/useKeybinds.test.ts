import { renderHook, waitFor, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useKeybinds } from "./useHotkey";

const FULL = {
  keybinds: { call: "f3+f4", hangup: "f1+f2" },
  defaults: { call: "f3+f4", hangup: "f1+f2" },
  suggestions: [],
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
});
