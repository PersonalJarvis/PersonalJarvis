import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useVoiceMode } from "./useVoiceMode";

function response(available: boolean): Response {
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
});
