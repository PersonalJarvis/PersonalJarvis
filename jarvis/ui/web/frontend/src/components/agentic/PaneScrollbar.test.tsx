import { useCallback, useRef } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import { PaneScrollbar } from "./PaneScrollbar";
import { LINES_PER_NOTCH, SETTLE_MS } from "./scrollbarModel";

const REGION = { top: 0, bottom: 300, left: 0, right: 400 };
const TRACK_PX = 300;

/**
 * A fake terminal over a mutable state object, so a test can grow the buffer
 * underneath the bar the way a live agent does, and captured subscriptions so
 * it can fire the events xterm would.
 */
interface TermState {
  type: "normal" | "alternate";
  length: number;
  rows: number;
  viewportY: number;
  baseY: number;
  mouseTrackingMode: string;
  /** What the screen shows, for the overlay anchor; row index → text. */
  lines: string[];
}

function fakeTerminal(overrides: Partial<TermState> = {}) {
  const state: TermState = {
    type: "normal",
    length: 1000,
    rows: 24,
    viewportY: 976,
    baseY: 976,
    mouseTrackingMode: "none",
    lines: [],
    ...overrides,
  };
  const listeners: Record<string, (() => void)[]> = {};
  const on = (event: string) => (handler: () => void) => {
    (listeners[event] ??= []).push(handler);
    return { dispose() {} };
  };
  const term = {
    get rows() {
      return state.rows;
    },
    modes: {
      get mouseTrackingMode() {
        return state.mouseTrackingMode;
      },
    },
    buffer: {
      active: {
        get type() {
          return state.type;
        },
        get length() {
          return state.length;
        },
        get viewportY() {
          return state.viewportY;
        },
        get baseY() {
          return state.baseY;
        },
        getLine(y: number) {
          return { translateToString: () => state.lines[y] ?? "" };
        },
      },
      onBufferChange: on("bufferChange"),
    },
    onRender: on("render"),
    onResize: on("resize"),
    onScroll: on("scroll"),
    scrollToLine: vi.fn(),
    scrollLines: vi.fn(),
  } as unknown as Terminal;
  const fire = (event: string) => {
    for (const handler of listeners[event] ?? []) handler();
  };
  return { term, state, fire };
}

/** A Claude-Code-style pane: the CLI owns the screen and the wheel. */
function appTerminal(rows = 24) {
  return fakeTerminal({
    type: "alternate",
    length: rows,
    rows,
    viewportY: 0,
    baseY: 0,
    mouseTrackingMode: "any",
  });
}

function Harness({ term, epoch = 1 }: { term: Terminal; epoch?: number }) {
  const regionRef = useRef<HTMLDivElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const getTerminal = useCallback(() => term, [term]);
  return (
    <div ref={regionRef} data-testid="region">
      <div ref={hostRef} className="agentic-terminal-host">
        <div className="xterm-screen" />
      </div>
      <PaneScrollbar
        name="Kite"
        regionRef={regionRef}
        hostRef={hostRef}
        getTerminal={getTerminal}
        epoch={epoch}
        appearance="dark"
      />
    </div>
  );
}

/**
 * A pointer move with coordinates on it. jsdom ships no `PointerEvent`, and
 * the synthetic one Testing Library falls back to carries no `clientY` — a
 * `MouseEvent` of the same name does.
 */
function dragTo(element: Element, clientY: number) {
  fireEvent(element, new MouseEvent("pointermove", { clientY, bubbles: true }));
}

const bar = () => screen.queryByTestId("pane-scrollbar-Kite");
const thumb = () => screen.getByTestId("pane-scrollbar-thumb-Kite");
const thumbTop = () => parseFloat(thumb().style.top || "0");
const thumbHeight = () => parseFloat(thumb().style.height || "0");
const host = () => document.querySelector(".agentic-terminal-host")!;

function reachThePane() {
  fireEvent.mouseEnter(screen.getByTestId("region"));
}

function leaveThePane() {
  fireEvent.mouseLeave(screen.getByTestId("region"));
}

/** Every wheel event that reaches the CLI's screen, by deltaY. */
function watchScreen(): number[] {
  const seen: number[] = [];
  document
    .querySelector(".xterm-screen")!
    .addEventListener("wheel", (event) => {
      seen.push((event as WheelEvent).deltaY);
    });
  return seen;
}

