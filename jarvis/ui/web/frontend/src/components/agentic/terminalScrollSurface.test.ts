import type { Terminal } from "@xterm/xterm";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  applicationOffsetAtThumbTop,
  applicationPageNotches,
  ApplicationScrollTracker,
  bindTerminalScrollRegion,
  forwardWheelToTerminal,
  lineAtThumbTop,
  readTerminalScrollView,
  screenMoved,
  screenSignature,
  scrollApplication,
  scrollThumbGeometry,
  terminalScrollOwner,
  wheelNotches,
} from "./terminalScrollSurface";

function write(term: Terminal, data: string): Promise<void> {
  return new Promise((resolve) => term.write(data, resolve));
}

let restoreCanvasContext: (() => void) | undefined;

beforeAll(() => {
  // xterm probes canvas colour support at module load. Parsing terminal modes
  // needs no renderer, so a null context is the faithful jsdom capability.
  const canvasContext = vi
    .spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockImplementation(() => null);
  restoreCanvasContext = () => canvasContext.mockRestore();
});

afterAll(() => restoreCanvasContext?.());

async function newTerminal(): Promise<Terminal> {
  const { Terminal: XtermTerminal } = await import("@xterm/xterm");
  return new XtermTerminal({ allowProposedApi: true });
}

function terminalDouble(
  tracking: "none" | "any",
  type: "normal" | "alternate",
): Terminal & { input: ReturnType<typeof vi.fn> } {
  return {
    rows: 24,
    modes: { mouseTrackingMode: tracking },
    buffer: { active: { type, baseY: 0, viewportY: 0 } },
    input: vi.fn(),
  } as unknown as Terminal & { input: ReturnType<typeof vi.fn> };
}

