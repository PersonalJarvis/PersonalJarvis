import { describe, expect, it } from "vitest";
import {
  appScroll,
  backForThumbTop,
  bufferScroll,
  LINES_PER_NOTCH,
  MIN_THUMB_PX,
  notchesForLines,
  scrollable,
  thumbGeometry,
} from "./paneScrollModel";

describe("bufferScroll", () => {
  it("reads a terminal's own scrollback exactly", () => {
    expect(bufferScroll(1000, 976, 976, 24)).toEqual({
      span: 976,
      back: 0,
      rows: 24,
    });
  });

  it("counts how far back the viewport stands", () => {
    expect(bufferScroll(1000, 976, 900, 24).back).toBe(76);
  });

  it("has nothing to scroll while the buffer fits on screen", () => {
    expect(scrollable(bufferScroll(24, 0, 0, 24))).toBe(false);
  });
});

describe("appScroll", () => {
  /*
   * The claim an application-held pane makes, and the one it must not make. It
   * says "there is at least one more screen above you", which keeps the thumb
   * short enough to travel; it never says "you have reached the top", because
   * nothing outside the application can know that.
   */
  it("always leaves one screen of history above wherever you are", () => {
    expect(appScroll(0, 30)).toEqual({ span: 30, back: 0, rows: 30 });
    expect(appScroll(90, 30)).toEqual({ span: 120, back: 90, rows: 30 });
  });

  it("cannot stand further forward than the newest output", () => {
    expect(appScroll(-40, 30).back).toBe(0);
  });

  it("always has somewhere to scroll, so the bar is always reachable", () => {
    expect(scrollable(appScroll(0, 30))).toBe(true);
  });
});

describe("thumbGeometry", () => {
  it("sizes the thumb by how much of the content is on screen", () => {
    expect(thumbGeometry(bufferScroll(400, 300, 300, 100), 400)).toEqual({
      topPx: 300,
      heightPx: 100,
    });
  });

  /*
   * The reported bug this pins down: a pane showing the newest output must draw
   * its thumb AT THE BOTTOM. A short shape halfway up a track says "you are in
   * the middle" in the only grammar scrollbars have.
   */
  it("puts an untouched pane's thumb at the bottom of the track", () => {
    for (const scroll of [appScroll(0, 30), bufferScroll(1000, 976, 976, 24)]) {
      const geometry = thumbGeometry(scroll, 400)!;
      expect(geometry.topPx + geometry.heightPx).toBe(400);
    }
  });

  it("puts a pane scrolled to the top of its history at the top", () => {
    const geometry = thumbGeometry(bufferScroll(1000, 976, 0, 24), 400)!;
    expect(geometry.topPx).toBe(0);
  });

  it("keeps a deep history's thumb grabbable", () => {
    expect(
      thumbGeometry(bufferScroll(10000, 9976, 9976, 24), 300)?.heightPx,
    ).toBe(MIN_THUMB_PX);
  });

  it("draws no thumb where there is nothing to scroll", () => {
    expect(thumbGeometry(null, 300)).toBeNull();
    expect(thumbGeometry(bufferScroll(24, 0, 0, 24), 300)).toBeNull();
    expect(thumbGeometry(appScroll(0, 30), 0)).toBeNull();
  });

  it("never lets the thumb overflow a very short track", () => {
    const geometry = thumbGeometry(bufferScroll(10000, 9976, 9976, 24), 12)!;
    expect(geometry.heightPx).toBeLessThanOrEqual(12);
    expect(geometry.topPx + geometry.heightPx).toBeLessThanOrEqual(12);
  });
});

describe("backForThumbTop", () => {
  it("reads a dragged thumb as a distance back through the history", () => {
    const scroll = bufferScroll(1000, 976, 976, 24);
    const geometry = thumbGeometry(scroll, 400)!;
    const travel = 400 - geometry.heightPx;

    expect(backForThumbTop(travel, 400, scroll)).toBe(0);
    expect(backForThumbTop(0, 400, scroll)).toBe(976);
    expect(backForThumbTop(travel / 2, 400, scroll)).toBe(488);
  });

  it("is the exact inverse of where the thumb was drawn", () => {
    const scroll = bufferScroll(1000, 976, 500, 24);
    const geometry = thumbGeometry(scroll, 400)!;

    // Within the rounding the thumb's own whole-pixel top costs.
    expect(
      Math.abs(backForThumbTop(geometry.topPx, 400, scroll) - scroll.back),
    ).toBeLessThanOrEqual(3);
  });

  it("stays put when there is nothing to drag", () => {
    expect(backForThumbTop(10, 400, null)).toBe(0);
  });
});

describe("notchesForLines", () => {
  it("converts a distance into whole wheel notches", () => {
    expect(notchesForLines(LINES_PER_NOTCH * 4)).toBe(4);
    expect(notchesForLines(-LINES_PER_NOTCH * 2)).toBe(-2);
    // Less than a notch is no notch — never a rounded-up jump the user did not
    // ask for.
    expect(notchesForLines(LINES_PER_NOTCH - 1)).toBe(0);
  });
});
