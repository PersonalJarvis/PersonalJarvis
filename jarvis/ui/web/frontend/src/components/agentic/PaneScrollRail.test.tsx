import { useRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { Terminal } from "@xterm/xterm";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { PaneScrollRail } from "./PaneScrollRail";

const nativePointerEvent = globalThis.PointerEvent;

class PointerEventHarness extends MouseEvent {
  readonly pointerId: number;

  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 0;
  }
}

beforeAll(() => {
  // jsdom has pointer event names but no PointerEvent constructor, so Testing
  // Library would otherwise create an event with none of the drag coordinates.
  Object.defineProperty(globalThis, "PointerEvent", {
    configurable: true,
    value: PointerEventHarness,
  });
});

afterAll(() => {
  Object.defineProperty(globalThis, "PointerEvent", {
    configurable: true,
    value: nativePointerEvent,
  });
});

type Listener<T> = (value: T) => void;

function signal<T>() {
  const listeners = new Set<Listener<T>>();
  return {
    event(listener: Listener<T>) {
      listeners.add(listener);
      return { dispose: () => listeners.delete(listener) };
    },
    fire(value: T) {
      for (const listener of listeners) listener(value);
    },
  };
}

interface TerminalHarness {
  term: Terminal;
  scrollToLine: ReturnType<typeof vi.fn>;
  scrollLines: ReturnType<typeof vi.fn>;
  scrollToTop: ReturnType<typeof vi.fn>;
  scrollToBottom: ReturnType<typeof vi.fn>;
  /** Grow the scrollback WITHOUT firing an event — a pane that already wrote. */
  loadHistory: (next: { maxLine: number; line: number }) => void;
}

function fakeTerminal({
  type = "normal",
  line = 100,
  maxLine = 100,
  normalMaxLine = 0,
}: {
  type?: "normal" | "alternate";
  line?: number;
  maxLine?: number;
  /** Scrollback in the normal buffer while an alternate screen is displayed. */
  normalMaxLine?: number;
} = {}): TerminalHarness {
  const scroll = signal<number>();
  const parsed = signal<void>();
  const changed = signal<unknown>();
  const active = { type, baseY: maxLine, viewportY: line };
  const scrollToLine = vi.fn((next: number) => {
    active.viewportY = next;
    scroll.fire(next);
  });
  const scrollLines = vi.fn((amount: number) => {
    active.viewportY = Math.max(
      0,
      Math.min(active.baseY, active.viewportY + amount),
    );
    scroll.fire(active.viewportY);
  });
  const scrollToTop = vi.fn(() => scrollToLine(0));
  const scrollToBottom = vi.fn(() => scrollToLine(active.baseY));
  const term = {
    rows: 20,
    buffer: {
      active,
      normal: { baseY: normalMaxLine, viewportY: normalMaxLine },
      onBufferChange: changed.event,
    },
    onScroll: scroll.event,
    onWriteParsed: parsed.event,
    scrollToLine,
    scrollLines,
    scrollToTop,
    scrollToBottom,
  } as unknown as Terminal;
  return {
    term,
    scrollToLine,
    scrollLines,
    scrollToTop,
    scrollToBottom,
    loadHistory: ({ maxLine: nextMax, line: nextLine }) => {
      active.baseY = nextMax;
      active.viewportY = nextLine;
    },
  };
}

function RailHarness({
  name,
  term,
  onOpenHistory,
}: {
  name: string;
  term: Terminal;
  onOpenHistory?: () => void;
}) {
  const regionRef = useRef<HTMLDivElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(term);
  return (
    <div ref={regionRef} id={`terminal-${name}`}>
      <div ref={hostRef}>
        <div className="xterm-screen" data-testid={`xterm-screen-${name}`} />
      </div>
      <PaneScrollRail
        name={name}
        controlsId={`terminal-${name}`}
        regionRef={regionRef}
        hostRef={hostRef}
        terminalRef={terminalRef}
        epoch={1}
        appearance="dark"
        onOpenHistory={onOpenHistory}
      />
    </div>
  );
}

