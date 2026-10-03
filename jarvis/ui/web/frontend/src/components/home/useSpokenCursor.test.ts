import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSpokenCursor } from "@/components/home/useSpokenCursor";
import { beginSpeechPlayback, clearSpeechPlayback, publishTimedSpeechPlayback, setTimedSpeechSession, updateSpeechPlayback } from "@/lib/speechPlayback";
import { voiceOutputLevelRef } from "@/lib/voiceOutputLevel";

afterEach(() => {
  clearSpeechPlayback();
  setTimedSpeechSession("");
  voiceOutputLevelRef.current = null;
  vi.useRealTimers();
});

describe("playback-confirmed spoken cursor", () => {
  it("preserves heard prefixes when captions grow ahead of metadata or are truncated", () => {
    setTimedSpeechSession("growing");
    publishTimedSpeechPlayback("live:growing:answer", "One two", 3);
    const { result, rerender } = renderHook(({ text }) => useSpokenCursor(text, true, "live:growing:answer"), {
      initialProps: { text: "One two" },
    });
    expect(result.current).toBe(3);
    rerender({ text: "One two three" });
    expect(result.current).toBe(3);
    act(() => publishTimedSpeechPlayback("live:growing:answer", "One two three", 7));
    expect(result.current).toBe(7);
    rerender({ text: "One two" });
    expect(result.current).toBe(7);
    rerender({ text: "Different words" });
    expect(result.current).toBe(0);
    act(() => publishTimedSpeechPlayback("live:growing:unicode", "A😀", 3));
    const unicode = renderHook(() => useSpokenCursor("A😎", true, "live:growing:unicode"));
    expect(unicode.result.current).toBe(1);
  });
  it("keeps a new bus caption grey before the audio socket's metadata arrives", () => {
    setTimedSpeechSession("current");
    const { result } = renderHook(() => useSpokenCursor("New caption", true, "live:current:new"));
    const old = renderHook(() => useSpokenCursor("Old caption", true, "live:previous:old"));
    expect(result.current).toBe(0);
    expect(old.result.current).toBeNull();
    act(() => publishTimedSpeechPlayback("live:current:new", "New caption", 3));
    expect(result.current).toBe(3);
  });
  it("does not invent progress from time, levels or the speaking state", () => {
    vi.useFakeTimers();
    voiceOutputLevelRef.current = 1;
    const { result } = renderHook(() => useSpokenCursor("A generated answer", true));
    act(() => vi.advanceTimersByTime(60_000));
    expect(result.current).toBeNull();
  });

  it("follows actual boundaries at any rate and remains still during pauses", () => {
    vi.useFakeTimers();
    const text = "One two three four";
    const id = beginSpeechPlayback(text);
    const { result, rerender } = renderHook(({ active }) => useSpokenCursor(text, active), {
      initialProps: { active: true },
    });
    expect(result.current).toBe(0);
    act(() => vi.advanceTimersByTime(20_000));
    expect(result.current).toBe(0);
    act(() => updateSpeechPlayback(id, "playing", 3));
    expect(result.current).toBe(3);
    act(() => updateSpeechPlayback(id, "playing", 7));
    expect(result.current).toBe(7);
    act(() => updateSpeechPlayback(id, "paused"));
    rerender({ active: false });
    act(() => vi.advanceTimersByTime(60_000));
    expect(result.current).toBe(7);
    act(() => updateSpeechPlayback(id, "playing", 13));
    expect(result.current).toBe(13);
    act(() => updateSpeechPlayback(id, "ended", text.length));
    expect(result.current).toBe(text.length);
  });

  it("does not mark an unheard tail as spoken on cancellation or a status change", () => {
    const text = "Heard then cancelled";
    const id = beginSpeechPlayback(text);
    const { result, rerender } = renderHook(({ active }) => useSpokenCursor(text, active), {
      initialProps: { active: true },
    });
    act(() => updateSpeechPlayback(id, "playing", 5));
    act(() => updateSpeechPlayback(id, "cancelled"));
    rerender({ active: false });
    act(() => updateSpeechPlayback(id, "ended", text.length));
    expect(result.current).toBe(5);
    act(() => beginSpeechPlayback("A new reply"));
    expect(result.current).toBe(5);
  });

  it("does not give a repeated reply the previous reply's position", () => {
    const text = "The same answer";
    const first = beginSpeechPlayback(text);
    const oldLine = renderHook(({ eligible }) => useSpokenCursor(text, eligible), {
      initialProps: { eligible: true },
    });
    act(() => updateSpeechPlayback(first, "playing", 8));
    act(() => updateSpeechPlayback(first, "cancelled"));
    oldLine.rerender({ eligible: false });
    let second = 0;
    act(() => { second = beginSpeechPlayback(text); });
    const newLine = renderHook(() => useSpokenCursor(text, true));
    expect(newLine.result.current).toBe(0);
    act(() => updateSpeechPlayback(first, "ended", text.length));
    act(() => updateSpeechPlayback(second, "playing", 3));
    expect(newLine.result.current).toBe(3);
    expect(oldLine.result.current).toBe(8);
  });

  it("invalidates positions when the transcript is corrected", () => {
    const first = beginSpeechPlayback("Original text");
    const { result, rerender } = renderHook(({ text }) => useSpokenCursor(text, true), {
      initialProps: { text: "Original text" },
    });
    act(() => updateSpeechPlayback(first, "playing", 8));
    rerender({ text: "Corrected text" });
    expect(result.current).toBeNull();
  });
});
