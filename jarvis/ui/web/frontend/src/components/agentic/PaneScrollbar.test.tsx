import { useCallback, useRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import { PaneScrollbar } from "./PaneScrollbar";
import {
  PROBE_RETURN_MS,
  PROBE_STALE_MS,
  PROBE_WAIT_MS,
  SETTLE_MS,
} from "./paneAppScroll";

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
   * The second report on this strip, at the level a user meets it. Reaching for
   * the right edge of a Claude Code pane nobody has scrolled must reveal NOTHING
   * YET: no measurement has been taken, so there is no history to describe, and
   * the bar that used to appear here spent half its track on empty space — on
   * every pane of a workspace that was just opened.
   *
   * "Yet" is the third round (below). Silence is right for the instant the
   * pointer arrives; leaving it there permanently is what made the bar
   * unreachable.
   */
  it("shows nothing on a Claude-Code-style pane nobody has scrolled", () => {
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

    expect(screen.queryByTestId("pane-scrollbar-Dana")).toBeNull();
  });

  /*
   * The third report, and the one that made the bar unusable rather than merely
   * wrong: in a Claude Code pane there was NO WAY to bring it up. The measurement
   * only ever happened as a side effect of a wheel turn, so reaching for the edge
   * — the gesture the bar is revealed by — left the pane silent forever, and the
   * one input that did work is the one that makes a scrollbar unnecessary.
   *
   * So reaching for it now ASKS: a notch back, a settle period, a notch home. The
   * bar arrives describing a real measurement, and the pointer never moved the
   * user anywhere.
   */
  it("asks the CLI for its history when the pointer reaches for the bar", () => {
    vi.useFakeTimers();
    try {
      const pane = fakeTerminal({
        type: "alternate",
        length: 24,
        mouseTrackingMode: "any",
      });
      const relayed: number[] = [];
      render(<Harness term={pane.term} />);
      document
        .querySelector(".xterm-screen")!
        .addEventListener("wheel", (event) => {
          const delta = (event as WheelEvent).deltaY;
          relayed.push(delta);
          // The CLI answers the probe: one line of older output, then home.
          pane.show(
            delta < 0
              ? [...transcript(0, 1), ...transcript(1, 23)]
              : transcript(1, 24),
          );
        });

      reachForTheBar();
      act(() =>
        vi.advanceTimersByTime(PROBE_WAIT_MS + PROBE_RETURN_MS + SETTLE_MS * 2),
      );

      // Asked, and brought back — the user is still at the newest output.
      expect(relayed).toEqual([-1, 1]);
      // And the bar is there now, describing what was measured.
      const bar = screen.getByTestId("pane-scrollbar-Dana");
      expect(bar.dataset.mode).toBe("app");
      expect(bar.dataset.shown).toBe("true");
      expect(thumbTop() + thumbHeight()).toBe(TRACK_PX);
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * The probe is a one-off. It moves the application by a notch and back, which
   * nobody notices once — and would be a visible twitch on every pass of the
   * pointer along the pane's edge.
   */
  it("asks only once per terminal", () => {
    vi.useFakeTimers();
    try {
      const pane = fakeTerminal({
        type: "alternate",
        length: 24,
        mouseTrackingMode: "any",
      });
      const relayed: number[] = [];
      render(<Harness term={pane.term} />);
      document
        .querySelector(".xterm-screen")!
        .addEventListener("wheel", (event) => {
          relayed.push((event as WheelEvent).deltaY);
          pane.show(
            (event as WheelEvent).deltaY < 0
              ? [...transcript(0, 1), ...transcript(1, 23)]
              : transcript(1, 24),
          );
        });

      reachForTheBar();
      act(() =>
        vi.advanceTimersByTime(PROBE_WAIT_MS + PROBE_RETURN_MS + SETTLE_MS * 2),
      );
      // Pointer leaves and comes back.
      fireEvent.mouseMove(screen.getByTestId("region"), {
        clientX: 40,
        clientY: 150,
      });
      act(() => vi.advanceTimersByTime(1000));
      reachForTheBar();
      act(() =>
        vi.advanceTimersByTime(PROBE_WAIT_MS + PROBE_RETURN_MS + SETTLE_MS * 2),
      );

      expect(relayed).toEqual([-1, 1]);
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * The one answer that expires. A pane opened a minute ago holds a single screen
   * and nothing above it, so the first probe honestly finds no history — and a
   * pane is at its emptiest exactly when somebody first reaches for its edge. Ten
   * minutes of an agent printing later there are pages of it, and treating that
   * first reading as final would leave the pane bar-less for good: the original
   * bug arriving through a different door.
   */
  it("asks again once a pane that had no history has had time to fill up", () => {
    vi.useFakeTimers();
    try {
      const pane = fakeTerminal({
        type: "alternate",
        length: 24,
        mouseTrackingMode: "any",
      });
      const relayed: number[] = [];
      let hasHistory = false;
      render(<Harness term={pane.term} />);
      document
        .querySelector(".xterm-screen")!
        .addEventListener("wheel", (event) => {
          const delta = (event as WheelEvent).deltaY;
          relayed.push(delta);
          // Nothing above the screen at first, so the CLI repaints unchanged.
          if (!hasHistory) return;
          pane.show(
            delta < 0
              ? [...transcript(0, 1), ...transcript(1, 23)]
              : transcript(1, 24),
          );
        });

      const reachAndWait = () => {
        reachForTheBar();
        act(() =>
          vi.advanceTimersByTime(
            PROBE_WAIT_MS + PROBE_RETURN_MS + SETTLE_MS * 2,
          ),
        );
        // Pointer away again, so the next reach is a fresh one.
        fireEvent.mouseMove(screen.getByTestId("region"), {
          clientX: 40,
          clientY: 150,
        });
        act(() => vi.advanceTimersByTime(1000));
      };

      reachAndWait();
      expect(relayed).toEqual([-1, 1]);
      expect(screen.queryByTestId("pane-scrollbar-Dana")).toBeNull();

      // Straight away, the answer still stands — no second probe.
      reachAndWait();
      expect(relayed).toEqual([-1, 1]);

      // A while later the pane has printed, and the question is worth asking
      // again.
      hasHistory = true;
      act(() => vi.advanceTimersByTime(PROBE_STALE_MS));
      reachForTheBar();
      act(() =>
        vi.advanceTimersByTime(PROBE_WAIT_MS + PROBE_RETURN_MS + SETTLE_MS * 2),
      );

      expect(relayed).toEqual([-1, 1, -1, 1]);
      expect(screen.getByTestId("pane-scrollbar-Dana").dataset.mode).toBe(
        "app",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * The same rule for the question that was never answered at all — a notch
   * relayed into a terminal that was still being built, or while the agent was
   * repainting its whole screen. Nothing is measured either way, so the pane
   * looks exactly like one with no history: no bar. Treating only an explicit
   * "nothing here" as re-askable left such a pane one unlucky probe away from
   * having no scrollbar for the rest of its life, which is the original bug
   * with a rarer trigger.
   */
  it("asks again when the first probe went unanswered", () => {
    vi.useFakeTimers();
    try {
      const pane = fakeTerminal({
        type: "alternate",
        length: 24,
        mouseTrackingMode: "any",
      });
      const relayed: number[] = [];
      render(<Harness term={pane.term} />);
      const host = document.querySelector(".agentic-terminal-host")!;
      const paneScreen = host.querySelector(".xterm-screen")!;
      paneScreen.addEventListener("wheel", (event) => {
        const delta = (event as WheelEvent).deltaY;
        relayed.push(delta);
        pane.show(
          delta < 0
            ? [...transcript(0, 1), ...transcript(1, 23)]
            : transcript(1, 24),
        );
      });
      // The terminal is not up yet: there is nothing to hand a notch to.
      paneScreen.remove();

      reachForTheBar();
      act(() =>
        vi.advanceTimersByTime(PROBE_WAIT_MS + PROBE_RETURN_MS + SETTLE_MS * 2),
      );
      expect(relayed).toEqual([]);
      expect(screen.queryByTestId("pane-scrollbar-Dana")).toBeNull();

      // Pointer away, the terminal arrives, and the question is asked again.
      fireEvent.mouseMove(screen.getByTestId("region"), {
        clientX: 40,
        clientY: 150,
      });
      host.appendChild(paneScreen);
      act(() => vi.advanceTimersByTime(PROBE_STALE_MS));
      reachForTheBar();
      act(() =>
        vi.advanceTimersByTime(PROBE_WAIT_MS + PROBE_RETURN_MS + SETTLE_MS * 2),
      );

      expect(relayed).toEqual([-1, 1]);
      expect(screen.getByTestId("pane-scrollbar-Dana").dataset.mode).toBe(
        "app",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * And it is only for panes whose application holds the history. A terminal that
   * owns its scrollback already reports the truth, and a relayed notch there would
   * scroll the user's view for no reason at all.
   */
  it("asks nothing of a pane whose terminal owns its scrollback", () => {
    vi.useFakeTimers();
    try {
      render(<Harness term={fakeTerminal({ length: 1000 }).term} />);
      const relayed: number[] = [];
      document
        .querySelector(".xterm-screen")!
        .addEventListener("wheel", (event) =>
          relayed.push((event as WheelEvent).deltaY),
        );

      reachForTheBar();
      act(() =>
        vi.advanceTimersByTime(PROBE_WAIT_MS + PROBE_RETURN_MS + SETTLE_MS * 2),
      );

      expect(relayed).toEqual([]);
      // It needed no asking: the bar is up on its own.
      expect(screen.getByTestId("pane-scrollbar-Dana").dataset.mode).toBe(
        "scrollback",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  /*
   * The FIRST reported bug, which the silence above must not undo: once the pane
   * has been scrolled and brought back to the agent's newest output, its thumb
   * sits at the BOTTOM. It used to draw a short bright marking halfway up the
   * track, which reads as "you are in the middle" to everyone who has ever used
   * a scrollbar.
   */
  it("puts a scrolled pane's thumb at the live end once it returns there", () => {
    vi.useFakeTimers();
    try {
      const pane = fakeTerminal({
        type: "alternate",
        length: 24,
        mouseTrackingMode: "any",
      });
      render(<Harness term={pane.term} />);
      reachForTheBar();

      // Back four lines into the history, then all the way home again.
      fireEvent.wheel(document.querySelector(".xterm-screen")!, {
        deltaY: -1,
        deltaMode: 1,
      });
      pane.show([...transcript(-3, 4), ...transcript(1, 20)]);
      act(() => vi.advanceTimersByTime(SETTLE_MS));

      fireEvent.wheel(document.querySelector(".xterm-screen")!, {
        deltaY: 1,
        deltaMode: 1,
      });
      pane.show(transcript(1, 24));
      act(() => vi.advanceTimersByTime(SETTLE_MS));

      expect(screen.getByTestId("pane-scrollbar-Dana").dataset.mode).toBe(
        "app",
      );
      expect(thumbTop() + thumbHeight()).toBe(TRACK_PX);
      // And nothing is drawn on the thumb that could be read as a position of
      // its own.
      expect(screen.queryByTestId("pane-scrollbar-grip-Dana")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
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

      fireEvent.wheel(document.querySelector(".xterm-screen")!, {
        deltaY: -1,
        deltaMode: 1,
      });
      pane.show([...transcript(-3, 4), ...transcript(1, 20)]);
      act(() => vi.advanceTimersByTime(SETTLE_MS));

      // The measurement is what brings the bar into the document at all, and it
      // arrives already showing the position it measured: above the live end.
      expect(thumbTop() + thumbHeight()).toBeLessThan(TRACK_PX);
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
    vi.useFakeTimers();
    try {
      const pane = fakeTerminal({
        type: "alternate",
        length: 24,
        mouseTrackingMode: "any",
      });
      render(<Harness term={pane.term} />);
      reachForTheBar();

      // One measured notch, purely to bring the bar into the document: an
      // app-mode pane nothing has been measured on draws none, so there would be
      // nothing to turn a wheel over.
      fireEvent.wheel(document.querySelector(".xterm-screen")!, {
        deltaY: -1,
        deltaMode: 1,
      });
      pane.show([...transcript(-3, 4), ...transcript(1, 20)]);
      act(() => vi.advanceTimersByTime(SETTLE_MS));

      const relayed: number[] = [];
      document
        .querySelector(".xterm-screen")!
        .addEventListener("wheel", (event) =>
          relayed.push((event as WheelEvent).deltaY),
        );

      fireEvent.wheel(screen.getByTestId("pane-scrollbar-Dana"), {
        deltaY: 120,
      });

      expect(relayed).toEqual([1, 1, 1]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("scrolls the viewport itself when the terminal holds the history", () => {
    const { term } = fakeTerminal({ length: 1000 });
    render(<Harness term={term} />);
    reachForTheBar();

    fireEvent.wheel(screen.getByTestId("pane-scrollbar-Dana"), {
      deltaY: -120,
    });

    expect(term.scrollLines).toHaveBeenCalledWith(-3);
  });
});
