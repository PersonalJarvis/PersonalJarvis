import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { resetConnectBudgetForTests } from "@/lib/connectBudget";
import { useHistoryPolling } from "./useHistoryPolling";

let hidden: boolean | undefined;
const originalHidden = Object.getOwnPropertyDescriptor(document, "hidden");
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  vi.spyOn(document, "hasFocus").mockReturnValue(false);
  hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  resetConnectBudgetForTests();
});
afterEach(() => {
  cleanup();
  resetConnectBudgetForTests();
  vi.restoreAllMocks();
  vi.useRealTimers();
  if (originalHidden) Object.defineProperty(document, "hidden", originalHidden);
  else delete (document as unknown as { hidden?: boolean }).hidden;
});
async function advance(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }

it("one owner handles staggered sidebar/stage/rail mounts and lives until the last reader leaves", async () => {
  const read = vi.fn(async () => {});
  const first = renderHook(() => useHistoryPolling(read));
  await advance();
  const second = renderHook(() => useHistoryPolling(read));
  await advance();
  expect(read).toHaveBeenCalledTimes(1);
  first.unmount();
  await advance(5000);
  expect(read).toHaveBeenCalledTimes(2);
  second.unmount();
  await advance(20_000);
  expect(read).toHaveBeenCalledTimes(2);
});

it("never overlaps a slow read and retries only on the next scheduled tick after failure", async () => {
  let finish!: () => void;
  const read = vi.fn().mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; }))
    .mockRejectedValueOnce(new Error("offline")).mockResolvedValue(undefined);
  renderHook(() => useHistoryPolling(read));
  await advance(30_000);
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => { finish(); });
  await advance(5000);
  expect(read).toHaveBeenCalledTimes(2);
  await advance(4999);
  expect(read).toHaveBeenCalledTimes(2);
  await advance(1);
  expect(read).toHaveBeenCalledTimes(3);
});

it("skips hidden requests and catches up through the shared budget when visible", async () => {
  hidden = true;
  const read = vi.fn(async () => {});
  renderHook(() => useHistoryPolling(read));
  await advance(20_000);
  expect(read).not.toHaveBeenCalled();
  hidden = false;
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  expect(read).not.toHaveBeenCalled();
  await advance(125);
  expect(read).toHaveBeenCalledTimes(1);
});

it("recovers when a WebView misses visibilitychange and tolerates missing or inconsistent visibility APIs", async () => {
  hidden = true;
  const read = vi.fn(async () => {});
  const view = renderHook(() => useHistoryPolling(read));
  hidden = false;
  await advance(5125);
  expect(read).toHaveBeenCalledTimes(1);
  view.unmount();
  hidden = undefined;
  const unknown = vi.fn(async () => {});
  renderHook(() => useHistoryPolling(unknown));
  expect(unknown).toHaveBeenCalledTimes(1);
  hidden = true;
  vi.mocked(document.hasFocus).mockReturnValue(true);
  const focused = vi.fn(async () => {});
  renderHook(() => useHistoryPolling(focused));
  expect(focused).toHaveBeenCalledTimes(1);
});

it("does not merge readers belonging to different stores/surfaces", async () => {
  const jarvis = vi.fn(async () => {});
  const society = vi.fn(async () => {});
  renderHook(() => { useHistoryPolling(jarvis); useHistoryPolling(society); });
  await advance(5000);
  expect(jarvis).toHaveBeenCalledTimes(2);
  expect(society).toHaveBeenCalledTimes(2);
});
