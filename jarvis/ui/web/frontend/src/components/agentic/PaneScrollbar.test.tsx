import { useCallback, useRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import { PaneScrollbar } from "./PaneScrollbar";
import { LINES_PER_NOTCH } from "./paneScrollModel";

const REGION = { top: 0, bottom: 300, left: 0, right: 400 };
const TRACK_PX = 300;

interface FakeOptions {
  type?: "normal" | "alternate";
  length?: number;
  rows?: number;
  viewportY?: number;
  mouseTrackingMode?: string;
}

function fakeTerminal({
  type = "normal",
  length = 1000,
  rows = 24,
  viewportY = 976,
  mouseTrackingMode = "none",
}: FakeOptions = {}): Terminal {
  const noop = () => ({ dispose() {} });
  return {
    rows,
    modes: { mouseTrackingMode },
    buffer: {
      active: { type, length, viewportY, baseY: length - rows },
      onBufferChange: noop,
    },
    onRender: noop,
    onResize: noop,
    onScroll: noop,
    scrollToLine: vi.fn(),
    scrollLines: vi.fn(),
  } as unknown as Terminal;
}

/** A Claude-Code-style pane: the CLI owns the screen and the wheel. */
function appTerminal(rows = 24): Terminal {
  return fakeTerminal({
    type: "alternate",
    length: rows,
    rows,
    viewportY: 0,
    mouseTrackingMode: "any",
  });
}

function Harness({ term }: { term: Terminal }) {
  const regionRef = useRef<HTMLDivElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const getTerminal = useCallback(() => term, [term]);
  return (
    <div ref={regionRef} data-testid="region">
      <div ref={hostRef} className="agentic-terminal-host">
        <div className="xterm-screen" />
      </div>
      <PaneScrollbar
        name="Dana"
        regionRef={regionRef}
        hostRef={hostRef}
        getTerminal={getTerminal}
        epoch={1}
        appearance="dark"
      />
    </div>
  );
}

/**
 * A pointer move with coordinates on it.
 *
 * jsdom ships no `PointerEvent`, and the synthetic one Testing Library falls
 * back to arrives without `clientY` — so a drag driven by `fireEvent.pointerMove`
 * silently moves the thumb nowhere. A `MouseEvent` of the same type carries the
 * coordinates the handler reads.
 */
function dragTo(element: Element, clientY: number) {
  fireEvent(element, new MouseEvent("pointermove", { clientY, bubbles: true }));
}

const bar = () => screen.queryByTestId("pane-scrollbar-Dana");
const thumb = () => screen.getByTestId("pane-scrollbar-thumb-Dana");
const thumbTop = () => parseFloat(thumb().style.top || "0");
const thumbHeight = () => parseFloat(thumb().style.height || "0");

function reachThePane() {
  fireEvent.mouseEnter(screen.getByTestId("region"));
}

function leaveThePane() {
  fireEvent.mouseLeave(screen.getByTestId("region"));
}

/** What the CLI is handed when the bar relays a notch. */
function watchRelays(): number[] {
  const relayed: number[] = [];
  document
    .querySelector(".xterm-screen")!
    .addEventListener("wheel", (event) => {
      relayed.push((event as WheelEvent).deltaY);
    });
  return relayed;
}

describe("PaneScrollbar", () => {
  beforeEach(() => {
    // jsdom measures nothing, and both the track's height and the pane's box
    // are pure geometry — so the two measurements the component takes are
    // staged here.
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
      configurable: true,
      value: TRACK_PX,
    });
    HTMLElement.prototype.getBoundingClientRect = () =>
      ({
        ...REGION,
        width: REGION.right - REGION.left,
        height: REGION.bottom - REGION.top,
        x: REGION.left,
        y: REGION.top,
        toJSON: () => REGION,
      }) as DOMRect;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("stays out of the way until the pointer arrives", () => {
    render(<Harness term={fakeTerminal()} />);

    expect(bar()).toBeNull();

    reachThePane();

    expect(bar()!.dataset.shown).toBe("true");
    expect(bar()!.className).toContain("opacity-100");
  });

  /*
   * The bug this rewrite exists for, at the level a user meets it. Reaching a
   * Claude Code pane must produce a bar — full stop. The version before this one
   * asked the CLI to prove it had a history first, and every way that question
   * could go unanswered looked exactly like "nothing to scroll", so the bar
   * never appeared in the one kind of pane it was written for.
   */
  it("gives an application-held pane a bar, with nothing measured at all", () => {
    render(<Harness term={appTerminal()} />);

    reachThePane();

    expect(bar()!.dataset.shown).toBe("true");
    // Standing at the newest output: the thumb sits at the bottom.
    expect(thumbTop() + thumbHeight()).toBe(TRACK_PX);
  });

  it("fades out again once the pointer leaves", () => {
    vi.useFakeTimers();
    try {
      render(<Harness term={fakeTerminal()} />);
      reachThePane();
      leaveThePane();
      act(() => vi.advanceTimersByTime(1000));
    } finally {
      vi.useRealTimers();
    }

    expect(bar()!.dataset.shown).toBe("false");
    expect(bar()!.className).toContain("opacity-0");
    // Invisible AND intangible — a transparent strip that still swallowed
    // clicks would break selecting text at the pane's right edge.
    expect(bar()!.className).toContain("pointer-events-none");
  });

  it("shows nothing on a pane with nothing to scroll", () => {
    render(<Harness term={fakeTerminal({ length: 24, rows: 24 })} />);

    reachThePane();

    expect(bar()).toBeNull();
  });

  it("draws a scrolled-back terminal's thumb where it actually stands", () => {
    render(
      <Harness term={fakeTerminal({ length: 1000, rows: 24, viewportY: 0 })} />,
    );

    reachThePane();

    // At the very top of the scrollback.
    expect(thumbTop()).toBe(0);
  });

  it("scrolls the terminal itself when the terminal owns the history", () => {
    const term = fakeTerminal();
    render(<Harness term={term} />);
    reachThePane();

    fireEvent.wheel(bar()!, { deltaY: -1 });

    expect(term.scrollLines).toHaveBeenCalledWith(-LINES_PER_NOTCH);
  });

  /*
   * And hands the wheel to the CLI when the CLI owns the screen — the only
   * language a full-screen application listens in. Emitted as a real wheel
   * event so that xterm encodes it in whichever mouse protocol was negotiated.
   */
  it("hands the wheel to a CLI that owns the screen", () => {
    render(<Harness term={appTerminal()} />);
    reachThePane();
    const relayed = watchRelays();

    fireEvent.wheel(bar()!, { deltaY: -1 });

    expect(relayed).toEqual([-1]);
  });

  it("pages towards a click on the empty part of the track", () => {
    const term = fakeTerminal({ rows: 24 });
    render(<Harness term={term} />);
    reachThePane();

    // The thumb sits at the bottom, so a click at the top pages backwards.
    fireEvent.pointerDown(bar()!, { clientY: REGION.top + 4 });

    expect(term.scrollLines).toHaveBeenCalledWith(-23);
  });

  it("takes a dragged thumb straight to the line it was dropped on", () => {
    const term = fakeTerminal({ length: 1000, rows: 24, viewportY: 976 });
    render(<Harness term={term} />);
    reachThePane();
    const grabbed = thumbTop();

    fireEvent.pointerDown(thumb(), { clientY: 200, pointerId: 1 });
    dragTo(thumb(), 200 - grabbed);

    // Dragged to the top of the track: the top of the scrollback.
    expect(term.scrollToLine).toHaveBeenLastCalledWith(0);

    fireEvent.pointerUp(thumb(), { pointerId: 1 });
  });

  it("drags an application-held pane by relaying notches", () => {
    render(<Harness term={appTerminal(24)} />);
    reachThePane();
    const relayed = watchRelays();
    const grabbed = thumbTop();

    fireEvent.pointerDown(thumb(), { clientY: 200, pointerId: 1 });
    dragTo(thumb(), 200 - grabbed);
    fireEvent.pointerUp(thumb(), { pointerId: 1 });

    // A full drag up asks for the whole assumed span — one screen, in notches.
    expect(relayed.length).toBe(Math.trunc(24 / LINES_PER_NOTCH));
    expect(new Set(relayed)).toEqual(new Set([-1]));
  });

  /*
   * A wheel turn over the pane keeps the bar up on its own, so the user can see
   * where they have got to without having to find its edge first.
   */
  it("shows itself while the pane is being scrolled", () => {
    vi.useFakeTimers();
    try {
      render(<Harness term={appTerminal()} />);
      const host = document.querySelector(".agentic-terminal-host")!;

      fireEvent.wheel(host, { deltaY: -1 });
      act(() => vi.advanceTimersByTime(50));
      expect(bar()!.dataset.shown).toBe("true");

      act(() => vi.advanceTimersByTime(2000));
      expect(bar()!.dataset.shown).toBe("false");
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * And that turn is counted: a pane the user scrolled back through stands away
   * from its newest output, and its thumb has to say so the moment the bar comes
   * up — including on a CLI whose screen reveals nothing about where it is.
   */
  it("follows a CLI-held pane back through its own history", () => {
    render(<Harness term={appTerminal(24)} />);
    const host = document.querySelector(".agentic-terminal-host")!;

    for (let i = 0; i < 4; i += 1) fireEvent.wheel(host, { deltaY: -1 });
    reachThePane();

    // Away from the live end, so the thumb has left the bottom of the track.
    expect(thumbTop() + thumbHeight()).toBeLessThan(TRACK_PX);

    for (let i = 0; i < 8; i += 1) fireEvent.wheel(host, { deltaY: 1 });

    // Back at the newest output, however far past it the wheel was turned.
    expect(thumbTop() + thumbHeight()).toBe(TRACK_PX);
  });
});
