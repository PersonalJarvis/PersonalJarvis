import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { DictationStatus } from "@/components/agentchat/DictationStatus";
import { useEventStore } from "@/store/events";

describe("DictationStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useEventStore.setState({ dictating: false });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  test("stays out of the way until the microphone is actually open", () => {
    render(<DictationStatus />);
    expect(screen.queryByTestId("dictation-status")).toBeNull();
  });

  test("announces the live session and counts from the moment it started", () => {
    render(<DictationStatus />);
    act(() => {
      useEventStore.setState({ dictating: true });
    });

    const strip = screen.getByTestId("dictation-status");
    expect(strip.getAttribute("role")).toBe("status");
    expect(strip.textContent).toContain("Listening");
    expect(strip.textContent).toContain("0:00");

    act(() => {
      vi.advanceTimersByTime(7_000);
    });
    expect(screen.getByTestId("dictation-status").textContent).toContain("0:07");
  });

  test("the clock restarts with the next dictation instead of carrying over", () => {
    render(<DictationStatus />);
    act(() => {
      useEventStore.setState({ dictating: true });
    });
    act(() => {
      vi.advanceTimersByTime(65_000);
    });
    expect(screen.getByTestId("dictation-status").textContent).toContain("1:05");

    act(() => {
      useEventStore.setState({ dictating: false });
    });
    act(() => {
      useEventStore.setState({ dictating: true });
    });
    expect(screen.getByTestId("dictation-status").textContent).toContain("0:00");
  });

  test("carries its own way out when the composer hands one over", () => {
    const onStop = vi.fn();
    render(<DictationStatus onStop={onStop} />);
    act(() => {
      useEventStore.setState({ dictating: true });
    });

    fireEvent.click(screen.getByText("Stop dictation"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
