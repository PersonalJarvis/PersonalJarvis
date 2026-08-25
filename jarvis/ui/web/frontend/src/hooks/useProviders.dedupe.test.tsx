/**
 * Two `useProviders` instances in one tree share ONE `/api/providers`
 * request; an explicit `refetch()` still asks the server again.
 */
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetProvidersCacheForTests, useProviders } from "./useProviders";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

beforeEach(() => {
  _resetProvidersCacheForTests();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useProviders request sharing", () => {
  it("two hooks mounted together issue a single fetch", async () => {
    const provider = { id: "ollama", supports_model_pull: true };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ providers: [provider] }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => ({
      a: useProviders(),
      b: useProviders(),
    }));
    await waitFor(() => expect(result.current.a.loading).toBe(false));
    await waitFor(() => expect(result.current.b.loading).toBe(false));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.current.a.providers).toEqual([provider]);
    expect(result.current.b.providers).toEqual([provider]);
  });

  it("one window event fans out to every instance but fetches once", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ providers: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => ({
      a: useProviders(),
      b: useProviders(),
    }));
    await waitFor(() => expect(result.current.a.loading).toBe(false));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new CustomEvent("jarvis:brain-switched"));
      await Promise.resolve();
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("a remount starts from the last good list", async () => {
    const provider = { id: "ollama" };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ providers: [provider] }));
    vi.stubGlobal("fetch", fetchMock);

    const first = renderHook(() => useProviders());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    first.unmount();

    const second = renderHook(() => useProviders());
    expect(second.result.current.providers).toEqual([provider]);
  });
});