describe("PaneScrollbar", () => {
  beforeEach(() => {
    // jsdom measures nothing; the two boxes the component reads are staged.
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
    render(<Harness term={fakeTerminal().term} />);
    expect(bar()).toBeNull();

    reachThePane();

    expect(bar()!.dataset.shown).toBe("true");
  });

  it("becomes intangible again after the pointer leaves", () => {
    vi.useFakeTimers();
    try {
      render(<Harness term={fakeTerminal().term} />);
      reachThePane();
      leaveThePane();
      act(() => vi.advanceTimersByTime(1000));
    } finally {
      vi.useRealTimers();
    }

    expect(bar()!.dataset.shown).toBe("false");
    // Invisible AND untouchable: a transparent strip that still swallowed
    // pointer events would break selecting text at the pane's right edge.
    expect(bar()!.className).toContain("pointer-events-none");
  });

  it("shows nothing on a pane with nothing to scroll", () => {
    render(<Harness term={fakeTerminal({ length: 24 }).term} />);

    reachThePane();

    expect(bar()).toBeNull();
  });

  it("gives a full-screen CLI's pane a bar with nothing measured at all", () => {
    render(<Harness term={appTerminal().term} />);

    reachThePane();

    expect(bar()!.dataset.kind).toBe("travel");
    // Standing at the newest output: the thumb rests on the bottom.
    expect(thumbTop() + thumbHeight()).toBe(TRACK_PX);
  });

  it("draws a scrolled-back terminal's thumb where it really stands", () => {
    render(<Harness term={fakeTerminal({ viewportY: 0 }).term} />);

    reachThePane();

    expect(bar()!.dataset.kind).toBe("exact");
    expect(thumbTop()).toBe(0);
  });

  it("keeps the thumb honest while the agent keeps talking", async () => {
    const { term, state, fire } = fakeTerminal();
    render(<Harness term={term} />);
    reachThePane();
    expect(bar()!.getAttribute("aria-valuemax")).toBe("976");

    // The agent prints another thousand lines while the bar is up.
    state.length = 2000;
    state.baseY = 1976;
    fire("scroll");

    await waitFor(() =>
      expect(bar()!.getAttribute("aria-valuemax")).toBe("1976"),
    );
  });

  it("takes a dragged thumb straight to the line it was dropped on", () => {
    const { term } = fakeTerminal();
    render(<Harness term={term} />);
    reachThePane();
    const grabbed = thumbTop();

    fireEvent.pointerDown(thumb(), { clientY: 200, pointerId: 1 });
    dragTo(thumb(), 200 - grabbed);

    // Dropped at the very top of the track: the top of the scrollback.
    expect(term.scrollToLine).toHaveBeenLastCalledWith(0);

    fireEvent.pointerUp(thumb(), { pointerId: 1 });
  });

  it("drags a full-screen CLI by relaying wheel notches to its screen", () => {
    render(<Harness term={appTerminal(24).term} />);
    reachThePane();
    const seen = watchScreen();
    const grabbed = thumbTop();

    fireEvent.pointerDown(thumb(), { clientY: 200, pointerId: 1 });
    dragTo(thumb(), 200 - grabbed);
    fireEvent.pointerUp(thumb(), { pointerId: 1 });

    // A full pull up asks for the whole assumed span — one screen, in notches.
    expect(seen.length).toBe(Math.trunc(24 / LINES_PER_NOTCH));
    expect(new Set(seen)).toEqual(new Set([-1]));
  });

  it("pages towards a press on the empty part of the track", () => {
    const { term } = fakeTerminal();
    render(<Harness term={term} />);
    reachThePane();

    // The thumb rests at the bottom, so a press at the top pages backwards.
    fireEvent.pointerDown(bar()!, { clientY: REGION.top + 4 });

    expect(term.scrollLines).toHaveBeenCalledWith(-23);
  });

  it("forwards a wheel over the bar to the terminal, untouched", () => {
    render(<Harness term={fakeTerminal().term} />);
    reachThePane();
    const seen = watchScreen();

    const notCancelled = fireEvent.wheel(bar()!, {
      deltaY: 120,
      deltaMode: 0,
    });

    expect(seen).toEqual([120]);
    // The bar has no wheel behaviour of its own and never cancels the event.
    expect(notCancelled).toBe(true);
  });

  it("never lays a hand on a wheel turned over the terminal", () => {
    render(<Harness term={appTerminal().term} />);

    let untouched = false;
    act(() => {
      untouched = host().dispatchEvent(
        new WheelEvent("wheel", {
          deltaY: -1,
          bubbles: true,
          cancelable: true,
        }),
      );
    });

    expect(untouched).toBe(true);
  });

  it("counts a CLI-held pane's travel and re-anchors at the live end", async () => {
    render(<Harness term={appTerminal(24).term} />);

    // Four notches back, before the bar was ever shown.
    for (let i = 0; i < 4; i += 1) fireEvent.wheel(host(), { deltaY: -1 });
    reachThePane();
    expect(thumbTop() + thumbHeight()).toBeLessThan(TRACK_PX);

    // Forward far past the live end: the count clamps, the thumb comes home.
    for (let i = 0; i < 8; i += 1) fireEvent.wheel(host(), { deltaY: 1 });
    await waitFor(() => expect(thumbTop() + thumbHeight()).toBe(TRACK_PX));
  });

  /*
   * The reported bug, at the level the user met it: the pane stood at its
   * newest output while the thumb hung mid-track, because notches the CLI had
   * silently ignored at the transcript's top stayed counted. The CLI's own
   * scrolled-back overlay is the anchor: the moment it leaves the screen, the
   * thumb comes home — no wheel traffic required.
   */
  it("snaps home the moment the CLI's scrolled-back overlay disappears", async () => {
    const { state, term, fire } = appTerminal(24);
    state.lines = Array.from({ length: 24 }, (_, i) => `transcript row ${i}`);
    state.lines[20] = "  86   Jump to bottom (ctrl+End) ↓";
    render(<Harness term={term} />);

    // A pile of wheel-ups, some of which the CLI ignored at its top.
    for (let i = 0; i < 40; i += 1) fireEvent.wheel(host(), { deltaY: -1 });
    reachThePane();
    expect(thumbTop() + thumbHeight()).toBeLessThan(TRACK_PX);

    // The CLI returns to the live end and erases its overlay.
    state.lines[20] = "";
    fire("render");

    await waitFor(() => expect(thumbTop() + thumbHeight()).toBe(TRACK_PX));
  });

  it("keeps standing away from the live end while the overlay is up", async () => {
    const { state, term } = appTerminal(24);
    state.lines = Array.from({ length: 24 }, (_, i) => `transcript row ${i}`);
    state.lines[20] = "  12   Jump to bottom (ctrl+End) ↓";
    render(<Harness term={term} />);

    // Enough wheel-downs to clamp any count to zero — but the CLI's screen
    // still says the view is away, and the overlay outranks the count.
    for (let i = 0; i < 10; i += 1) fireEvent.wheel(host(), { deltaY: 1 });
    reachThePane();

    await waitFor(() =>
      expect(thumbTop() + thumbHeight()).toBeLessThan(TRACK_PX),
    );
  });

  /*
   * The pinned-pane deadlock, end to end: the brake engages MID-DRAG (the
   * CLI stopped answering), and the rest of the drag must still send one
   * probe notch per step instead of going dead — the escape hatch that lets
   * a wrongly-braked pane free itself.
   */
  it("keeps probing upward while the brake holds", async () => {
    let clock = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => clock);
    const { state, term, fire } = appTerminal(24);
    state.lines = Array.from({ length: 24 }, (_, i) => `transcript row ${i}`);
    state.lines[20] = "  12   Jump to bottom (ctrl+End) ↓";
    render(<Harness term={term} />);
    reachThePane();

    // Prove movement once — a brake without proven movement must not engage.
    // Then WAIT for the frame that carries the sync: the change must be on
    // record before the drag below piles up unconfirmed notches.
    state.lines[0] = "transcript moved";
    fire("render");
    await act(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    expect(bar()!.dataset.kind).toBe("travel");

    // Grab the thumb and pull: the burst is relayed, the screen stays still.
    const seen = watchScreen();
    fireEvent.pointerDown(thumb(), { clientY: 250, pointerId: 1 });
    dragTo(thumb(), 220);
    expect(seen.length).toBeGreaterThan(0);

    // The pty stays silent past the settle window: the brake engages.
    clock += SETTLE_MS + 100;
    fire("render");
    await waitFor(() =>
      expect(bar()!.getAttribute("aria-valuemax")).toBe("3"),
    );

    // Further pulling probes with exactly one notch per step, never a burst.
    const before = seen.length;
    dragTo(thumb(), 100);
    fireEvent.pointerUp(thumb(), { pointerId: 1 });
    expect(seen.length - before).toBe(1);
    expect(seen[seen.length - 1]).toBe(-1);
  });

  it("starts a fresh terminal's count from zero", async () => {
    const first = appTerminal(24).term;
    const { rerender } = render(<Harness term={first} epoch={1} />);
    for (let i = 0; i < 4; i += 1) fireEvent.wheel(host(), { deltaY: -1 });
    reachThePane();
    expect(thumbTop() + thumbHeight()).toBeLessThan(TRACK_PX);

    // The pane restarted its agent: same component, replaced terminal.
    rerender(<Harness term={appTerminal(24).term} epoch={2} />);

    await waitFor(() => expect(thumbTop() + thumbHeight()).toBe(TRACK_PX));
  });
});
