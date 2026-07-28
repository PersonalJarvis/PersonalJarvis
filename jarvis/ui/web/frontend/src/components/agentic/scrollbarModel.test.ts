import { describe, expect, it } from "vitest";
import {
  backAtThumbTop,
  exactView,
  freshTravel,
  hasScroll,
  LINES_PER_NOTCH,
  MAX_NOTCHES_PER_STEP,
  MIN_THUMB_PX,
  notchesFor,
  screenTravel,
  SETTLE_MS,
  thumbBox,
  travelView,
  wheelTravel,
  type Travel,
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

  it("maps the real span once the top has been measured", () => {
    const view = travelView(90, 24, 90);
    // At the measured ceiling the thumb genuinely reaches the top.
    expect(view.above).toBe(90);
    expect(thumbBox(view, 300)!.topPx).toBe(0);
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

describe("wheelTravel", () => {
  it("counts a turn away from the hand as travelling back", () => {
    const t = wheelTravel(freshTravel(), -1, 1000);
    expect(t.travelled).toBe(LINES_PER_NOTCH);
    expect(t.pendingUp).toBe(LINES_PER_NOTCH);
    expect(wheelTravel(t, 1, 1001).travelled).toBe(0);
  });

  it("clamps at zero however far past the live end the wheel turns", () => {
    let t = wheelTravel(freshTravel(), -1, 0);
    for (let i = 0; i < 10; i += 1) t = wheelTravel(t, 1, i);
    expect(t.travelled).toBe(0);
  });

  it("stops counting ups while the top is saturated", () => {
    const saturated: Travel = { ...freshTravel(), travelled: 30, saturated: true };
    expect(wheelTravel(saturated, -1, 0).travelled).toBe(30);
  });

  it("a turn towards the live end releases the brake", () => {
    const saturated: Travel = { ...freshTravel(), travelled: 30, saturated: true };
    const t = wheelTravel(saturated, 1, 0);
    expect(t.saturated).toBe(false);
    expect(t.travelled).toBe(27);
  });

  it("ignores a turn that moved nothing", () => {
    const t: Travel = { ...freshTravel(), travelled: 6 };
    expect(wheelTravel(t, 0, 0)).toBe(t);
    expect(wheelTravel(t, Number.NaN, 0)).toBe(t);
  });
});

describe("screenTravel", () => {
  const seen = (travelled: number): Travel => ({
    ...freshTravel(),
    travelled,
    markerSeen: true,
    fingerprint: "same",
  });

  it("learns that this application paints a scrolled-back overlay", () => {
    const t = screenTravel(
      freshTravel(),
      { markerRow: 22, fingerprint: "a" },
      0,
    );
    expect(t.markerSeen).toBe(true);
  });

  it("snaps to the live end the moment the overlay disappears", () => {
    // The user's reported bug: at the newest output with the thumb mid-track.
    const t = screenTravel(seen(300), { markerRow: -1, fingerprint: "" }, 0);
    expect(t.travelled).toBe(0);
    expect(t.saturated).toBe(false);
  });

  it("does not treat a missing overlay as an anchor before one was ever seen", () => {
    const t: Travel = { ...freshTravel(), travelled: 60 };
    expect(
      screenTravel(t, { markerRow: -1, fingerprint: "" }, 0).travelled,
    ).toBe(60);
  });

  it("keeps the estimate when the screen cannot be read", () => {
    const t = seen(60);
    expect(screenTravel(t, null, 0)).toBe(t);
  });

  it("confirms pending ups when the transcript moved", () => {
    const t: Travel = { ...seen(60), pendingUp: 30, lastUpAt: 0 };
    const next = screenTravel(t, { markerRow: 22, fingerprint: "moved" }, 1);
    expect(next.travelled).toBe(60);
    expect(next.pendingUp).toBe(0);
    expect(next.saturated).toBe(false);
  });

  it("un-counts ups the application ignored at the top, and measures it", () => {
    // The user's other reported bug: scrolling past a top nobody can see.
    const t: Travel = { ...seen(90), pendingUp: 30, lastUpAt: 0 };
    const next = screenTravel(
      t,
      { markerRow: 22, fingerprint: "same" },
      SETTLE_MS,
    );
    expect(next.travelled).toBe(60);
    expect(next.saturated).toBe(true);
    expect(next.ceiling).toBe(60);
  });

  it("waits for the pty before judging an unmoved screen", () => {
    const t: Travel = { ...seen(90), pendingUp: 30, lastUpAt: 0 };
    const next = screenTravel(
      t,
      { markerRow: 22, fingerprint: "same" },
      SETTLE_MS - 1,
    );
    expect(next.travelled).toBe(90);
    expect(next.saturated).toBe(false);
  });

  it("floors the count while the overlay says the view is away", () => {
    const t = screenTravel(
      { ...seen(0), fingerprint: "a" },
      { markerRow: 22, fingerprint: "a" },
      0,
    );
    expect(t.travelled).toBe(LINES_PER_NOTCH);
  });
});
