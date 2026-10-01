import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { PERMISSIONS_REFRESH_EVENT } from "./usePermissions";
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

it("tells the permission views to look again once the switch is saved", async () => {
  // Switching "mute music" on makes the Music/Spotify Automation row wanted,
  // and the permission banner has no other way to learn that before the window
  // is next focused.
  stubBackend({
    ok: true,
    body: { ok: true, enabled: true, persisted: true, applied_live: true },
  });
  const refreshed = vi.fn();
  window.addEventListener(PERMISSIONS_REFRESH_EVENT, refreshed);

  try {
    const { result } = renderHook(() => useMuteMusic());
    await waitFor(() => expect(result.current.enabled).toBe(false));

    await act(async () => {
      await result.current.setEnabled(true);
    });

    expect(result.current.enabled).toBe(true);
    expect(refreshed).toHaveBeenCalledTimes(1);
  } finally {
    window.removeEventListener(PERMISSIONS_REFRESH_EVENT, refreshed);
  }
});

it("stays quiet when the switch could not be saved", async () => {
  stubBackend({ ok: false, status: 500, body: { detail: "config is locked" } });
  const refreshed = vi.fn();
  window.addEventListener(PERMISSIONS_REFRESH_EVENT, refreshed);

  try {
    const { result } = renderHook(() => useMuteMusic());
    await waitFor(() => expect(result.current.enabled).toBe(false));

    await act(async () => {
      await expect(result.current.setEnabled(true)).rejects.toThrow("config is locked");
    });

    expect(result.current.enabled).toBe(false);
    expect(refreshed).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener(PERMISSIONS_REFRESH_EVENT, refreshed);
  }
});
