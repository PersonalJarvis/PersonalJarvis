import { describe, expect, it } from "vitest";
import type { Terminal } from "@xterm/xterm";
import {
  MIN_THUMB_PX,
  lineForThumbTop,
  readScrollState,
  relayWheelNotch,
  thumbGeometry,
} from "./paneScroll";
import { AT_LIVE_END, type AppScrollPosition } from "./paneAppScroll";

interface FakeOptions {
  type?: "normal" | "alternate";
  length?: number;
  rows?: number;
  viewportY?: number;
  mouseTrackingMode?: string;
}

function fakeTerminal({
  type = "normal",
  length = 100,
  rows = 24,
  viewportY = 0,
  mouseTrackingMode = "none",
}: FakeOptions = {}): Terminal {
  return {
    rows,
    modes: { mouseTrackingMode },
    buffer: { active: { type, length, viewportY, baseY: length - rows } },
  } as unknown as Terminal;
}

/** An app-mode pane whose history has been measured end to end. */
function measured(
  overrides: Partial<AppScrollPosition> = {},
): AppScrollPosition {
  return {
    offset: 0,
    span: 400,
    spanKnown: true,
    linesPerNotch: 3,
    ...overrides,
  };
}

describe("readScrollState", () => {
  it("reports scrollback for a terminal that holds its own history", () => {
    const state = readScrollState(fakeTerminal({ length: 100, rows: 24 }));

    expect(state).toEqual({ mode: "scrollback", total: 100, rows: 24, top: 0 });
  });

  it("reports nothing to scroll while the buffer still fits on screen", () => {
    expect(readScrollState(fakeTerminal({ length: 24, rows: 24 })).mode).toBe(
      "none",
    );
  });

  /*
   * The regression this whole component exists for. Claude Code 2.1.220 opens a
   * session with ESC[?1049h (alternate screen) plus ESC[?1000h/1002h/1003h/1006h
   * (SGR mouse tracking): the terminal keeps no scrollback, so the native
   * viewport scrollbar has nothing to move, and the wheel belongs to the CLI.
   */
  it("reports app mode for a full-screen CLI that took the mouse", () => {
    const state = readScrollState(
      fakeTerminal({ type: "alternate", mouseTrackingMode: "any", rows: 30 }),
      measured({ offset: 120 }),
    );

    expect(state).toEqual({ mode: "app", total: 430, rows: 30, top: 280 });
  });

  /*
   * The follow-up regression, and the reason the check reads the mouse before
   * the buffer type. Claude Code 2.1.195 was measured holding the mouse while
   * still on the NORMAL buffer, leaving a viewport of `scrollHeight 286 /
   * clientHeight 242 / scrollTop 44` behind it: a few stale lines, pinned to
   * the end forever. Read as scrollback that draws a thumb filling 85% of the
   * track, parked at the bottom — telling somebody halfway up the agent's own
   * transcript that they are at the very end of it.
   */
  it("reports app mode for a CLI that took the mouse on the normal buffer", () => {
    const state = readScrollState(
      fakeTerminal({
        type: "normal",
        mouseTrackingMode: "any",
        length: 28,
        rows: 24,
        viewportY: 4,
      }),
      measured({ offset: 200 }),
    );

    expect(state.mode).toBe("app");
    // Not one number from the frozen viewport: the position is the measured one.
    expect(state).toMatchObject({ total: 424, rows: 24, top: 200 });
  });

  /*
   * The follow-up report on the same strip: a pane nobody has scrolled yet gets
   * NO bar. It used to be credited with an assumed screenful of history, which
   * sized a half-track thumb and left the other half as empty furniture down the
   * side of every pane in a freshly opened workspace.
   */
  it("draws no bar for an app-mode pane nothing has been measured on", () => {
    const state = readScrollState(
      fakeTerminal({ type: "alternate", mouseTrackingMode: "any", rows: 30 }),
      AT_LIVE_END,
    );

    expect(state.mode).toBe("none");
    expect(thumbGeometry(state, 300)).toBeNull();
  });

  /*
   * And the moment there IS a measurement, the pane showing the agent's newest
   * output draws its thumb AT THE BOTTOM — the original reported bug, which the
   * gate above must not quietly undo. `span` without `offset` is a pane that
   * travelled back through the history and came home to the live end.
   */
  it("puts a measured app-mode pane at the live end", () => {
    const state = readScrollState(
      fakeTerminal({ type: "alternate", mouseTrackingMode: "any", rows: 30 }),
      measured({ offset: 0, span: 90, spanKnown: true }),
    );
    const geometry = thumbGeometry(state, 300)!;

    expect(state.mode).toBe("app");
    expect(geometry.topPx + geometry.heightPx).toBe(300);
  });

  /*
   * A full-screen application with no history of its own — a dashboard, a pager
   * on a short file — proves it the first time a scroll moves nothing. From then
   * on it gets no bar rather than a thumb filling its own track.
   */
  it("takes the bar away from an app measured as having no history", () => {
    const state = readScrollState(
      fakeTerminal({ type: "alternate", mouseTrackingMode: "any" }),
      measured({ span: 0 }),
    );

    expect(state.mode).toBe("none");
  });

  it("stays away from a full-screen app that did not take the mouse", () => {
    // Relaying a wheel notch to one of those would arrive in its input as the
    // raw escape sequence, so no bar is the correct answer.
    expect(readScrollState(fakeTerminal({ type: "alternate" })).mode).toBe(
      "none",
    );
  });

  it("survives a terminal that is not built yet", () => {
    expect(readScrollState(null).mode).toBe("none");
    expect(readScrollState({} as Terminal).mode).toBe("none");
  });
});

