import { describe, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import {
  AT_LIVE_END,
  BURST_MS,
  MAX_LINES_PER_NOTCH,
  PROBE_RETURN_MS,
  SETTLE_MS,
  appScrollExtent,
  appTakesWheel,
  applyShift,
  detectShift,
  hasMeasuredHistory,
  notchesForLines,
  probeAppHistory,
  trackAppScroll,
  visibleRows,
  type AppScrollPosition,
} from "./paneAppScroll";

/** A screen of an agent transcript — long enough to identify itself again. */
function transcript(from: number, count: number): string[] {
  return Array.from({ length: count }, (_, i) => `line ${from + i} of output`);
}

function fakeTerminal(rows: string[], mouseTrackingMode = "any"): Terminal {
  return {
    rows: rows.length,
    modes: { mouseTrackingMode },
    buffer: {
      active: {
        type: "alternate",
        length: rows.length,
        viewportY: 0,
        baseY: 0,
        getLine: (index: number) =>
          index >= 0 && index < rows.length
            ? { translateToString: () => rows[index] }
            : undefined,
      },
    },
  } as unknown as Terminal;
}

describe("visibleRows", () => {
  it("reads the screen the user is looking at", () => {
    expect(visibleRows(fakeTerminal(transcript(1, 3)))).toEqual([
      "line 1 of output",
      "line 2 of output",
      "line 3 of output",
    ]);
  });

  it("degrades to an empty screen rather than throwing", () => {
    expect(visibleRows(null)).toEqual([]);
    expect(visibleRows({} as Terminal)).toEqual([]);
    // An xterm build without `getLine` must not take a wheel handler down.
    expect(
      visibleRows({
        rows: 4,
        buffer: { active: { viewportY: 0 } },
      } as unknown as Terminal),
    ).toEqual([]);
  });
});

describe("detectShift", () => {
  it("sees a screen that did not move, spinner and all", () => {
    const before = [...transcript(1, 8), "Fermenting… 3m 23s"];
    const after = [...transcript(1, 8), "Fermenting… 3m 41s"];

    expect(detectShift(before, after)).toBe(0);
  });

  /*
   * The measurement the whole feature rests on: scrolling back two lines pushes
   * the content DOWN the screen, and that distance IS how much further from the
   * live end the user now is.
   */
  it("measures how far the content travelled when scrolled back", () => {
    const before = transcript(10, 10);
    const after = [...transcript(8, 2), ...transcript(10, 8)];

    expect(detectShift(before, after)).toBe(2);
  });

  it("measures a move back towards newer output as a negative shift", () => {
    const before = transcript(10, 10);
    const after = [...transcript(13, 7), ...transcript(20, 3)];

    expect(detectShift(before, after)).toBe(-3);
  });

  /*
   * A coding agent pins its prompt box and status line to the bottom of the
   * screen, so those rows match at a shift of zero no matter what the transcript
   * above them did. They must not be able to outvote the part that moved.
   */
  it("is not fooled by rows the application pins to the bottom", () => {
    const box = ["> type here", "bypass permissions on"];
    const before = [...transcript(10, 10), ...box];
    const after = [...transcript(9, 1), ...transcript(10, 9), ...box];

    expect(detectShift(before, after)).toBe(1);
  });

  it("refuses to guess from a screen with nothing on it", () => {
    expect(detectShift(["", "", "a", ""], ["", "", "", "a"])).toBeNull();
    expect(detectShift(transcript(1, 2), transcript(1, 2))).toBeNull();
  });

  it("refuses to guess when the screen was replaced outright", () => {
    expect(detectShift(transcript(1, 10), transcript(500, 10))).toBeNull();
  });
});

describe("applyShift", () => {
  it("moves away from the live end by exactly what was measured", () => {
    const next = applyShift(AT_LIVE_END, 3, -1);

    expect(next.offset).toBe(3);
    // One notch moved three lines, so that is what a notch is worth here.
    expect(next.linesPerNotch).toBe(3);
    // Three lines of history are now proven to exist — and possibly more.
    expect(next).toMatchObject({ span: 3, spanKnown: false });
  });

  it("pins the top down when a scroll back moves nothing", () => {
    const scrolled: AppScrollPosition = {
      offset: 42,
      span: 42,
      spanKnown: false,
      linesPerNotch: 3,
    };

    expect(applyShift(scrolled, 0, -1)).toMatchObject({
      offset: 42,
      span: 42,
      spanKnown: true,
    });
  });

  /*
   * The reading the reported bug turned on: a pane at the newest output must be
   * drawn at the newest output. A scroll down that moves nothing says so exactly,
   * whatever the accumulated arithmetic had drifted to.
   */
  it("snaps back to zero when a scroll down moves nothing", () => {
    const drifted: AppScrollPosition = {
      offset: 7,
      span: 200,
      spanKnown: true,
      linesPerNotch: 3,
    };

    expect(applyShift(drifted, 0, 1)).toMatchObject({
      offset: 0,
      span: 200,
      spanKnown: true,
    });
  });

  it("learns nothing about a notch from a move that hit the end", () => {
    const near: AppScrollPosition = {
      offset: 0,
      span: 0,
      spanKnown: false,
      linesPerNotch: 3,
    };

    // Four notches asked for, one line delivered: the history ran out mid-burst,
    // so this says nothing about how far one notch goes.
    expect(applyShift(near, 1, -4).linesPerNotch).toBe(3);
  });

  it("keeps a notch's measured size within reason", () => {
    expect(applyShift(AT_LIVE_END, 900, -1).linesPerNotch).toBe(
      MAX_LINES_PER_NOTCH,
    );
  });

  /*
   * "Nothing moved" and "we could not tell" look the same in a sum and mean
   * opposite things: one is the end of the history, the other is a measurement
   * that failed. Only the first may ever pin an end down.
   */
  it("falls back to what was sent without claiming an end", () => {
    const known: AppScrollPosition = {
      offset: 0,
      span: 90,
      spanKnown: true,
      linesPerNotch: 3,
    };

    expect(applyShift(known, null, -4)).toMatchObject({
      offset: 12,
      spanKnown: true,
      linesPerNotch: 3,
    });
    expect(applyShift(known, null, 4)).toMatchObject({ offset: 0 });
  });

  it("gives up a top it turns out to be able to scroll past", () => {
    const stale: AppScrollPosition = {
      offset: 90,
      span: 90,
      spanKnown: true,
      linesPerNotch: 3,
    };

    // The transcript grew while the user sat in it, so the old top was not one.
    expect(applyShift(stale, 6, -2)).toMatchObject({
      offset: 96,
      span: 96,
      spanKnown: false,
    });
  });

  it("never lets a position run below the live end", () => {
    expect(applyShift(AT_LIVE_END, -40, 4).offset).toBe(0);
  });
});

describe("hasMeasuredHistory", () => {
  it("knows nothing about a pane nobody has scrolled", () => {
    expect(hasMeasuredHistory(AT_LIVE_END)).toBe(false);
  });

  /*
   * The three ways something can be known, each on its own — because each is a
   * pane the user has already scrolled, and one that must keep the bar it was
   * scrolled with even after coming home to the newest output.
   */
  it("knows something once the pane has been away from the live end", () => {
    expect(hasMeasuredHistory({ ...AT_LIVE_END, offset: 4 })).toBe(true);
  });

  it("knows something once a depth has been reached", () => {
    expect(hasMeasuredHistory({ ...AT_LIVE_END, span: 40 })).toBe(true);
  });

  /*
   * Including the pane measured as having NO history at all: something IS known
   * about it, and what `readScrollState` does with that is take the bar away —
   * for a reason of its own, not for want of a measurement.
   */
  it("knows something once a scroll has hit the top", () => {
    expect(hasMeasuredHistory({ ...AT_LIVE_END, spanKnown: true })).toBe(true);
  });
});

describe("appScrollExtent", () => {
  /*
   * The arithmetic for a position whose top is still unfound: one screenful is
   * assumed above the deepest point reached, and the viewport sits at the bottom
   * of it. Note this is the arithmetic ONLY — whether a bar is drawn at all is
   * `hasMeasuredHistory`'s call, and for an untouched pane the answer is no (see
   * `readScrollState` in ./paneScroll). Kept as a unit so the "top unknown" case
   * that DOES reach the screen — scrolled back, top not yet hit — stays pinned.
   */
  it("assumes one screen above a position whose top is unfound", () => {
    expect(appScrollExtent(AT_LIVE_END, 30)).toEqual({ total: 60, top: 30 });
  });

  it("draws the real proportions once the top has been found", () => {
    const known: AppScrollPosition = {
      offset: 0,
      span: 400,
      spanKnown: true,
      linesPerNotch: 3,
    };

    expect(appScrollExtent(known, 24)).toEqual({ total: 424, top: 400 });
    expect(appScrollExtent({ ...known, offset: 400 }, 24)).toEqual({
      total: 424,
      top: 0,
    });
    expect(appScrollExtent({ ...known, offset: 200 }, 24)).toEqual({
      total: 424,
      top: 200,
    });
  });

  it("reports no history at all for an app measured as having none", () => {
    const flat: AppScrollPosition = {
      offset: 0,
      span: 0,
      spanKnown: true,
      linesPerNotch: 1,
    };

    // total === rows, which is how ./paneScroll knows to take the bar away.
    expect(appScrollExtent(flat, 24)).toEqual({ total: 24, top: 0 });
  });
});

describe("notchesForLines", () => {
  it("converts a distance in lines into wheel notches", () => {
    expect(notchesForLines(9, 3)).toBe(3);
    expect(notchesForLines(-9, 3)).toBe(-3);
    expect(notchesForLines(4, 3)).toBe(1);
    // A nonsense step must not produce an infinite relay.
    expect(notchesForLines(6, 0)).toBe(6);
  });
});

describe("appTakesWheel", () => {
  it("is the one test that decides which world a pane is in", () => {
    expect(appTakesWheel(fakeTerminal([], "any"))).toBe(true);
    expect(appTakesWheel(fakeTerminal([], "none"))).toBe(false);
    expect(appTakesWheel(null)).toBe(false);
  });
});

describe("trackAppScroll", () => {
  function wheel(host: HTMLElement, deltaY: number, deltaMode = 1) {
    host
      .querySelector(".xterm-screen")!
      .dispatchEvent(
        new WheelEvent("wheel", { bubbles: true, deltaY, deltaMode }),
      );
  }

  function stage(rows: string[], mouseTrackingMode = "any") {
    const host = document.createElement("div");
    const screen = document.createElement("div");
    screen.className = "xterm-screen";
    host.appendChild(screen);
    document.body.appendChild(host);

    let term = fakeTerminal(rows, mouseTrackingMode);
    let position = AT_LIVE_END;
    const stop = trackAppScroll({
      host,
      getTerminal: () => term,
      onChange: (update) => {
        position = update(position);
      },
    });
    return {
      host,
      stop,
      show: (next: string[]) => {
        term = fakeTerminal(next, mouseTrackingMode);
      },
      get position() {
        return position;
      },
    };
  }

  it("measures the position from a wheel turn the CLI answered", () => {
    vi.useFakeTimers();
    try {
      const pane = stage(transcript(10, 10));

      wheel(pane.host, -1);
      pane.show([...transcript(7, 3), ...transcript(10, 7)]);
      vi.advanceTimersByTime(SETTLE_MS);

      expect(pane.position).toMatchObject({ offset: 3, linesPerNotch: 3 });
      pane.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("reads the end of the history off a turn that changed nothing", () => {
    vi.useFakeTimers();
    try {
      const pane = stage(transcript(10, 10));

      wheel(pane.host, -1);
      vi.advanceTimersByTime(SETTLE_MS);

      expect(pane.position).toMatchObject({ span: 0, spanKnown: true });
      pane.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * A held-down wheel would otherwise move the screen further than one screenful
   * between snapshots, leaving nothing for the two to be lined up by.
   */
  it("measures a long burst before it runs off the end of the screen", () => {
    vi.useFakeTimers();
    try {
      const pane = stage(transcript(10, 10));
      wheel(pane.host, -1);
      pane.show([...transcript(8, 2), ...transcript(10, 8)]);

      // The wheel never stops long enough for the quiet timer to fire, so only
      // the burst deadline can produce a reading.
      for (let i = 0; i < 5; i += 1) {
        vi.advanceTimersByTime(SETTLE_MS - 40);
        wheel(pane.host, -1);
      }

      expect(pane.position.offset).toBe(2);
      pane.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("only learns a notch's size from notches it sent itself", () => {
    vi.useFakeTimers();
    try {
      const pane = stage(transcript(10, 10));

      // A mouse wheel in pixel mode: the driver's idea of a notch, not ours.
      wheel(pane.host, -120, 0);
      pane.show([...transcript(4, 6), ...transcript(10, 4)]);
      vi.advanceTimersByTime(SETTLE_MS);

      expect(pane.position.offset).toBe(6);
      expect(pane.position.linesPerNotch).toBe(AT_LIVE_END.linesPerNotch);
      pane.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("leaves a pane whose terminal owns its scrollback alone", () => {
    vi.useFakeTimers();
    try {
      const pane = stage(transcript(10, 10), "none");

      wheel(pane.host, -1);
      vi.advanceTimersByTime(BURST_MS);

      expect(pane.position).toBe(AT_LIVE_END);
      pane.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("leaves no timer running behind a pane that goes away", () => {
    vi.useFakeTimers();
    try {
      const pane = stage(transcript(10, 10));
      wheel(pane.host, -1);

      pane.stop();

      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * What the probe is FOR: a CLI that holds its own history reveals it, and the
   * user ends up back where they started. Measured through the real tracker,
   * because keeping the two notches in separate bursts is the whole point.
   */
  it("makes an untouched pane's history measurable without moving the user", () => {
    vi.useFakeTimers();
    try {
      const pane = stage(transcript(10, 10));
      expect(hasMeasuredHistory(pane.position)).toBe(false);

      const stop = probeAppHistory((direction) => {
        wheel(pane.host, direction);
        // What the CLI repaints in answer: one line of older output on the way
        // back, the live screen again on the way home.
        pane.show(
          direction < 0
            ? [...transcript(9, 1), ...transcript(10, 9)]
            : transcript(10, 10),
        );
      });
      vi.advanceTimersByTime(PROBE_RETURN_MS);
      vi.advanceTimersByTime(SETTLE_MS);
      stop();

      // A history is now known to exist — so the bar has something to describe.
      expect(hasMeasuredHistory(pane.position)).toBe(true);
      // And the user is where they were: at the agent's newest output.
      expect(pane.position.offset).toBe(0);
      pane.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * And the honest negative: an application with nothing above its screen does
   * not move, which is the one exact answer this measurement gets — so the pane
   * keeps no bar rather than being given a fake one.
   */
  it("reports no history for an application that does not scroll", () => {
    vi.useFakeTimers();
    try {
      const pane = stage(transcript(10, 10));

      const stop = probeAppHistory((direction) => wheel(pane.host, direction));
      vi.advanceTimersByTime(PROBE_RETURN_MS);
      vi.advanceTimersByTime(SETTLE_MS);
      stop();

      expect(pane.position.spanKnown).toBe(true);
      expect(pane.position.span).toBe(0);
      expect(appScrollExtent(pane.position, 10).total).toBe(10);
      pane.stop();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("probeAppHistory", () => {
  it("goes one notch back and comes straight back", () => {
    vi.useFakeTimers();
    try {
      const sent: number[] = [];
      probeAppHistory((direction) => sent.push(direction));

      // Asked immediately, so the answer is on its way the moment the user
      // reaches for the bar.
      expect(sent).toEqual([-1]);

      vi.advanceTimersByTime(PROBE_RETURN_MS);

      expect(sent).toEqual([-1, 1]);
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * The two notches must be measured SEPARATELY. Sent inside one burst they
   * cancel out into an intent of zero, `trackAppScroll` measures nothing, and the
   * probe would move the screen twice for no answer at all.
   */
  it("waits out a settle period before returning", () => {
    vi.useFakeTimers();
    try {
      const sent: number[] = [];
      probeAppHistory((direction) => sent.push(direction));

      vi.advanceTimersByTime(SETTLE_MS);

      expect(sent).toEqual([-1]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("brings the application back when the probe is abandoned early", () => {
    vi.useFakeTimers();
    try {
      const sent: number[] = [];
      const stop = probeAppHistory((direction) => sent.push(direction));

      // The pointer left the hot zone a frame later. A pane parked one line into
      // its history would be the probe showing through.
      stop();

      expect(sent).toEqual([-1, 1]);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns exactly once, however it is torn down", () => {
    vi.useFakeTimers();
    try {
      const sent: number[] = [];
      const stop = probeAppHistory((direction) => sent.push(direction));

      vi.advanceTimersByTime(PROBE_RETURN_MS);
      stop();
      stop();

      expect(sent).toEqual([-1, 1]);
    } finally {
      vi.useRealTimers();
    }
  });
});
