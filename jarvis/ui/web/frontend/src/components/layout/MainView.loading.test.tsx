/**
 * The loading state a section switch shows before its chunk arrives.
 *
 * Why this test exists (2026-08-22): the placeholder rendered nothing at all —
 * `<div className="h-full w-full" aria-busy="true" />` — on the reasoning that
 * the idle prefetch makes the wait invisible. When the backend's single asyncio
 * loop blocks (measured that week at 15.0 s to 17.9 s), the chunk stops
 * mid-flight and that empty div is all there is: the section stage's own
 * `rgb(10, 10, 10)` ground and no other mark on the screen. Users reported it
 * as the app going black when they switch sections.
 *
 * So the contract is a staircase, and each step is pinned here: silent while
 * the wait is too short to notice, visibly loading once it is not, and honest
 * about it once the wait stops being normal.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  LOADING_HINT_MS,
  LOADING_SLOW_MS,
  ViewLoadingFallback,
} from "@/components/layout/MainView";

describe("the section loading placeholder", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  test("stays silent for a wait too short to notice", () => {
    render(<ViewLoadingFallback />);
    act(() => {
      vi.advanceTimersByTime(LOADING_HINT_MS - 50);
    });
    expect(
      screen.getByTestId("view-loading-fallback").getAttribute("data-phase"),
    ).toBe("quiet");
  });

  test("never leaves the stage unpainted, not even while silent", () => {
    // The whole defect was a transparent placeholder over a near-black stage.
    // Whatever else changes, this element paints the theme's own ground.
    render(<ViewLoadingFallback />);
    expect(screen.getByTestId("view-loading-fallback").className).toContain(
      "bg-background",
    );
  });

  test("says it is loading once the wait is long enough to see", () => {
    render(<ViewLoadingFallback />);
    act(() => {
      vi.advanceTimersByTime(LOADING_HINT_MS + 50);
    });
    const el = screen.getByTestId("view-loading-fallback");
    expect(el.getAttribute("data-phase")).toBe("loading");
    expect(el.textContent?.trim()).not.toBe("");
  });

  test("admits it is taking longer than usual instead of just sitting there", () => {
    render(<ViewLoadingFallback />);
    act(() => {
      vi.advanceTimersByTime(LOADING_SLOW_MS + 50);
    });
    const el = screen.getByTestId("view-loading-fallback");
    expect(el.getAttribute("data-phase")).toBe("slow");
    // Two lines by then: what is happening, and that it is running late.
    expect(el.querySelectorAll("p")).toHaveLength(2);
  });

  test("is announced to assistive technology as a busy region", () => {
    render(<ViewLoadingFallback />);
    const el = screen.getByTestId("view-loading-fallback");
    expect(el.getAttribute("aria-busy")).toBe("true");
    expect(el.getAttribute("role")).toBe("status");
  });
});
