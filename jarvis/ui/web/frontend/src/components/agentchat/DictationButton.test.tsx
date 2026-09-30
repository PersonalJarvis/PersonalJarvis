import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { DictationButton, formatElapsed } from "@/components/agentchat/DictationButton";

function renderButton(dictating: boolean, onToggle = vi.fn()) {
  return render(
    <DictationButton
      dictating={dictating}
      onToggle={onToggle}
      startLabel="Dictate"
      stopLabel="Stop dictation"
    />,
  );
}

describe("DictationButton", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  test("idle, it is only the microphone and starts a dictation", () => {
    const onToggle = vi.fn();
    renderButton(false, onToggle);
    expect(screen.queryByTestId("dictation-status")).toBeNull();
    const mic = screen.getByTestId("dictation-button");
    expect(mic.getAttribute("aria-label")).toBe("Dictate");
    expect(mic.hasAttribute("data-jarvis-dictation-trigger")).toBe(true);
    fireEvent.click(mic);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  test("live, it announces itself, shows the waveform and counts up", () => {
    renderButton(true);
    const pill = screen.getByTestId("dictation-status");
    expect(pill.getAttribute("role")).toBe("status");
    expect(pill.textContent).toContain("Listening");
    expect(pill.textContent).toContain("0:00");
    expect(screen.getByTestId("dictation-waveform").children.length).toBeGreaterThan(0);
    act(() => {
      vi.advanceTimersByTime(7_000);
    });
    expect(screen.getByTestId("dictation-status").textContent).toContain("0:07");
  });

  test("the stop sits inside the pill and ends the dictation", () => {
    const onToggle = vi.fn();
    renderButton(true, onToggle);
    const stop = screen.getByTestId("dictation-stop");
    expect(stop.getAttribute("aria-label")).toBe("Stop dictation");
    fireEvent.click(stop);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  test("the clock restarts with the next dictation instead of carrying over", () => {
    const view = renderButton(true);
    act(() => {
      vi.advanceTimersByTime(65_000);
    });
    expect(screen.getByTestId("dictation-status").textContent).toContain("1:05");
    view.rerender(<DictationButton dictating={false} onToggle={vi.fn()} startLabel="Dictate" stopLabel="Stop dictation" />);
    view.rerender(<DictationButton dictating onToggle={vi.fn()} startLabel="Dictate" stopLabel="Stop dictation" />);
    expect(screen.getByTestId("dictation-status").textContent).toContain("0:00");
  });

  test("formats elapsed time as m:ss", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(102_400)).toBe("1:42");
  });
});
