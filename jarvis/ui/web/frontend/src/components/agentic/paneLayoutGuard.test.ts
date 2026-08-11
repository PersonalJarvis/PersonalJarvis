import { describe, expect, it } from "vitest";
import {
  describeLayoutViolations,
  findLayoutViolations,
  hasLayoutViolations,
  type MeasuredCanvas,
  type MeasuredPane,
} from "./paneLayoutGuard";

const canvas: MeasuredCanvas = { left: 0, top: 0, width: 1000, height: 600 };

const at = (
  name: string,
  left: number,
  top: number,
  width: number,
  height: number,
  content?: MeasuredPane["content"],
): MeasuredPane => ({ name, left, top, width, height, content });

describe("findLayoutViolations", () => {
  it("calls a healthy gapped workspace healthy", () => {
    // Two columns 4 px apart, two panes stacked 4 px apart — the shape
    // `paneLayout` + `paneBoxStyle` actually produce.
    const report = findLayoutViolations(
      [
        at("T1", 0, 0, 498, 600),
        at("T2", 502, 0, 498, 298),
        at("T3", 502, 302, 498, 298),
      ],
      canvas,
    );
    expect(hasLayoutViolations(report)).toBe(false);
  });

  it("flags ANY overlap, however small past the tolerance", () => {
    // The report this guard exists for said it explicitly: it does not matter
    // by how much — overlapping at all is the bug.
    const report = findLayoutViolations(
      [at("T1", 0, 0, 500, 600), at("T2", 497, 0, 503, 600)],
      canvas,
    );
    expect(report.overlaps).toEqual([["T1", "T2"]]);
  });

  it("flags a pane standing on half of its neighbour", () => {
    const report = findLayoutViolations(
      [at("T1", 0, 0, 750, 600), at("T2", 500, 0, 500, 600)],
      canvas,
    );
    expect(report.overlaps).toEqual([["T1", "T2"]]);
    expect(hasLayoutViolations(report)).toBe(true);
  });

  it("lets sub-pixel rounding jitter pass", () => {
    // Browsers round fractional percentages per element; edges 0.4 px into
    // each other are noise, not a fault to repair every three seconds.
    const report = findLayoutViolations(
      [at("T1", 0, 0, 500.4, 600), at("T2", 500, 0, 500, 600)],
      canvas,
    );
    expect(report.overlaps).toEqual([]);
  });

  it("ignores hidden panes, which measure as sizeless", () => {
    // Under a maximize every other pane is display:none and reports 0x0 at
    // 0,0 — stacked on each other, and none of it on screen.
    const report = findLayoutViolations(
      [at("T1", 0, 0, 1000, 600), at("T2", 0, 0, 0, 0), at("T3", 0, 0, 0, 0)],
      canvas,
    );
    expect(hasLayoutViolations(report)).toBe(false);
  });

  it("flags a pane reaching past the canvas edge", () => {
    // The canvas clips silently (`overflow-hidden`), so a pane past its edge
    // is text cut off mid-word with nothing saying why.
    const report = findLayoutViolations([at("T1", 600, 0, 500, 600)], canvas);
    expect(report.escaped).toEqual(["T1"]);
  });

  it("flags terminal content bigger than its pane", () => {
    // A missed refit: the PTY still thinks the pane is wide, xterm renders
    // that width, and the pane clips the right half of every line.
    const report = findLayoutViolations(
      [at("T1", 0, 0, 500, 600, { left: 8, top: 40, width: 620, height: 500 })],
      canvas,
    );
    expect(report.clipped).toEqual(["T1"]);
  });

  it("accepts content a little smaller than its pane", () => {
    // The fit leaves the remainder under one character cell unused — that is
    // letterboxing, not a fault.
    const report = findLayoutViolations(
      [at("T1", 0, 0, 500, 600, { left: 8, top: 40, width: 484, height: 540 })],
      canvas,
    );
    expect(report.clipped).toEqual([]);
    expect(report.underfit).toEqual([]);
  });

  it("flags a terminal running as a thin strip down a wide pane", () => {
    // The other direction of the same missed refit: the terminal was once
    // fitted to a narrow moment, the tile got its room back, and no
    // ResizeObserver will ever fire again because the tile is not changing.
    // The agent keeps wrapping at a handful of columns forever (maintainer
    // report 2026-08-11).
    const report = findLayoutViolations(
      [at("T6", 0, 0, 500, 600, { left: 8, top: 40, width: 180, height: 540 })],
      canvas,
    );
    expect(report.underfit).toEqual(["T6"]);
    expect(hasLayoutViolations(report)).toBe(true);
    expect(describeLayoutViolations(report)).toContain("far narrower");
  });

  it("does not call a short pane underfit for its header and notice rows", () => {
    // Height deficits are legitimate — the header, notice rows and delivery
    // receipts all sit between the content and the pane's bottom edge.
    const report = findLayoutViolations(
      [at("T1", 0, 0, 500, 600, { left: 8, top: 40, width: 484, height: 420 })],
      canvas,
    );
    expect(hasLayoutViolations(report)).toBe(false);
  });

  it("names what it found, for the console", () => {
    const report = findLayoutViolations(
      [at("T1", 0, 0, 750, 600), at("T2", 500, 0, 600, 600)],
      canvas,
    );
    const line = describeLayoutViolations(report);
    expect(line).toContain("T1+T2");
    expect(line).toContain("outside the canvas");
  });
});
