import { StrictMode } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { CanvasActivity } from "./useCanvasAwake";
import { useCanvasInterval } from "./useCanvasInterval";

function Clock({ onTick, enabled = true }: { onTick: () => void; enabled?: boolean }) {
  useCanvasInterval(onTick, 1000, enabled);
  return null;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); });

test("retained clocks stop while hidden and resume without replaying missed ticks", () => {
  const ticks: number[] = [];
  const onTick = () => ticks.push(Date.now());
  const tree = (active: boolean) => <CanvasActivity.Provider value={active}><Clock onTick={onTick} /></CanvasActivity.Provider>;
  const view = render(tree(true));
  act(() => vi.advanceTimersByTime(1000));
  expect(ticks).toHaveLength(1);

  view.rerender(tree(false));
  expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(60_000));
  expect(ticks).toHaveLength(1);

  view.rerender(tree(true));
  expect(ticks).toHaveLength(1);
  act(() => vi.advanceTimersByTime(999));
  expect(ticks).toHaveLength(1);
  act(() => vi.advanceTimersByTime(1));
  expect(ticks).toHaveLength(2);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

test("a hidden initial mount and a locally disabled clock never schedule work", () => {
  const ticks: number[] = [];
  const tree = (active: boolean, enabled: boolean) => <CanvasActivity.Provider value={active}>
    <Clock enabled={enabled} onTick={() => ticks.push(1)} />
  </CanvasActivity.Provider>;
  const view = render(tree(false, true));
  expect(vi.getTimerCount()).toBe(0);
  view.rerender(tree(true, false));
  expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(5000));
  expect(ticks).toEqual([]);
  view.rerender(tree(true, true));
  act(() => vi.advanceTimersByTime(1000));
  expect(ticks).toEqual([1]);
});

test("rerenders use the latest callback without restarting the cadence or duplicating StrictMode timers", () => {
  const ticks: string[] = [];
  const tree = (label: string) => <StrictMode><Clock onTick={() => ticks.push(label)} /></StrictMode>;
  const view = render(tree("old"));
  expect(vi.getTimerCount()).toBe(1);
  act(() => vi.advanceTimersByTime(600));
  view.rerender(tree("new"));
  act(() => vi.advanceTimersByTime(400));
  expect(ticks).toEqual(["new"]);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
