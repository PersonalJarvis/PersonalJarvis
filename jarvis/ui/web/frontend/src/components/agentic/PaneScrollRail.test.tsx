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
  /** Repaint every visible row, which is what a moved application looks like. */
  repaint: () => void;
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
  let generation = 0;
  const active = {
    type: owner === "terminal" ? "normal" : "alternate",
    baseY: maxLine,
    viewportY: line,
    getLine: (index: number) => ({
      translateToString: () => `row ${index} of paint ${generation}`,
    }),
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
  return {
    term,
    scrollToLine,
    scrollLines,
    input,
    setOwner,
    repaint: () => {
      generation += 1;
    },
  };
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

  it("strokes a mouse-aware full-screen IDE both ways and shows no position at all", () => {
    const harness = fakeTerminal({ owner: "mouse-app" });
    render(<RailHarness name="Aria" term={harness.term} />);
    const rail = giveTrackGeometry("Aria");
    const xtermScreen = screen.getByTestId("xterm-screen-Aria");
    const deltas: number[] = [];
    xtermScreen.addEventListener("wheel", (event) => deltas.push(event.deltaY));
    // The regression this pins: no shape in this track, in any state. Twice a
    // grip was drawn here and twice it was read as a position that was wrong.
    expect(screen.queryByTestId("pane-scroll-thumb-Aria")).toBeNull();
    expect(rail.dataset.scrollPosition).toBe("none");

    fireEvent.pointerDown(rail, { button: 0, clientY: 100, pointerId: 2 });
    // A press alone must move nothing: there is no position it could mean.
    expect(deltas).toEqual([]);
    fireEvent.pointerMove(rail, { clientY: 65, pointerId: 2 });
    fireEvent.pointerMove(rail, { clientY: 121, pointerId: 2 });
    fireEvent.pointerUp(rail, { clientY: 121, pointerId: 2 });

    expect(deltas.some((delta) => delta < 0)).toBe(true);
    expect(deltas.some((delta) => delta > 0)).toBe(true);
    expect(screen.queryByTestId("pane-scroll-thumb-Aria")).toBeNull();
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

  it("drags the application grip to stroke, and it springs back to centre", () => {
    const harness = fakeTerminal({ owner: "mouse-app" });
    render(<RailHarness name="Rex" term={harness.term} />);
    const rail = giveTrackGeometry("Rex");
    const grip = screen.getByTestId("pane-scroll-grip-Rex");
    const deltas: number[] = [];
    screen
      .getByTestId("xterm-screen-Rex")
      .addEventListener("wheel", (event) => deltas.push(event.deltaY));

    // At rest the grip sits dead centre — it carries no position.
    expect(grip.style.transform).toBe("translate(-50%, calc(-50% + 0px))");

    fireEvent.pointerDown(grip, { button: 0, clientY: 100, pointerId: 9 });
    fireEvent.pointerMove(rail, { clientY: 60, pointerId: 9 });

    // The drag strokes the application and the grip rides along with it.
    expect(deltas.length).toBeGreaterThan(0);
    expect(deltas.every((delta) => delta < 0)).toBe(true);
    expect(grip.style.transform).toBe("translate(-50%, calc(-50% + -40px))");

    fireEvent.pointerUp(rail, { clientY: 60, pointerId: 9 });

    // Released, it returns to centre: a handle that never stays put cannot be
    // read as "you are here". The thumb testid stays application-free.
    expect(grip.style.transform).toBe("translate(-50%, calc(-50% + 0px))");
    expect(screen.queryByTestId("pane-scroll-thumb-Rex")).toBeNull();
  });

  it("pages the application from the visible arrow caps", () => {
    const harness = fakeTerminal({ owner: "mouse-app" });
    render(<RailHarness name="Odo" term={harness.term} />);
    giveTrackGeometry("Odo");
    const deltas: number[] = [];
    screen
      .getByTestId("xterm-screen-Odo")
      .addEventListener("wheel", (event) =>
        deltas.push((event as WheelEvent).deltaY),
      );

    fireEvent.click(screen.getByRole("button", { name: "Scroll older in Odo" }));
    expect(deltas.length).toBeGreaterThan(0);
    expect(deltas.every((delta) => delta < 0)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Scroll newer in Odo" }));
    expect(deltas.some((delta) => delta > 0)).toBe(true);
  });

  it("says plainly why an application rail has no position, and stays visible", () => {
    const harness = fakeTerminal({ owner: "mouse-app" });
    render(<RailHarness name="Ida" term={harness.term} />);
    const rail = giveTrackGeometry("Ida");

    expect(rail.title).toBe(
      "Scroll Ida: this app keeps its own history, so there is no position to show",
    );
    // The regression this pins: the application rail once hid at rest, and an
    // invisible control next to a sibling pane's always-visible thumb was read
    // as "the scrollbar is broken in this CLI". Both rail kinds now share the
    // same resting visibility.
    expect(rail.className).not.toContain("opacity-0");
    expect(rail.className).toContain("opacity-65");
  });

  it("notices a mouse-tracking flip that arrives without any buffer event", () => {
    vi.useFakeTimers({
      toFake: [
        "setInterval",
        "clearInterval",
        "requestAnimationFrame",
        "cancelAnimationFrame",
      ],
    });
    try {
      const harness = fakeTerminal({ owner: "terminal" });
      render(<RailHarness name="Vera" term={harness.term} />);
      const rail = screen.getByTestId("pane-scroll-rail-Vera");
      expect(rail.dataset.scrollMode).toBe("terminal");

      // A replayed session consumes the TUI's DECSET before the rail
      // subscribes, so the flip reaches xterm without any later event.
      (
        harness.term.modes as { mouseTrackingMode: string }
      ).mouseTrackingMode = "any";
      act(() => {
        vi.advanceTimersByTime(1600);
      });

      expect(rail.dataset.scrollMode).toBe("application");
    } finally {
      vi.useRealTimers();
    }
  });

  it("drops the exact thumb when a TUI takes the screen during a drag", () => {
    vi.useFakeTimers({
      toFake: [
        "setInterval",
        "clearInterval",
        "requestAnimationFrame",
        "cancelAnimationFrame",
      ],
    });
    try {
      const harness = fakeTerminal({ owner: "terminal", line: 50 });
      render(<RailHarness name="Iris" term={harness.term} />);
      const rail = giveTrackGeometry("Iris");
      const thumb = screen.getByTestId("pane-scroll-thumb-Iris");
      expect(rail.dataset.scrollMode).toBe("terminal");
      const deltas: number[] = [];
      screen
        .getByTestId("xterm-screen-Iris")
        .addEventListener("wheel", (event) => deltas.push(event.deltaY));

      fireEvent.pointerDown(thumb, { button: 0, clientY: 100, pointerId: 5 });
      harness.setOwner("mouse-app");
      fireEvent.pointerMove(rail, { clientY: 60, pointerId: 5 });
      fireEvent.pointerUp(rail, { clientY: 60, pointerId: 5 });
      act(() => {
        vi.advanceTimersByTime(50);
      });

      // The gesture continues as a stroke on the application that took over,
      // and the thumb it started on is gone rather than left pointing at a line
      // the pane no longer has.
      expect(deltas.length).toBeGreaterThan(0);
      expect(deltas.every((delta) => delta < 0)).toBe(true);
      expect(screen.queryByTestId("pane-scroll-thumb-Iris")).toBeNull();
      expect(rail.dataset.scrollMode).toBe("application");
    } finally {
      vi.useRealTimers();
    }
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
