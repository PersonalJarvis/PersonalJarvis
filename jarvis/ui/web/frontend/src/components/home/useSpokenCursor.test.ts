import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CHARS_PER_SECOND, useSpokenCursor, wordEnd } from "@/components/home/useSpokenCursor";
import { voiceOutputLevelRef } from "@/lib/voiceOutputLevel";

afterEach(() => {
  vi.useRealTimers();
  voiceOutputLevelRef.current = null;
});

describe("wordEnd", () => {
  it("lights a word up whole, never half-way", () => {
    expect(wordEnd("Morgen ist Freitag", 0)).toBe(0);
    expect(wordEnd("Morgen ist Freitag", 2)).toBe(6);
    expect(wordEnd("Morgen ist Freitag", 7.4)).toBe(10);
    expect(wordEnd("Morgen ist Freitag", 12)).toBe(18);
    expect(wordEnd("Morgen ist Freitag", 99)).toBe(18);
  });
});

describe("useSpokenCursor", () => {
  it("draws the text plainly while nothing is being spoken", () => {
    const { result } = renderHook(() => useSpokenCursor("Hallo Ruben", false));
    expect(result.current).toBeNull();
  });

  it("moves while audio plays and waits while it is silent", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    const text = "Tomorrow is Friday, the second of October. Anything special planned?";
    voiceOutputLevelRef.current = 0.5;
    const { result } = renderHook(() => useSpokenCursor(text, true));
    expect(result.current).toBe(0);

    act(() => vi.advanceTimersByTime(1000));
    const afterOneSecond = result.current ?? 0;
    expect(afterOneSecond).toBeGreaterThan(CHARS_PER_SECOND / 2);
    expect(afterOneSecond).toBeLessThan(text.length);

    voiceOutputLevelRef.current = 0;
    act(() => vi.advanceTimersByTime(1000));
    const paused = result.current ?? 0;
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current).toBe(paused);
  });

  it("starts a new answer from its beginning", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    const { result, rerender } = renderHook(({ text }) => useSpokenCursor(text, true), {
      initialProps: { text: "A first answer with a few words" },
    });
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current).toBeGreaterThan(0);
    rerender({ text: "Quite another answer" });
    expect(result.current).toBeLessThanOrEqual("Quite".length);
  });
});
