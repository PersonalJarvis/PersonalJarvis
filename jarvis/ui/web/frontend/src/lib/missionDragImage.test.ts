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

  it("keeps the chip's layout box at the origin (dpr-safe, not -9999px)", () => {
    // Regression for the 150%-scaled-display bug: a large negative left/top is
    // multiplied by devicePixelRatio by Chromium/WebView2, detaching the drag
    // ghost from the cursor by ~5000px. The chip must hide via `transform`
    // while its layout offset stays at 0, so the error is zero on every dpr.
    const setDragImage = vi.fn();
    const dt = { setDragImage } as unknown as DataTransfer;

    applyMissionDragImage(dt, "Build the landing page");

    const node = setDragImage.mock.calls[0][0] as HTMLElement;
    expect(node.style.left).toBe("0px");
    expect(node.style.top).toBe("0px");
    expect(node.style.transform).not.toBe("");
    // Guard the exact failure mode: no huge negative offset anywhere.
    expect(node.style.left).not.toContain("-9999");
    expect(node.style.top).not.toContain("-9999");
  });
});
