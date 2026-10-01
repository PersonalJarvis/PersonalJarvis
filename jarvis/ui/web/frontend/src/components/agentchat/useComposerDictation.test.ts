import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";

import { useComposerDictation } from "./useComposerDictation";

describe("useComposerDictation", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useEventStore.setState({ dictating: false, dictationText: "" });
  });
  afterEach(() => vi.useRealTimers());

  it("does not mirror interim text into the box", () => {
    const setValue = vi.fn();
    const { result } = renderHook(() => useComposerDictation(setValue));
    act(() => result.current.start());
    act(() => useEventStore.getState().setDictationInterim("hello wor"));
    expect(setValue).not.toHaveBeenCalled();
  });

  it("appends the final transcript once", () => {
    let box = "Fix";
    const setValue = (next: string | ((current: string) => string)) => {
      box = typeof next === "function" ? next(box) : next;
    };
    renderHook(() => useComposerDictation(setValue));
    act(() => useEventStore.getState().commitDictation("the login bug"));
    expect(box).toBe("Fix the login bug");
  });

  it("sends after the final transcript when Send ended the dictation", () => {
    const onSend = vi.fn();
    const { result } = renderHook(() => useComposerDictation(vi.fn(), onSend));
    act(() => result.current.start());
    act(() => result.current.stopAndSend());
    expect(onSend).not.toHaveBeenCalled();
    act(() => {
      useEventStore.getState().commitDictation("ship it");
      useEventStore.getState().noteDictationFinal();
    });
    act(() => vi.runOnlyPendingTimers());
    expect(onSend).toHaveBeenCalledTimes(1);
    // The fallback timer was cleared: no second send.
    act(() => vi.advanceTimersByTime(10_000));
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("still sends when no final transcript arrives", () => {
    const onSend = vi.fn();
    const { result } = renderHook(() => useComposerDictation(vi.fn(), onSend));
    act(() => result.current.start());
    act(() => result.current.stopAndSend());
    act(() => vi.advanceTimersByTime(7_999));
    expect(onSend).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    act(() => vi.runOnlyPendingTimers());
    expect(onSend).toHaveBeenCalledTimes(1);
  });
});
