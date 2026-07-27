import { describe, expect, it } from "vitest";
import type { Terminal } from "@xterm/xterm";
import {
  GRIP_MARKER_PX,
  GRIP_TRAVEL_PX,
  JOG_STEP_PX,
  MIN_THUMB_PX,
  jogNotches,
  lineForThumbTop,
  readScrollState,
  relayWheelNotch,
  thumbGeometry,
} from "./paneScroll";

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
    );

    expect(state.mode).toBe("app");
    expect(state.rows).toBe(30);
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
    );

    expect(state.mode).toBe("app");
    // No leftover position from the frozen viewport: the grip encodes none.
    expect(state.top).toBe(0);
    expect(thumbGeometry(state, 400)).toEqual({
      topPx: 0,
      heightPx: 400,
      markerPx: 189,
    });
  });

  it("stays away from a full-screen app that did not take the mouse", () => {
    // Relaying a wheel notch to one of those would arrive in its input as the
    // raw escape sequence, so no bar is the correct answer.
    expect(
      readScrollState(fakeTerminal({ type: "alternate" })).mode,
    ).toBe("none");
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
    const geometry = thumbGeometry(state, 400);

    expect(geometry).not.toBeNull();
    expect(geometry!.topPx + geometry!.heightPx).toBe(400);
  });

  /*
   * The bug this shape exists to prevent: a fixed 44px grip centred in the
   * track was read — by the only grammar scrollbars have — as "you are halfway
   * up", while the pane sat at the live end of Claude Code's transcript. A bar
   * that spans the track cannot be read that way in either direction.
   */
  it("spans the whole track in app mode — no length of it means a position", () => {
    const state = readScrollState(
      fakeTerminal({ type: "alternate", mouseTrackingMode: "any" }),
    );

    expect(thumbGeometry(state, 300)).toEqual({
      topPx: 0,
      heightPx: 300,
      markerPx: 139,
    });
  });

  it("lets the app-mode marking follow a drag, within its travel", () => {
    const state = readScrollState(
      fakeTerminal({ type: "alternate", mouseTrackingMode: "any" }),
    );

    const dragged = thumbGeometry(state, 300, 40);
    // The bar itself never moves — only its marking, as drag feedback.
    expect(dragged?.topPx).toBe(0);
    expect(dragged?.heightPx).toBe(300);
    expect(dragged?.markerPx).toBe(179);
    // A long drag keeps relaying wheel notches, but the marking stops moving:
    // running it to the track's end would restore the very "you are here" read
    // the full-track bar is here to refuse.
    expect(thumbGeometry(state, 300, 9000)?.markerPx).toBe(
      139 + GRIP_TRAVEL_PX,
    );
  });

  it("keeps the app-mode marking inside a track shorter than itself", () => {
    const state = readScrollState(
      fakeTerminal({ type: "alternate", mouseTrackingMode: "any" }),
    );

    expect(thumbGeometry(state, GRIP_MARKER_PX - 8)).toEqual({
      topPx: 0,
      heightPx: GRIP_MARKER_PX - 8,
      markerPx: 0,
    });
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
});

describe("jogNotches", () => {
  it("converts a drag distance into whole wheel notches", () => {
    expect(jogNotches(0)).toBe(0);
    expect(jogNotches(JOG_STEP_PX - 1)).toBe(0);
    expect(jogNotches(JOG_STEP_PX * 3)).toBe(3);
    expect(jogNotches(-JOG_STEP_PX * 2)).toBe(-2);
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
    // at all and be dropped before it is ever encoded.
    expect(seen[0].deltaMode).toBe(1);
    expect(seen[0].deltaY).toBe(-1);
  });

  it("does nothing without a rendered terminal to aim at", () => {
    expect(relayWheelNotch(null, 1)).toBe(false);
    expect(relayWheelNotch(document.createElement("div"), 1)).toBe(false);
  });
});
