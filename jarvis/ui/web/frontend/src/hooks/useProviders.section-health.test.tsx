import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PROVIDER_BACKEND_UNREACHABLE,
  sectionHealthForSubject,
  sectionHealthFromProviderTest,
  type ProviderTestResult,
  type SectionHealth,
  type SectionHealthResponse,
  useProviders,
  useSectionHealth,
} from "./useProviders";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function responseFor(health: SectionHealth): SectionHealthResponse {
  return {
    sections: { brain: health },
    checked_at: 1,
    cached: false,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("provider status loading", () => {
  it("labels a local API outage and reconnects automatically", async () => {
    const provider = { id: "openai-realtime", configured: true };
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonResponse({ providers: [provider] }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useProviders({ retryDelaysMs: [100] }));
    await waitFor(() =>
      expect(result.current.error).toBe(PROVIDER_BACKEND_UNREACHABLE),
    );
    await waitFor(() => expect(result.current.providers).toEqual([provider]));

    expect(result.current.error).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not let an older response overwrite newer provider truth", async () => {
    const oldRequest = deferred<Response>();
    const freshProvider = { id: "codex-subscription-realtime", configured: true };
    const staleProvider = { id: "codex-subscription-realtime", configured: false };
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => oldRequest.promise)
      .mockResolvedValueOnce(jsonResponse({ providers: [freshProvider] }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useProviders());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => {
      await result.current.refetch();
    });
    expect(result.current.providers).toEqual([freshProvider]);

    await act(async () => {
      oldRequest.resolve(jsonResponse({ providers: [staleProvider] }));
      await oldRequest.promise;
    });
    expect(result.current.providers).toEqual([freshProvider]);
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ cache: "no-store" });
  });
});

describe("provider-bound section health", () => {
  it.each([
    "gemini",
    "openrouter",
    "openrouter-tts",
    "groq-api",
    "openai-realtime",
    "gemini-live",
    "codex",
    "telephony",
  ])("accepts %s only for that exact subject", (subjectId) => {
    const health: SectionHealth = {
      status: "error",
      reason: "timeout",
      detail: `${subjectId}: timeout`,
      subject_id: subjectId,
    };

    expect(sectionHealthForSubject(health, subjectId)).toBe(health);
    expect(sectionHealthForSubject(health, `${subjectId}-replacement`)).toBeUndefined();
  });

  it.each([
    ["ok", "ok"],
    ["not_configured", "needs_setup"],
    ["bad_key", "error"],
    ["no_credits", "error"],
    ["rate_limited", "error"],
    ["model_unavailable", "error"],
    ["unreachable", "error"],
    ["error", "error"],
  ] as const)("maps a manual %s result to %s", (providerStatus, sectionStatus) => {
    const result: ProviderTestResult = {
      provider: "openrouter",
      status: providerStatus,
      detail: "probe result",
      latency_ms: 10,
      integration_ok: providerStatus !== "unreachable" && providerStatus !== "error",
    };

    expect(sectionHealthFromProviderTest(result, "OpenRouter")).toMatchObject({
      status: sectionStatus,
      reason: providerStatus,
      subject_id: "openrouter",
    });
  });

  it("keeps a fresh OpenRouter test when an older NVIDIA request finishes later", async () => {
    const oldRequest = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => oldRequest.promise)
      .mockImplementation(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSectionHealth());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    act(() => {
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-tested", {
          detail: {
            section: "brain",
            provider: "openrouter",
            provider_label: "OpenRouter",
            active: true,
            result: {
              provider: "openrouter",
              status: "ok",
              detail: "working",
              latency_ms: 25,
              integration_ok: true,
            },
          },
        }),
      );
    });
    expect(result.current.health.brain).toMatchObject({
      status: "ok",
      subject_id: "openrouter",
    });

    await act(async () => {
      oldRequest.resolve(
        jsonResponse(
          responseFor({
            status: "error",
            reason: "timeout",
            detail: "NVIDIA NIM: timeout after 60.0s",
            subject_id: "nvidia",
          }),
        ),
      );
      await Promise.resolve();
    });

    expect(result.current.health.brain).toMatchObject({
      status: "ok",
      subject_id: "openrouter",
    });
  });

  it("clears the old result immediately while a provider switch is pending", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        responseFor({
          status: "error",
          reason: "timeout",
          detail: "NVIDIA NIM: timeout after 60.0s",
          subject_id: "nvidia",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSectionHealth());
    await waitFor(() => expect(result.current.health.brain?.subject_id).toBe("nvidia"));

    act(() => {
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-selection-pending", {
          detail: { section: "brain", provider: "openrouter" },
        }),
      );
    });

    expect(result.current.health.brain).toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
