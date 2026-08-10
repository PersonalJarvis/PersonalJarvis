import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useVoiceMode } from "./useVoiceMode";

function response(
  available: boolean,
  overrides: Record<string, unknown> = {},
): Response {
  return {
    ok: true,
    json: async () => ({
      mode: "realtime",
      realtime_available: available,
      requires_webrtc_offer: true,
      active_provider: available ? "codex-subscription-realtime" : null,
      active_provider_label: available ? "ChatGPT subscription (Codex)" : null,
      active_model: available ? "auto" : null,
      active_model_label: available
        ? "ChatGPT-Live (model chosen by OpenAI)"
        : null,
      session_active: false,
      active_session_mode: null,
      active_session_provider: "",
      active_session_model: "",
      transitioning: false,
      ...overrides,
    }),
  } as Response;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useVoiceMode realtime discovery", () => {
  it("rechecks a transient cold-boot unavailable result", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(false))
      .mockResolvedValue(response(true));
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useVoiceMode(), { wrapper });
    await waitFor(() => expect(result.current.statusKnown).toBe(true));
    expect(result.current.realtimeAvailable).toBe(false);

    await waitFor(
      () => expect(result.current.realtimeAvailable).toBe(true),
      { timeout: 2_500 },
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ cache: "no-store" });
    expect(result.current.activeModel).toBe(
      "ChatGPT-Live (model chosen by OpenAI)",
    );
  });

  it("does not show a handshake when an idle realtime provider changes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(response(true, { handshake_budget_s: 135 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useVoiceMode(), { wrapper });
    await waitFor(() => expect(result.current.statusKnown).toBe(true));

    act(() => {
      window.dispatchEvent(new CustomEvent("jarvis:realtime-switched"));
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(result.current.connecting).toBe(false);
  });

  it("does not show a handshake when realtime is selected while idle", async () => {
    let getCount = 0;
    const fetchMock = vi.fn().mockImplementation((_url, init?: RequestInit) => {
      if (init?.method === "PUT") {
        return Promise.resolve(response(true, { mode: "realtime" }));
      }
      getCount += 1;
      return Promise.resolve(
        response(true, {
          mode: getCount === 1 ? "pipeline" : "realtime",
          handshake_budget_s: 135,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useVoiceMode(), { wrapper });
    await waitFor(() => expect(result.current.mode).toBe("pipeline"));

    act(() => result.current.setMode("realtime"));

    await waitFor(() => expect(result.current.mode).toBe("realtime"));
    await waitFor(() => expect(result.current.isSaving).toBe(false));
    expect(result.current.connecting).toBe(false);
  });
});
