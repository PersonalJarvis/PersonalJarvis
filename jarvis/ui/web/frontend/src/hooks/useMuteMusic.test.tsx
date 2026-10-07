import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useMuteMusic } from "./useMuteMusic";

vi.mock("@/lib/bootStagger", () => ({ bootSettled: () => Promise.resolve() }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** A fake backend: the GET reads the switch, the PUT answers like the real route. */
function stubBackend(put: { ok: boolean; body: unknown; status?: number }) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        return Promise.resolve({
          ok: put.ok,
          status: put.status ?? (put.ok ? 200 : 500),
          json: () => Promise.resolve(put.body),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ enabled: false }) });
    }),
  );
}

it("saves the switch and reports what the backend answered", async () => {
  stubBackend({
    ok: true,
    body: { ok: true, enabled: true, persisted: true, applied_live: true },
  });

  const { result } = renderHook(() => useMuteMusic());
  await waitFor(() => expect(result.current.enabled).toBe(false));

  let saved: unknown;
  await act(async () => {
    saved = await result.current.setEnabled(true);
  });

  expect(result.current.enabled).toBe(true);
  expect(saved).toMatchObject({ ok: true, enabled: true, persisted: true, applied_live: true });
});

it("stays quiet when the switch could not be saved", async () => {
  stubBackend({ ok: false, status: 500, body: { detail: "config is locked" } });

  const { result } = renderHook(() => useMuteMusic());
  await waitFor(() => expect(result.current.enabled).toBe(false));

  await act(async () => {
    await expect(result.current.setEnabled(true)).rejects.toThrow("config is locked");
  });

  expect(result.current.enabled).toBe(false);
});
