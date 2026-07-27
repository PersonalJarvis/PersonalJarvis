import { useCallback, useRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import { PaneScrollbar } from "./PaneScrollbar";

const REGION = { top: 0, bottom: 300, left: 0, right: 400 };

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
  viewportY = 0,
  mouseTrackingMode = "none",
}: FakeOptions = {}) {
  const noop = () => ({ dispose() {} });
  return {
    rows,
    modes: { mouseTrackingMode },
    buffer: {
      active: { type, length, viewportY, baseY: length - rows },
      onBufferChange: noop,
    },
    onScroll: noop,
    onRender: noop,
    onResize: noop,
    scrollToLine: vi.fn(),
    scrollLines: vi.fn(),
  } as unknown as Terminal;
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

describe("PaneScrollbar", () => {
  beforeEach(() => {
    // jsdom measures nothing, and both the hover zone and the thumb's size are
    // pure geometry — so the two measurements the component takes are staged.
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
      configurable: true,
      value: 300,
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
    render(<Harness term={fakeTerminal()} />);

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
      render(<Harness term={fakeTerminal()} />);
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

  it("gives a Claude-Code-style pane a bar the terminal cannot provide", () => {
    // Alternate screen + mouse tracking: no scrollback in the terminal at all,
    // which is why the native viewport scrollbar was dead in these panes.
    render(
      <Harness
        term={fakeTerminal({
          type: "alternate",
          length: 24,
          mouseTrackingMode: "any",
        })}
      />,
    );

    reachForTheBar();

    const bar = screen.getByTestId("pane-scrollbar-Dana");
    expect(bar.dataset.mode).toBe("app");
    expect(screen.getByTestId("pane-scrollbar-thumb-Dana")).toBeTruthy();
  });

  it("hands wheel turns over the bar to the CLI that owns the screen", () => {
    render(
      <Harness
        term={fakeTerminal({
          type: "alternate",
          length: 24,
          mouseTrackingMode: "any",
        })}
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
    const term = fakeTerminal({ length: 1000 });
    render(<Harness term={term} />);
    reachForTheBar();

    fireEvent.wheel(screen.getByTestId("pane-scrollbar-Dana"), { deltaY: -120 });

    expect(term.scrollLines).toHaveBeenCalledWith(-3);
  });
});
