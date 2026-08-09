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
}

function fakeTerminal({
  type = "normal",
  line = 100,
  maxLine = 100,
}: {
  type?: "normal" | "alternate";
  line?: number;
  maxLine?: number;
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
      onBufferChange: changed.event,
    },
    onScroll: scroll.event,
    onWriteParsed: parsed.event,
    scrollToLine,
    scrollLines,
    scrollToTop,
    scrollToBottom,
  } as unknown as Terminal;
  return { term, scrollToLine, scrollLines, scrollToTop, scrollToBottom };
}

function RailHarness({ name, term }: { name: string; term: Terminal }) {
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

    // No history to scroll: the thumb honestly fills the whole track, and the
    // tooltip explains who owns the screen. One rail shape for every pane —
    // the owner-switching rail (grip, caps, strokes) is gone.
    expect(thumb.style.height).toBe("200px");
    expect(rail.title).toBe(
      "Scroll Ida: a full-screen app owns this pane right now",
    );
  });
});
