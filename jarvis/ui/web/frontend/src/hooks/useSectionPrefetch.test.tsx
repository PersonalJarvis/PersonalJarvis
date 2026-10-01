import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { sectionPrefetch } from "@/lib/sectionPrefetch";
import { SECTION_PREFETCH_DELAY_MS, useSectionPrefetch } from "./useSectionPrefetch";
import type { SectionId } from "@/store/events";

function Link({ section = "agents" }: { section?: SectionId }) {
  return <button {...useSectionPrefetch(section)}>destination</button>;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(sectionPrefetch, "prefetch").mockResolvedValue();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test("mounting and a passing pointer do no work", () => {
  render(<Link />);
  act(() => { vi.advanceTimersByTime(60_000); });
  const button = screen.getByRole("button");
  fireEvent.mouseEnter(button);
  act(() => { vi.advanceTimersByTime(SECTION_PREFETCH_DELAY_MS - 1); });
  fireEvent.mouseLeave(button);
  act(() => { vi.advanceTimersByTime(60_000); });
  expect(sectionPrefetch.prefetch).not.toHaveBeenCalled();
});

test.each(["mouseEnter", "focus"] as const)("%s warms only the intended destination", (event) => {
  render(<Link />);
  fireEvent[event](screen.getByRole("button"));
  act(() => { vi.advanceTimersByTime(SECTION_PREFETCH_DELAY_MS); });
  expect(sectionPrefetch.prefetch).toHaveBeenCalledExactlyOnceWith("agents");
});

test("blur, a new destination and unmount cancel pending work", () => {
  const { rerender, unmount } = render(<Link />);
  fireEvent.focus(screen.getByRole("button"));
  fireEvent.blur(screen.getByRole("button"));
  act(() => { vi.advanceTimersByTime(SECTION_PREFETCH_DELAY_MS); });
  fireEvent.mouseEnter(screen.getByRole("button"));
  rerender(<Link section="docs" />);
  act(() => { vi.advanceTimersByTime(SECTION_PREFETCH_DELAY_MS); });
  fireEvent.focus(screen.getByRole("button"));
  unmount();
  act(() => { vi.advanceTimersByTime(SECTION_PREFETCH_DELAY_MS); });
  expect(sectionPrefetch.prefetch).not.toHaveBeenCalled();
});
