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
});
