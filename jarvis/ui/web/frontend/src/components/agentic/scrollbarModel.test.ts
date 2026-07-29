import { describe, expect, it } from "vitest";
import {
  ASSUMED_SCREENS,
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
  it("claims the travelled distance plus the assumed headroom", () => {
    expect(travelView(60, 24)).toEqual({
      kind: "travel",
      above: 60 + 24 * ASSUMED_SCREENS,
      back: 60,
      rows: 24,
    });
  });

  it("draws a graspable thumb, not half the track, at the live end", () => {
    // The maintainer's report: on a pane at its newest output the thumb filled
    // half the bar, which claims a two-screen history no agent ever has.
    const box = thumbBox(travelView(0, 24), 300)!;
    expect(box.heightPx).toBeLessThan(100);
    expect(box.heightPx).toBeGreaterThanOrEqual(MIN_THUMB_PX);
    expect(box.topPx + box.heightPx).toBe(300);
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

  it("counts every report of one turn, not the turn", () => {
    // A physical wheel covers several rows, and xterm sends one report per
    // row — taking the whole turn for a single notch under-counted every
    // real scroll five-fold and left the thumb near a bottom it had left.
    const t = wheelTravel(freshTravel(), -6, 1000);
    expect(t.travelled).toBe(6 * LINES_PER_NOTCH);
    expect(wheelTravel(t, 6, 1001).travelled).toBe(0);
  });

  it("holds a down pending, so an unanswered one can prove the live end", () => {
    const t = wheelTravel({ ...freshTravel(), travelled: 30 }, 2, 1000);
    expect(t.pendingDown).toBe(2 * LINES_PER_NOTCH);
    expect(t.lastDownAt).toBe(1000);
    // An up in the other direction retracts that claim.
    expect(wheelTravel(t, -1, 1001).pendingDown).toBe(0);
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
  const standing = (travelled: number): Travel => ({
    ...freshTravel(),
    travelled,
    fingerprint: "same",
  });

  it("keeps the estimate when the screen cannot be read", () => {
    const t = standing(60);
    expect(screenTravel(t, null, 0)).toBe(t);
  });

  it("confirms pending notches when the transcript moved", () => {
    const t: Travel = {
      ...standing(60),
      pendingUp: 30,
      lastUpAt: 0,
      pendingDown: 9,
      lastDownAt: 0,
    };
    const next = screenTravel(t, { fingerprint: "moved" }, 1);
    expect(next.travelled).toBe(60);
    expect(next.pendingUp).toBe(0);
    expect(next.pendingDown).toBe(0);
    expect(next.saturated).toBe(false);
  });

  it("un-counts ups the application ignored at the top, and measures it", () => {
    // Scrolling past a top nobody can see: the notches emit no bytes at all,
    // so only the unchanged screen can tell.
    const t: Travel = {
      ...standing(90),
      pendingUp: 30,
      lastUpAt: 0,
      moved: true,
    };
    const next = screenTravel(t, { fingerprint: "same" }, SETTLE_MS);
    expect(next.travelled).toBe(60);
    expect(next.saturated).toBe(true);
    expect(next.ceiling).toBe(60);
  });

  it("re-anchors at the live end when downs stop changing the screen", () => {
    // The word-agnostic replacement for one CLI's English overlay: a down the
    // application had nothing left to answer with means the newest output is
    // already on screen, in whatever language it happens to be printed.
    const t: Travel = {
      ...standing(42),
      pendingDown: 9,
      lastDownAt: 0,
      moved: true,
    };
    const next = screenTravel(t, { fingerprint: "same" }, SETTLE_MS);
    expect(next.travelled).toBe(0);
    expect(next.pendingDown).toBe(0);
    // The episode closes: the next one has to prove movement afresh.
    expect(next.moved).toBe(false);
  });

  it("waits for the pty before judging an unmoved screen", () => {
    const up: Travel = {
      ...standing(90),
      pendingUp: 30,
      lastUpAt: 0,
      moved: true,
    };
    expect(screenTravel(up, { fingerprint: "same" }, SETTLE_MS - 1)).toBe(up);
    const down: Travel = {
      ...standing(90),
      pendingDown: 9,
      lastDownAt: 0,
      moved: true,
    };
    expect(screenTravel(down, { fingerprint: "same" }, SETTLE_MS - 1)).toBe(
      down,
    );
  });

  it("never calls an episode that has not moved a top", () => {
    // The pinned-thumb deadlock: a busy CLI leaves the screen unchanged far
    // past the settle window without a single notch having reached the top.
    // The ups still leave the count, but no brake and no false ceiling.
    const t: Travel = { ...standing(30), pendingUp: 30, lastUpAt: 0 };
    const next = screenTravel(t, { fingerprint: "same" }, SETTLE_MS);
    expect(next.travelled).toBe(0);
    expect(next.saturated).toBe(false);
    expect(next.ceiling).toBeNull();
  });

  it("never calls an episode that has not moved the live end either", () => {
    // Same deadlock, mirrored: a busy CLI must not be able to claim the pane
    // is at its newest output while the user is reading its history.
    const t: Travel = { ...standing(60), pendingDown: 9, lastDownAt: 0 };
    const next = screenTravel(t, { fingerprint: "same" }, SETTLE_MS);
    expect(next.travelled).toBe(60);
    expect(next.pendingDown).toBe(0);
  });

  it("a fingerprint CHANGE proves movement; the first look only records", () => {
    const first = screenTravel(freshTravel(), { fingerprint: "a" }, 0);
    expect(first.moved).toBe(false);
    const second = screenTravel(first, { fingerprint: "b" }, 1);
    expect(second.moved).toBe(true);
  });

  it("holds its ground while a scrolled-back pane keeps repainting", () => {
    // The reported bug, at model level: the count used to snap to zero on
    // every repaint that carried no known overlay string, so a pane parked at
    // the TOP of its transcript drew its thumb at the very bottom.
    let t: Travel = { ...standing(300), moved: true };
    for (let i = 0; i < 20; i += 1) {
      t = screenTravel(t, { fingerprint: `agent still talking ${i}` }, i);
    }
    expect(t.travelled).toBe(300);
  });

  it("keeps the ceiling graspable however small the measured top", () => {
    const t: Travel = {
      ...standing(3),
      pendingUp: 3,
      lastUpAt: 0,
      moved: true,
    };
    const next = screenTravel(t, { fingerprint: "same" }, SETTLE_MS);
    expect(next.ceiling).toBeGreaterThanOrEqual(LINES_PER_NOTCH);
  });
});
