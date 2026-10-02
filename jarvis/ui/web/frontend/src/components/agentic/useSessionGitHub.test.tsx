import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useSessionGitHub, type SessionGitHubStatus } from "./useSessionGitHub";
import { resetConnectBudgetForTests } from "@/lib/connectBudget";

const value: SessionGitHubStatus = {
  repo: "owner/repo", branch: "feature/a", url: "https://github.com/owner/repo/tree/feature/a",
  published: true, available: true, reason: "", fetched_at: 1, state: "open", number: 1,
  ci_stale: false, ci: { state: "none", url: "", total: 0, passed: 0, failed: 0, pending: 0, running: 0, names: [], commit: "" },
};

beforeEach(() => {
  vi.useFakeTimers();
  resetConnectBudgetForTests();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
});
afterEach(() => {
  cleanup();
  resetConnectBudgetForTests();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("shares one poll per workspace and refreshes PR/CI transitions", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ panes: { t1: value, t2: value } }) });
  vi.stubGlobal("fetch", fetcher);
  const a = renderHook(() => useSessionGitHub("w", "t1"));
  const b = renderHook(() => useSessionGitHub("w", "t2"));
  await act(() => vi.advanceTimersByTimeAsync(300));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(a.result.current?.state).toBe("open");
  expect(b.result.current?.state).toBe("open");
  fetcher.mockResolvedValue({ ok: true, json: async () => ({ panes: { t1: { ...value, state: "merged" } } }) });
  await act(() => vi.advanceTimersByTimeAsync(20_000));
  expect(a.result.current?.state).toBe("merged");
  expect(b.result.current).toBeNull();
  a.unmount(); b.unmount();
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("hides obsolete identity on a branch/workspace change and marks failed refreshes", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ panes: { t1: value } }) });
  vi.stubGlobal("fetch", fetcher);
  const hook = renderHook(({ workspace }) => useSessionGitHub(workspace, "t1"), { initialProps: { workspace: "w" } });
  await act(() => vi.advanceTimersByTimeAsync(300));
  fetcher.mockRejectedValue(new Error("offline"));
  await act(() => vi.advanceTimersByTimeAsync(20_000));
  expect(hook.result.current?.available).toBe(false);
  hook.rerender({ workspace: "another" });
  expect(hook.result.current).toBeNull();
});

it("pauses while hidden and resumes through the shared budget", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ panes: {} }) });
  vi.stubGlobal("fetch", fetcher);
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  renderHook(() => useSessionGitHub("w", "t1"));
  await act(() => vi.advanceTimersByTimeAsync(60_000));
  expect(fetcher).not.toHaveBeenCalled();
  hidden.mockReturnValue(false);
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  expect(fetcher).not.toHaveBeenCalled();
  await act(() => vi.advanceTimersByTimeAsync(600));
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("aborts an in-flight poll when its last subscriber unmounts", async () => {
  const fetcher = vi.fn().mockImplementation(() => new Promise(() => {}));
  vi.stubGlobal("fetch", fetcher);
  const hook = renderHook(() => useSessionGitHub("w", "t1"));
  await act(() => vi.advanceTimersByTimeAsync(300));
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  hook.unmount();
  expect(signal.aborted).toBe(true);
});
