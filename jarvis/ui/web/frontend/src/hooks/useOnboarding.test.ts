import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useOnboarding } from "./useOnboarding";

afterEach(() => vi.restoreAllMocks());

const STATE = {
  completed: false,
  current_step: null,
  skipped_steps: [],
  terms: { accepted: false, accepted_version: null, current_version: "1.0" },
  wake_word_acknowledged: false,
  legal_references: [{ label: "EUIPO", url: "https://euipo.europa.eu/eSearch/" }],
  steps: ["welcome", "terms", "finish"],
};

it("loads state and posts a step", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(STATE) });
    }),
  );

  const { result } = renderHook(() => useOnboarding());
  await waitFor(() => expect(result.current.state?.terms.current_version).toBe("1.0"));

  await act(async () => {
    await result.current.saveStep("terms", ["mic-test"]);
  });
  const put = calls.find(([u, i]) => u === "/api/onboarding/step" && i?.method === "POST");
  expect(put).toBeDefined();
  expect(JSON.parse(put![1]!.body as string).step).toBe("terms");
});

it("retries a warming 503 until the backend answers, then resolves state", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce({ ok: false, status: 503, json: () => Promise.resolve({}) })
    .mockResolvedValueOnce({ ok: false, status: 503, json: () => Promise.resolve({}) })
    .mockResolvedValue({ ok: true, json: () => Promise.resolve(STATE) });
  vi.stubGlobal("fetch", fetchMock);

  const { result } = renderHook(() => useOnboarding());
  await waitFor(() => expect(result.current.state?.completed).toBe(false), {
    timeout: 15000,
  });
  expect(result.current.error).toBeNull();
  expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3);
}, 20000);

it("gives up after the bounded retry window (fail-open preserved)", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new Error("net down")),
  );
  const { result } = renderHook(() => useOnboarding({ retryDelaysMs: [10, 20] }));
  await waitFor(() => expect(result.current.error).not.toBeNull(), { timeout: 5000 });
  expect(result.current.loading).toBe(false);
});

it("complete() surfaces a failed completion (throws, no event)", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(STATE) });
    }),
  );
  let dispatched = false;
  const onChanged = () => { dispatched = true; };
  window.addEventListener("jarvis:onboarding-changed", onChanged);

  const { result } = renderHook(() => useOnboarding());
  await waitFor(() => expect(result.current.state).not.toBeNull());

  await expect(result.current.complete()).rejects.toThrow();
  expect(dispatched).toBe(false);

  window.removeEventListener("jarvis:onboarding-changed", onChanged);
});