function giveTrackGeometry(name: string, height = 200): HTMLElement {
  const rail = screen.getByTestId(`pane-scroll-rail-${name}`);
  Object.defineProperty(rail, "clientHeight", {
    configurable: true,
    value: height,
  });
  rail.getBoundingClientRect = () =>
    ({
      top: 0,
      left: 0,
      right: 12,
      bottom: height,
      width: 12,
      height,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect;
  act(() => fireEvent.resize(window));
  return rail;
}

describe("PaneScrollRail", () => {
  it("drags the exact xterm history independently for each terminal ID", () => {
    const mika = fakeTerminal();
    const nova = fakeTerminal();
    render(
      <>
        <RailHarness name="Mika" term={mika.term} />
        <RailHarness name="Nova" term={nova.term} />
      </>,
    );
    const rail = giveTrackGeometry("Mika");
    giveTrackGeometry("Nova");
    const thumb = screen.getByTestId("pane-scroll-thumb-Mika");

    fireEvent.pointerDown(thumb, { button: 0, clientY: 180, pointerId: 1 });
    fireEvent.pointerMove(rail, { clientY: 35, pointerId: 1 });
    fireEvent.pointerUp(rail, { clientY: 35, pointerId: 1 });

    expect(mika.scrollToLine).toHaveBeenCalled();
    expect(mika.scrollToLine.mock.calls.at(-1)?.[0]).toBeLessThan(30);
    expect(nova.scrollToLine).not.toHaveBeenCalled();
    expect(thumb.style.top).not.toBe("");
    expect(
      screen
        .getByRole("scrollbar", { name: "Scroll Mika" })
        .getAttribute("aria-controls"),
    ).toBe("terminal-Mika");
  });

  it("jumps to a pressed track spot and keeps dragging from there", () => {
    const harness = fakeTerminal({ line: 100, maxLine: 100 });
    render(<RailHarness name="Odo" term={harness.term} />);
    const rail = giveTrackGeometry("Odo");

    fireEvent.pointerDown(rail, { button: 0, clientY: 20, pointerId: 2 });
    expect(harness.scrollToLine).toHaveBeenCalled();
    expect(harness.scrollToLine.mock.calls.at(-1)?.[0]).toBeLessThan(30);

    fireEvent.pointerMove(rail, { clientY: 190, pointerId: 2 });
    fireEvent.pointerUp(rail, { clientY: 190, pointerId: 2 });
    expect(harness.scrollToLine.mock.calls.at(-1)?.[0]).toBe(100);
  });

  it("scrolls with the keyboard on every pane the same way", () => {
    const harness = fakeTerminal({ line: 50 });
    render(<RailHarness name="Vera" term={harness.term} />);
    const rail = giveTrackGeometry("Vera");

    fireEvent.keyDown(rail, { key: "ArrowUp" });
    fireEvent.keyDown(rail, { key: "ArrowDown" });
    fireEvent.keyDown(rail, { key: "PageUp" });
    fireEvent.keyDown(rail, { key: "PageDown" });
    fireEvent.keyDown(rail, { key: "Home" });
    fireEvent.keyDown(rail, { key: "End" });

    expect(harness.scrollLines.mock.calls.map((call) => call[0])).toEqual([
      -1, 1, -20, 20,
    ]);
    expect(harness.scrollToTop).toHaveBeenCalledOnce();
    expect(harness.scrollToBottom).toHaveBeenCalledOnce();
  });

  it("forwards a wheel over the rail unchanged to its own terminal", () => {
    const harness = fakeTerminal();
    render(<RailHarness name="Nia" term={harness.term} />);
    const rail = giveTrackGeometry("Nia");
    const xtermScreen = screen.getByTestId("xterm-screen-Nia");
    const received = vi.fn();
    xtermScreen.addEventListener("wheel", received);

    fireEvent.wheel(rail, {
      deltaMode: WheelEvent.DOM_DELTA_PIXEL,
      deltaX: 5,
      deltaY: 120,
      shiftKey: true,
    });

    expect(received).toHaveBeenCalledOnce();
    const forwarded = received.mock.calls[0][0] as WheelEvent;
    expect(forwarded.deltaX).toBe(5);
    expect(forwarded.deltaY).toBe(120);
    expect(forwarded.shiftKey).toBe(true);
  });

  it("cancels an uncaptured drag when the pointer leaves the rail", () => {
    const harness = fakeTerminal({ line: 50 });
    render(<RailHarness name="Lumi" term={harness.term} />);
    const rail = giveTrackGeometry("Lumi");
    const thumb = screen.getByTestId("pane-scroll-thumb-Lumi");

    fireEvent.pointerDown(thumb, { button: 0, clientY: 100, pointerId: 6 });
    expect(rail.className).toContain("cursor-grabbing");
    fireEvent.pointerLeave(rail, { pointerId: 6 });
    expect(rail.className).not.toContain("cursor-grabbing");
  });

  it("says honestly when a full-screen app owns the pane", () => {
    const harness = fakeTerminal({ type: "alternate", line: 0, maxLine: 0 });
    render(<RailHarness name="Ida" term={harness.term} />);
    const rail = giveTrackGeometry("Ida");
    const thumb = screen.getByTestId("pane-scroll-thumb-Ida");

    // No history to scroll: the track is a faint full-length line, never a
    // bright bar that reads as a grip stuck at full height.
    expect(rail.dataset.scrollState).toBe("app");
    expect(thumb.style.height).toBe("200px");
    expect(thumb.className).toContain("bg-[#e7c46e]/30");
    expect(rail.title).toBe(
      "Scroll Ida: this CLI draws a full screen and keeps its own history — open the pane history to scroll it",
    );
  });

  it("offers the pane history where a full-screen CLI keeps its own", () => {
    // Claude Code 2.1.226 takes the alternate screen (measured 2026-08-09), so
    // xterm holds no history for it and no thumb can ever be honest. Rather
    // than a dead track, the rail carries a visible door to the one scrollable
    // copy — an empty rail is what reads as "the scrollbar is broken".
    const harness = fakeTerminal({ type: "alternate", line: 0, maxLine: 0 });
    const onOpenHistory = vi.fn();
    render(
      <RailHarness name="Sora" term={harness.term} onOpenHistory={onOpenHistory} />,
    );
    const rail = giveTrackGeometry("Sora");

    expect(rail.dataset.scrollState).toBe("app");
    fireEvent.click(screen.getByTestId("pane-scroll-history-Sora"));
    expect(onOpenHistory).toHaveBeenCalledOnce();
    // The press must not also be read as a track seek.
    expect(harness.scrollToLine).not.toHaveBeenCalled();
  });

  it("carries no history door on a pane whose own history is scrollable", () => {
    const harness = fakeTerminal({ line: 40, maxLine: 100 });
    render(<RailHarness name="Kai" term={harness.term} onOpenHistory={vi.fn()} />);
    giveTrackGeometry("Kai");

    expect(screen.queryByTestId("pane-scroll-history-Kai")).toBeNull();
  });

  it("re-reads an idle pane whose history arrived before it subscribed", () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      // The regression this pins: a replayed pane writes its whole scrollback
      // in one burst, and an idle pane emits nothing afterwards. Reading once
      // and waiting for an event left the rail showing a full-track thumb on
      // a pane full of history — "the scrollbar is just gone".
      const harness = fakeTerminal({ line: 0, maxLine: 0 });
      render(<RailHarness name="Rex" term={harness.term} />);
      const rail = giveTrackGeometry("Rex");
      expect(rail.dataset.scrollState).toBe("empty");

      harness.loadHistory({ maxLine: 300, line: 300 });
      act(() => {
        vi.advanceTimersByTime(600);
      });

      expect(rail.dataset.scrollState).toBe("history");
      const thumb = screen.getByTestId("pane-scroll-thumb-Rex");
      expect(Number.parseFloat(thumb.style.height)).toBeLessThan(200);
      expect(rail.title).toBe("Terminal line 300 of 300");
    } finally {
      vi.useRealTimers();
    }
  });
});
