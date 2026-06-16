import { describe, it, expect, vi, afterEach } from "vitest";
import { applyMissionDragImage } from "./missionDragImage";

afterEach(() => {
  // Remove any chips left in the DOM between cases.
  document
    .querySelectorAll("[data-mission-drag-chip]")
    .forEach((n) => n.remove());
});

describe("applyMissionDragImage", () => {
  it("sets a compact custom drag image carrying the mission title", () => {
    const setDragImage = vi.fn();
    const dt = { setDragImage } as unknown as DataTransfer;

    applyMissionDragImage(dt, "Build the landing page");

    expect(setDragImage).toHaveBeenCalledTimes(1);
    const node = setDragImage.mock.calls[0][0] as HTMLElement;
    expect(node).toBeInstanceOf(HTMLElement);
    expect(node.textContent).toContain("Build the landing page");
    // Must be in the document at snapshot time or the browser draws nothing.
    expect(document.body.contains(node)).toBe(true);
  });

  it("truncates an overly long title so the chip stays compact", () => {
    const setDragImage = vi.fn();
    const dt = { setDragImage } as unknown as DataTransfer;

    applyMissionDragImage(dt, "x".repeat(300));

    const node = setDragImage.mock.calls[0][0] as HTMLElement;
    expect((node.textContent ?? "").length).toBeLessThan(120);
  });

  it("falls back to a generic label for an empty title", () => {
    const setDragImage = vi.fn();
    const dt = { setDragImage } as unknown as DataTransfer;

    applyMissionDragImage(dt, "   ");

    const node = setDragImage.mock.calls[0][0] as HTMLElement;
    expect((node.textContent ?? "").trim().length).toBeGreaterThan(0);
  });

  it("never throws when setDragImage is unavailable", () => {
    expect(() =>
      applyMissionDragImage({} as unknown as DataTransfer, "t"),
    ).not.toThrow();
  });
});
