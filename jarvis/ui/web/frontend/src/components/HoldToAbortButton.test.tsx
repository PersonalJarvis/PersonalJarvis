/**
 * Hold-to-abort: the stop control for RUNNING mission cards.
 *
 * Design contract: no confirm dialog (anti-confirmation-fatigue), but no
 * accidental one-click kill either — the button must be HELD for the full
 * `holdMs` before `onConfirm` fires. Releasing or leaving early resets.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { HoldToAbortButton } from "@/components/HoldToAbortButton";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderButton(
  overrides: Partial<React.ComponentProps<typeof HoldToAbortButton>> = {},
) {
  const onConfirm = vi.fn();
  render(
    <HoldToAbortButton
      onConfirm={onConfirm}
      holdMs={1200}
      label="Abort mission"
      {...overrides}
    />,
  );
  const btn = screen.getByRole("button", { name: "Abort mission" });
  return { onConfirm, btn };
}

describe("HoldToAbortButton", () => {
  it("fires onConfirm exactly once after holding for the full duration", () => {
    const { onConfirm, btn } = renderButton();

    fireEvent.pointerDown(btn);
    act(() => {
      vi.advanceTimersByTime(1199);
    });
    expect(onConfirm).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("does not fire when released before the hold completes", () => {
    const { onConfirm, btn } = renderButton();

    fireEvent.pointerDown(btn);
    act(() => {
      vi.advanceTimersByTime(600);
    });
    fireEvent.pointerUp(btn);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("aborts the hold when the pointer leaves the button", () => {
    const { onConfirm, btn } = renderButton();

    fireEvent.pointerDown(btn);
    act(() => {
      vi.advanceTimersByTime(600);
    });
    fireEvent.pointerLeave(btn);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("ignores presses while a cancel request is pending", () => {
    const { onConfirm, btn } = renderButton({ pending: true });

    fireEvent.pointerDown(btn);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("exposes the holding state for the ring animation", () => {
    const { btn } = renderButton();

    expect(btn.dataset.holding).toBe("false");
    fireEvent.pointerDown(btn);
    expect(btn.dataset.holding).toBe("true");
    fireEvent.pointerUp(btn);
    expect(btn.dataset.holding).toBe("false");
  });

  it("supports keyboard hold via Space", () => {
    const { onConfirm, btn } = renderButton();

    fireEvent.keyDown(btn, { key: " " });
    act(() => {
      vi.advanceTimersByTime(1200);
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("a repeated keydown (OS auto-repeat) does not restart the hold", () => {
    const { onConfirm, btn } = renderButton();

    fireEvent.keyDown(btn, { key: " " });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    fireEvent.keyDown(btn, { key: " ", repeat: true });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
