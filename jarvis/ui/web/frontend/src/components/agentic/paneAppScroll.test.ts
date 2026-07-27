import { describe, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import {
  AT_LIVE_END,
  BURST_MS,
  MAX_LINES_PER_NOTCH,
  SETTLE_MS,
  appScrollExtent,
  appTakesWheel,
  applyShift,
  detectShift,
  notchesForLines,
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

describe("appScrollExtent", () => {
  /*
   * Before anybody has scrolled, the size of the history is unknown but the
   * position is not: the application is showing its newest output. So the thumb
   * rests at the BOTTOM — which is the reading the reported bug was missing —
   * and is sized by an assumed screenful above it.
   */
  it("puts an unscrolled pane at the bottom of its track", () => {
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
});
