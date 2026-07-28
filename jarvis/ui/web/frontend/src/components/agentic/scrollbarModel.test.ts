import { describe, expect, it } from "vitest";
import {
  backAtThumbTop,
  countWheel,
  exactView,
  hasScroll,
  LINES_PER_NOTCH,
  MAX_NOTCHES_PER_STEP,
  MIN_THUMB_PX,
  notchesFor,
  thumbBox,
  travelView,
} from "./scrollbarModel";

describe("exactView", () => {
  it("reads a scrolled-back terminal off its own numbers", () => {
    // 1000 lines, 24 on screen, viewport 100 lines above the newest screen.
    const view = exactView(1000, 976, 876, 24);
    expect(view).toEqual({ kind: "exact", above: 976, back: 100, rows: 24 });
  });

  it("clamps a viewport reading that ran past either end", () => {
    expect(exactView(1000, 976, 1200, 24).back).toBe(0);
    expect(exactView(1000, 976, -50, 24).back).toBe(976);
  });

  it("treats a garbage field as the safe end, not a crash", () => {
    expect(exactView(1000, Number.NaN, 0, 24).back).toBe(0);
  });
});

describe("travelView", () => {
  it("claims the travelled distance plus one screen of headroom", () => {
    expect(travelView(60, 24)).toEqual({
      kind: "travel",
      above: 84,
      back: 60,
      rows: 24,
    });
  });

  it("never stands behind the live end", () => {
    expect(travelView(-5, 24).back).toBe(0);
  });
});

describe("hasScroll", () => {
  it("says no for an unscrollable or missing view", () => {
    expect(hasScroll(null)).toBe(false);
    expect(hasScroll(exactView(24, 0, 0, 24))).toBe(false);
  });

  it("says yes the moment there is history above the screen", () => {
    expect(hasScroll(exactView(25, 1, 1, 24))).toBe(true);
    expect(hasScroll(travelView(0, 24))).toBe(true);
  });
});

describe("thumbBox", () => {
  it("rests the thumb at the very bottom on the newest output", () => {
    const box = thumbBox(exactView(1000, 976, 976, 24), 300)!;
    expect(box.topPx + box.heightPx).toBe(300);
  });

  it("parks the thumb at the top of a fully scrolled-back pane", () => {
    expect(thumbBox(exactView(1000, 976, 0, 24), 300)!.topPx).toBe(0);
  });

  it("keeps the thumb graspable however long the history grows", () => {
    expect(thumbBox(exactView(100000, 99976, 99976, 24), 300)!.heightPx).toBe(
      MIN_THUMB_PX,
    );
  });

  it("draws nothing without a view or a measured track", () => {
    expect(thumbBox(null, 300)).toBeNull();
    expect(thumbBox(exactView(1000, 976, 0, 24), 0)).toBeNull();
  });
});

describe("backAtThumbTop", () => {
  it("round-trips the position the thumb is drawn at", () => {
    const view = exactView(1000, 976, 776, 24);
    const box = thumbBox(view, 300)!;
    expect(backAtThumbTop(box.topPx, 300, view)).toBeCloseTo(view.back, -1);
  });

  it("maps the track's ends to the history's ends", () => {
    const view = exactView(1000, 976, 776, 24);
    expect(backAtThumbTop(0, 300, view)).toBe(976);
    expect(backAtThumbTop(10000, 300, view)).toBe(0);
  });
});

describe("notchesFor", () => {
  it("converts lines to whole notches", () => {
    expect(notchesFor(LINES_PER_NOTCH * 4)).toBe(4);
    expect(notchesFor(-LINES_PER_NOTCH * 4)).toBe(-4);
    expect(notchesFor(LINES_PER_NOTCH - 1)).toBe(0);
  });

  it("caps a single step so a fast drag cannot flood the pty", () => {
    expect(notchesFor(1000000)).toBe(MAX_NOTCHES_PER_STEP);
    expect(notchesFor(-1000000)).toBe(-MAX_NOTCHES_PER_STEP);
  });
});

describe("countWheel", () => {
  it("counts a turn away from the hand as travelling back", () => {
    expect(countWheel(0, -1)).toBe(LINES_PER_NOTCH);
    expect(countWheel(LINES_PER_NOTCH, 1)).toBe(0);
  });

  it("re-anchors at zero however far past the live end the wheel turns", () => {
    let travelled = countWheel(0, -1);
    for (let i = 0; i < 10; i += 1) travelled = countWheel(travelled, 1);
    expect(travelled).toBe(0);
  });

  it("ignores a turn that moved nothing", () => {
    expect(countWheel(6, 0)).toBe(6);
    expect(countWheel(6, Number.NaN)).toBe(6);
  });
});