describe("thumbGeometry", () => {
  it("sizes the thumb by how much of the buffer is on screen", () => {
    const state = readScrollState(fakeTerminal({ length: 400, rows: 100 }));

    expect(thumbGeometry(state, 400)).toEqual({ topPx: 0, heightPx: 100 });
  });

  it("keeps a deep scrollback's thumb grabbable", () => {
    const state = readScrollState(fakeTerminal({ length: 10000, rows: 24 }));

    expect(thumbGeometry(state, 300)?.heightPx).toBe(MIN_THUMB_PX);
  });

  it("puts the thumb at the bottom when the viewport is at the live end", () => {
    const state = readScrollState(
      fakeTerminal({ length: 124, rows: 24, viewportY: 100 }),
    );
    const geometry = thumbGeometry(state, 400)!;

    expect(geometry.topPx + geometry.heightPx).toBe(400);
  });

  /*
   * The bug this shape exists to prevent, from the other side. A short bright
   * shape halfway up a track says "you are in the middle" in the only grammar
   * scrollbars have — so an app-mode pane draws its thumb from a real measured
   * position, through this same geometry, rather than parking a marking in the
   * middle and hoping to be read as saying nothing.
   */
  it("draws an app-mode pane through the very same geometry", () => {
    const term = fakeTerminal({
      type: "alternate",
      mouseTrackingMode: "any",
      rows: 24,
    });

    expect(thumbGeometry(readScrollState(term, measured()), 400)).toEqual(
      thumbGeometry(
        readScrollState(fakeTerminal({ length: 424, rows: 24, viewportY: 400 })),
        400,
      ),
    );
    // And the top of the history is the top of the track.
    expect(
      thumbGeometry(readScrollState(term, measured({ offset: 400 })), 400)
        ?.topPx,
    ).toBe(0);
  });

  it("draws nothing when there is nothing to scroll", () => {
    expect(thumbGeometry(readScrollState(null), 300)).toBeNull();
    expect(
      thumbGeometry(readScrollState(fakeTerminal({ length: 400 })), 0),
    ).toBeNull();
  });
});

describe("lineForThumbTop", () => {
  it("maps the ends of the track to the ends of the scrollback", () => {
    const state = readScrollState(fakeTerminal({ length: 1024, rows: 24 }));

    expect(lineForThumbTop(0, 400, state)).toBe(0);
    expect(lineForThumbTop(400, 400, state)).toBe(1000);
  });

  it("clamps a drag that ran past the track", () => {
    const state = readScrollState(fakeTerminal({ length: 1024, rows: 24 }));

    expect(lineForThumbTop(-500, 400, state)).toBe(0);
    expect(lineForThumbTop(5000, 400, state)).toBe(1000);
  });

  it("answers for an app-mode pane too, so its thumb can be dragged", () => {
    const state = readScrollState(
      fakeTerminal({ type: "alternate", mouseTrackingMode: "any", rows: 24 }),
      measured({ offset: 400 }),
    );

    expect(lineForThumbTop(0, 400, state)).toBe(0);
    expect(lineForThumbTop(400, 400, state)).toBe(400);
  });
});

describe("relayWheelNotch", () => {
  function host(): HTMLElement {
    const element = document.createElement("div");
    const screen = document.createElement("div");
    screen.className = "xterm-screen";
    element.appendChild(screen);
    document.body.appendChild(element);
    return element;
  }

  it("dispatches a wheel event xterm can encode for the running CLI", () => {
    const element = host();
    const seen: WheelEvent[] = [];
    element.addEventListener("wheel", (event) => seen.push(event as WheelEvent));

    relayWheelNotch(element, -1);

    expect(seen).toHaveLength(1);
    // Lines, not pixels: xterm divides a pixel delta by the row height and
    // carries the remainder, so a synthetic pixel delta can round to no lines
    // at all and be dropped before it is ever encoded. It is also how
    // ./paneAppScroll recognises a notch it sent itself.
    expect(seen[0].deltaMode).toBe(1);
    expect(seen[0].deltaY).toBe(-1);
  });

  it("does nothing without a rendered terminal to aim at", () => {
    expect(relayWheelNotch(null, 1)).toBe(false);
    expect(relayWheelNotch(document.createElement("div"), 1)).toBe(false);
  });
});
