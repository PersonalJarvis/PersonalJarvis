import { useCallback, useRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import { PaneScrollbar } from "./PaneScrollbar";
import { SETTLE_MS } from "./paneAppScroll";

const REGION = { top: 0, bottom: 300, left: 0, right: 400 };
const TRACK_PX = 300;

interface FakeOptions {
  type?: "normal" | "alternate";
  length?: number;
  rows?: number;
  viewportY?: number;
  mouseTrackingMode?: string;
}

/** A screen of agent output — rows recognisable enough to be tracked. */
function transcript(from: number, count: number): string[] {
  return Array.from({ length: count }, (_, i) => `line ${from + i} of output`);
}

function fakeTerminal({
  type = "normal",
  length = 1000,
  rows = 24,
  viewportY = 0,
  mouseTrackingMode = "none",
}: FakeOptions = {}) {
  const noop = () => ({ dispose() {} });
  let content = transcript(1, rows);
  const term = {
    rows,
    modes: { mouseTrackingMode },
    buffer: {
      active: {
        type,
        length,
        viewportY,
        baseY: length - rows,
        getLine: (index: number) =>
          index >= 0 && index < content.length
            ? { translateToString: () => content[index] }
            : undefined,
      },
      onBufferChange: noop,
    },
    onScroll: noop,
    onRender: noop,
    onResize: noop,
    scrollToLine: vi.fn(),
    scrollLines: vi.fn(),
  } as unknown as Terminal;
  return {
    term,
    /** What the CLI repaints after it handled a relayed wheel notch. */
    show(next: string[]) {
      content = next;
    },
  };
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

/** Move the pointer into the strip along the pane's right edge. */
function reachForTheBar() {
  fireEvent.mouseMove(screen.getByTestId("region"), {
    clientX: REGION.right - 6,
    clientY: 150,
  });
}

function thumbTop(): number {
  return parseFloat(
    screen.getByTestId("pane-scrollbar-thumb-Dana").style.top || "0",
  );
}

function thumbHeight(): number {
  return parseFloat(
    screen.getByTestId("pane-scrollbar-thumb-Dana").style.height || "0",
  );
}

describe("PaneScrollbar", () => {
  beforeEach(() => {
    // jsdom measures nothing, and both the hover zone and the thumb's size are
    // pure geometry — so the two measurements the component takes are staged.
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

  it("stays out of the way until the pointer reaches for it", () => {
    render(<Harness term={fakeTerminal().term} />);

    expect(screen.queryByTestId("pane-scrollbar-Dana")).toBeNull();

    reachForTheBar();

    const bar = screen.getByTestId("pane-scrollbar-Dana");
    expect(bar.dataset.shown).toBe("true");
    expect(bar.className).toContain("opacity-100");
    expect(screen.getByTestId("pane-scrollbar-thumb-Dana")).toBeTruthy();
  });

  it("fades out again once the pointer leaves the edge", () => {
    vi.useFakeTimers();
    try {
      render(<Harness term={fakeTerminal().term} />);
      reachForTheBar();
      fireEvent.mouseMove(screen.getByTestId("region"), {
        clientX: 40,
        clientY: 150,
      });
      act(() => vi.advanceTimersByTime(1000));
    } finally {
      vi.useRealTimers();
    }

    const bar = screen.getByTestId("pane-scrollbar-Dana");
    expect(bar.dataset.shown).toBe("false");
    expect(bar.className).toContain("opacity-0");
    // Invisible AND intangible — a transparent strip that still swallowed
    // clicks would break selecting text at the pane's right edge.
    expect(bar.className).toContain("pointer-events-none");
  });

  /*
   * The reported bug, at the level a user meets it. Alternate screen + mouse
   * tracking means the terminal holds no scrollback to read a position from —
   * and a pane showing the agent's newest output must still draw its thumb at
   * the BOTTOM. It used to draw a short bright marking halfway up the track,
   * which reads as "you are in the middle" to everyone who has ever used a
   * scrollbar.
   */
  it("puts a Claude-Code-style pane's thumb at the live end", () => {
    render(
      <Harness
        term={
          fakeTerminal({
            type: "alternate",
            length: 24,
            mouseTrackingMode: "any",
          }).term
        }
      />,
    );

    reachForTheBar();

    expect(screen.getByTestId("pane-scrollbar-Dana").dataset.mode).toBe("app");
    expect(thumbTop() + thumbHeight()).toBe(TRACK_PX);
    // And nothing is drawn on the thumb that could be read as a position of
    // its own.
    expect(screen.queryByTestId("pane-scrollbar-grip-Dana")).toBeNull();
  });

  /*
   * And it moves, because the position is measured rather than assumed: the
   * screen's content travelling down the pane IS the user going back through
   * the agent's own history.
   */
  it("moves the thumb up as the agent's screen scrolls back", () => {
    vi.useFakeTimers();
    try {
      const pane = fakeTerminal({
        type: "alternate",
        length: 24,
        mouseTrackingMode: "any",
      });
      render(<Harness term={pane.term} />);
      reachForTheBar();
      const atLiveEnd = thumbTop();

      fireEvent.wheel(document.querySelector(".xterm-screen")!, {
        deltaY: -1,
        deltaMode: 1,
      });
      pane.show([...transcript(-3, 4), ...transcript(1, 20)]);
      act(() => vi.advanceTimersByTime(SETTLE_MS));

      expect(thumbTop()).toBeLessThan(atLiveEnd);
      expect(screen.getByTestId("pane-scrollbar-Dana").dataset.shown).toBe(
        "true",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * A full-screen application with no history at all says so by not moving, and
   * then gets no bar rather than a thumb filling its own track.
   */
  it("takes the bar away from an app that turns out not to scroll", () => {
    vi.useFakeTimers();
    try {
      const pane = fakeTerminal({
        type: "alternate",
        length: 24,
        mouseTrackingMode: "any",
      });
      render(<Harness term={pane.term} />);
      reachForTheBar();

      fireEvent.wheel(document.querySelector(".xterm-screen")!, {
        deltaY: -1,
        deltaMode: 1,
      });
      act(() => vi.advanceTimersByTime(SETTLE_MS));

      expect(screen.queryByTestId("pane-scrollbar-Dana")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("hands wheel turns over the bar to the CLI that owns the screen", () => {
    render(
      <Harness
        term={
          fakeTerminal({
            type: "alternate",
            length: 24,
            mouseTrackingMode: "any",
          }).term
        }
      />,
    );
    reachForTheBar();

    const relayed: number[] = [];
    document
      .querySelector(".xterm-screen")!
      .addEventListener("wheel", (event) =>
        relayed.push((event as WheelEvent).deltaY),
      );

    fireEvent.wheel(screen.getByTestId("pane-scrollbar-Dana"), { deltaY: 120 });

    expect(relayed).toEqual([1, 1, 1]);
  });

  it("scrolls the viewport itself when the terminal holds the history", () => {
    const { term } = fakeTerminal({ length: 1000 });
    render(<Harness term={term} />);
    reachForTheBar();

    fireEvent.wheel(screen.getByTestId("pane-scrollbar-Dana"), { deltaY: -120 });

    expect(term.scrollLines).toHaveBeenCalledWith(-3);
  });
});
