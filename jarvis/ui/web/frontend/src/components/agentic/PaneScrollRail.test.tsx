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
  input: ReturnType<typeof vi.fn>;
  setOwner: (owner: "terminal" | "mouse-app" | "page-app") => void;
}

function fakeTerminal({
  owner,
  line = 100,
  maxLine = 100,
}: {
  owner: "terminal" | "mouse-app" | "page-app";
  line?: number;
  maxLine?: number;
}): TerminalHarness {
  const scroll = signal<number>();
  const parsed = signal<void>();
  const changed = signal<unknown>();
  const active = {
    type: owner === "terminal" ? "normal" : "alternate",
    baseY: maxLine,
    viewportY: line,
  };
  const modes = {
    mouseTrackingMode: owner === "mouse-app" ? "any" : "none",
  };
  const scrollToLine = vi.fn((next: number) => {
    active.viewportY = next;
    scroll.fire(next);
  });
  const scrollLines = vi.fn((amount: number) => {
    active.viewportY = Math.max(0, Math.min(active.baseY, active.viewportY + amount));
    scroll.fire(active.viewportY);
  });
  const input = vi.fn();
  const term = {
    rows: 20,
    modes,
    buffer: {
      active,
      onBufferChange: changed.event,
    },
    onScroll: scroll.event,
    onWriteParsed: parsed.event,
    scrollToLine,
    scrollLines,
    scrollToTop: vi.fn(() => scrollToLine(0)),
    scrollToBottom: vi.fn(() => scrollToLine(active.baseY)),
    input,
  } as unknown as Terminal;
  const setOwner = (next: "terminal" | "mouse-app" | "page-app") => {
    active.type = next === "terminal" ? "normal" : "alternate";
    modes.mouseTrackingMode = next === "mouse-app" ? "any" : "none";
    changed.fire(active);
  };
  return { term, scrollToLine, scrollLines, input, setOwner };
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
    const mika = fakeTerminal({ owner: "terminal" });
    const nova = fakeTerminal({ owner: "terminal" });
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
    expect(screen.getByTestId("pane-scroll-rail-Mika").dataset.scrollMode).toBe(
      "terminal",
    );
    expect(
      screen
        .getByRole("scrollbar", { name: "Scroll Mika" })
        .getAttribute("aria-controls"),
    ).toBe("terminal-Mika");
  });

  it("moves a mouse-aware full-screen IDE both up and down without a fake position", () => {
    const harness = fakeTerminal({ owner: "mouse-app" });
    render(<RailHarness name="Aria" term={harness.term} />);
    const rail = giveTrackGeometry("Aria");
    const thumb = screen.getByTestId("pane-scroll-thumb-Aria");
    const xtermScreen = screen.getByTestId("xterm-screen-Aria");
    const deltas: number[] = [];
    xtermScreen.addEventListener("wheel", (event) => deltas.push(event.deltaY));
    const restingTop = thumb.style.top;

    fireEvent.pointerDown(thumb, { button: 0, clientY: 100, pointerId: 2 });
    fireEvent.pointerMove(rail, { clientY: 65, pointerId: 2 });
    fireEvent.pointerMove(rail, { clientY: 121, pointerId: 2 });
    fireEvent.pointerUp(rail, { clientY: 121, pointerId: 2 });

    expect(deltas.some((delta) => delta < 0)).toBe(true);
    expect(deltas.some((delta) => delta > 0)).toBe(true);
    expect(thumb.style.top).toBe(restingTop);
    expect(rail.dataset.scrollMode).toBe("application");
    expect(rail.hasAttribute("aria-valuemin")).toBe(false);
    expect(rail.hasAttribute("aria-valuemax")).toBe(false);
    expect(rail.hasAttribute("aria-valuenow")).toBe(false);
    expect(rail.hasAttribute("aria-valuetext")).toBe(false);
    expect(
      screen
        .getByRole("button", { name: "Scroll older in Aria" })
        .getAttribute("aria-controls"),
    ).toBe("terminal-Aria");
    expect(
      screen
        .getByRole("button", { name: "Scroll newer in Aria" })
        .getAttribute("aria-controls"),
    ).toBe("terminal-Aria");
  });

  it("restores the current owner geometry when a TUI starts during a drag", () => {
    const harness = fakeTerminal({ owner: "terminal", line: 50 });
    render(<RailHarness name="Iris" term={harness.term} />);
    const rail = giveTrackGeometry("Iris");
    const thumb = screen.getByTestId("pane-scroll-thumb-Iris");

    fireEvent.pointerDown(thumb, { button: 0, clientY: 100, pointerId: 5 });
    harness.setOwner("mouse-app");
    fireEvent.pointerMove(rail, { clientY: 40, pointerId: 5 });
    fireEvent.pointerUp(rail, { clientY: 40, pointerId: 5 });

    expect(thumb.style.top).toBe("82px");
  });

  it("cancels an uncaptured drag when the pointer leaves the rail", () => {
    const harness = fakeTerminal({ owner: "terminal", line: 50 });
    render(<RailHarness name="Lumi" term={harness.term} />);
    const rail = giveTrackGeometry("Lumi");
    const thumb = screen.getByTestId("pane-scroll-thumb-Lumi");

    fireEvent.pointerDown(thumb, { button: 0, clientY: 100, pointerId: 6 });
    expect(rail.className).toContain("cursor-grabbing");
    fireEvent.pointerLeave(rail, { pointerId: 6 });
    expect(rail.className).not.toContain("cursor-grabbing");
  });

  it("uses PageUp and PageDown for an alternate-screen IDE without mouse mode", () => {
    const harness = fakeTerminal({ owner: "page-app" });
    render(<RailHarness name="Sora" term={harness.term} />);
    const rail = giveTrackGeometry("Sora");

    fireEvent.keyDown(rail, { key: "PageUp" });
    fireEvent.keyDown(rail, { key: "PageDown" });

    expect(harness.input.mock.calls.map((call) => call[0])).toEqual([
      "\x1b[5~",
      "\x1b[6~",
    ]);
  });

  it("forwards a wheel over the rail unchanged to its own terminal", () => {
    const harness = fakeTerminal({ owner: "mouse-app" });
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
});
