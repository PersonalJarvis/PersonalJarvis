import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useRestartApp } from "@/hooks/useRestartApp";
import { useEventStore } from "@/store/events";

function stubRestart(status: number, body: unknown = {}) {
  const fetchMock = vi.fn(async () => ({ ok: status < 400, status, json: async () => body }) as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const messages = () => useEventStore.getState().toasts.map((toast) => toast.message);

describe("useRestartApp", () => {
  beforeEach(() => useEventStore.setState({ toasts: [] }));
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("says what it was told to when the restart cannot start, instead of the raw error", async () => {
    stubRestart(500);
    const { result } = renderHook(() => useRestartApp());

    await act(async () => {
      await result.current.restart({ failureMessage: "Could not restart. Quit and reopen it manually." });
    });

    expect(messages()).toEqual(["Could not restart. Quit and reopen it manually."]);
    expect(result.current.restarting).toBe(false);
  });

  it("keeps the raw error for callers that give no message", async () => {
    stubRestart(500);
    const { result } = renderHook(() => useRestartApp());

    await act(async () => {
      await result.current.restart();
    });

    expect(messages()).toEqual(["restart-failed:500"]);
  });

  it("arms a forced restart when missions are running, and resends with force", async () => {
    const fetchMock = stubRestart(409, { detail: { missions: [{ id: "a" }, { id: "b" }] } });
    const { result } = renderHook(() => useRestartApp());

    await act(async () => {
      await result.current.restart();
    });
    expect(messages()[0]).toContain("2 ");
    expect(result.current.forceArmed).toBe(true);

    await act(async () => {
      await result.current.restart();
    });
    expect(fetchMock).toHaveBeenLastCalledWith("/api/settings/restart-app?force=true", { method: "POST" });
  });
});