describe("terminalScrollSurface", () => {
  it("uses the real xterm parser to distinguish scrollback from every TUI mode", async () => {
    const term = await newTerminal();
    try {
      expect(terminalScrollOwner(term)).toBe("terminal");

      await write(
        term,
        "\x1b[?1049h\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h",
      );
      expect(term.buffer.active.type).toBe("alternate");
      expect(term.modes.mouseTrackingMode).toBe("any");
      expect(terminalScrollOwner(term)).toBe("application");

      await write(
        term,
        "\x1b[?1006l\x1b[?1003l\x1b[?1002l\x1b[?1000l\x1b[?1049l",
      );
      expect(terminalScrollOwner(term)).toBe("terminal");
    } finally {
      term.dispose();
    }
  });

  it("maps a normal buffer to an exact conventional thumb", () => {
    const term = {
      rows: 20,
      modes: { mouseTrackingMode: "none" },
      buffer: { active: { type: "normal", baseY: 80, viewportY: 40 } },
    } as unknown as Terminal;
    const view = readTerminalScrollView(term);

    expect(view).toEqual({ owner: "terminal", rows: 20, maxLine: 80, line: 40 });
    expect(scrollThumbGeometry(view, 200)).toEqual({ top: 80, height: 40 });
    expect(lineAtThumbTop(view, 0, 200)).toBe(0);
    expect(lineAtThumbTop(view, 80, 200)).toBe(40);
    expect(lineAtThumbTop(view, 160, 200)).toBe(80);
  });

  it("centres an application controller instead of inventing a position", () => {
    const term = terminalDouble("any", "alternate");
    const view = readTerminalScrollView(term);
    const thumb = scrollThumbGeometry(view, 200);

    expect(view).toEqual({ owner: "application", rows: 24, maxLine: 0, line: 0 });
    expect(thumb.height).toBe(36);
    expect(thumb.top).toBe(82);
  });

  it("draws a measured application position at both ends of the rail", () => {
    const view = readTerminalScrollView(terminalDouble("any", "alternate"));
    const tracker = new ApplicationScrollTracker();

    // Nothing measured: the grip rests in the middle and says it is not a
    // position, which is the only honest answer before the first scroll.
    expect(scrollThumbGeometry(view, 200).top).toBe(82);

    // Ten units of older, answered by a repaint.
    tracker.advance(-1, 10);
    tracker.settle(-1, 10, true);
    const moving = tracker.estimate();
    expect(moving.calibrated).toBe(false);
    expect(scrollThumbGeometry(view, 200, { estimate: moving }).top).toBeCloseTo(
      164 * (1 - 10 / 40),
      5,
    );

    // Ten more the application does not answer: that is the oldest end, and it
    // fixes the scale at the ten units that did move.
    tracker.advance(-1, 10);
    tracker.settle(-1, 10, false);
    const top = tracker.estimate();
    expect(top).toMatchObject({ offset: 10, span: 10, calibrated: true, atTop: true });
    expect(scrollThumbGeometry(view, 200, { estimate: top }).top).toBe(0);

    // Back down until it stops answering again: the newest end, not the middle.
    tracker.advance(1, 4);
    tracker.settle(1, 4, false);
    const bottom = tracker.estimate();
    expect(bottom).toMatchObject({ offset: 0, span: 10, atBottom: true });
    expect(scrollThumbGeometry(view, 200, { estimate: bottom }).top).toBe(164);
  });

  it("maps a dragged application grip back to the offset it points at", () => {
    const tracker = new ApplicationScrollTracker();
    tracker.advance(-1, 60);
    tracker.settle(-1, 60, true);
    tracker.advance(-1, 10);
    tracker.settle(-1, 10, false);
    const estimate = tracker.estimate();

    expect(estimate.span).toBe(60);
    expect(applicationOffsetAtThumbTop(estimate, 0, 200)).toBe(60);
    expect(applicationOffsetAtThumbTop(estimate, 164, 200)).toBe(0);
    expect(applicationOffsetAtThumbTop(estimate, 82, 200)).toBe(30);
    // Past either end of the track still means that end.
    expect(applicationOffsetAtThumbTop(estimate, -50, 200)).toBe(60);
    expect(applicationOffsetAtThumbTop(estimate, 900, 200)).toBe(0);
  });

  it("tells a ticking status line apart from a screen that actually moved", () => {
    const before = Array.from({ length: 24 }, (_, row) => `row ${row}`);
    const ticked = [...before];
    ticked[23] = "esc to interrupt · 12s";

    expect(screenMoved(before, ticked)).toBe(false);
    expect(screenMoved(before, before.map((row) => `${row} scrolled`))).toBe(true);
    expect(screenMoved([], [])).toBe(false);
  });

  it("reads the visible rows of the current viewport as the screen signature", () => {
    const term = {
      rows: 3,
      modes: { mouseTrackingMode: "none" },
      buffer: {
        active: {
          type: "normal",
          baseY: 5,
          viewportY: 2,
          getLine: (index: number) => ({
            translateToString: () => `line ${index}`,
          }),
        },
      },
    } as unknown as Terminal;

    expect(screenSignature(term)).toEqual(["line 2", "line 3", "line 4"]);
  });

  it("counts a wheel in the whole rows xterm reports to the application", () => {
    const host = document.createElement("div");
    const screen = document.createElement("div");
    screen.className = "xterm-screen";
    screen.getBoundingClientRect = () => ({ height: 480 }) as DOMRect;
    host.append(screen);
    const term = terminalDouble("any", "alternate");

    const line = new WheelEvent("wheel", {
      deltaMode: WheelEvent.DOM_DELTA_LINE,
      deltaY: -3,
    });
    const pixels = new WheelEvent("wheel", {
      deltaMode: WheelEvent.DOM_DELTA_PIXEL,
      deltaY: 100,
    });
    const still = new WheelEvent("wheel", { deltaY: 0 });

    expect(wheelNotches(term, host, line)).toBe(3);
    // 480px over 24 rows is a 20px cell, so 100px is five rows.
    expect(wheelNotches(term, host, pixels)).toBe(5);
    expect(wheelNotches(term, host, still)).toBe(0);
  });

  it("relays standard wheel reports through xterm for a mouse-aware coding TUI", () => {
    const host = document.createElement("div");
    const screen = document.createElement("div");
    screen.className = "xterm-screen";
    host.append(screen);
    const term = terminalDouble("any", "alternate");
    const deltas: number[] = [];
    screen.addEventListener("wheel", (event) => deltas.push(event.deltaY));

    expect(scrollApplication(term, host, -1, 3)).toBe(3);
    expect(scrollApplication(term, host, 1, 2)).toBe(2);

    expect(deltas).toEqual([-1, -1, -1, 1, 1]);
    expect(term.input).not.toHaveBeenCalled();
  });

  it("falls back to standard page keys for an alternate screen without mouse mode", () => {
    const term = terminalDouble("none", "alternate");
    const page = applicationPageNotches(term.rows);

    expect(scrollApplication(term, null, -1, page)).toBe(1);
    expect(scrollApplication(term, null, 1, page * 2)).toBe(2);
    expect(term.input.mock.calls.map((call) => call[0])).toEqual([
      "\x1b[5~",
      "\x1b[6~",
      "\x1b[6~",
    ]);
  });

  it("forwards the wheel over a rail unchanged and contains the workspace", () => {
    const workspace = document.createElement("div");
    const region = document.createElement("div");
    const host = document.createElement("div");
    const xtermScreen = document.createElement("div");
    xtermScreen.className = "xterm-screen";
    workspace.append(region);
    region.append(host);
    host.append(xtermScreen);
    const terminalWheel = vi.fn();
    const workspaceWheel = vi.fn();
    xtermScreen.addEventListener("wheel", terminalWheel);
    workspace.addEventListener("wheel", workspaceWheel);
    const dispose = bindTerminalScrollRegion(region);
    try {
      const source = new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        deltaMode: WheelEvent.DOM_DELTA_PIXEL,
        deltaX: 4,
        deltaY: 120,
        shiftKey: true,
      });

      expect(forwardWheelToTerminal(host, source)).toBe(true);
      expect(terminalWheel).toHaveBeenCalledOnce();
      const forwarded = terminalWheel.mock.calls[0][0] as WheelEvent;
      expect(forwarded.deltaX).toBe(4);
      expect(forwarded.deltaY).toBe(120);
      expect(forwarded.deltaMode).toBe(WheelEvent.DOM_DELTA_PIXEL);
      expect(forwarded.shiftKey).toBe(true);
      expect(workspaceWheel).not.toHaveBeenCalled();
    } finally {
      dispose();
    }
  });
});
