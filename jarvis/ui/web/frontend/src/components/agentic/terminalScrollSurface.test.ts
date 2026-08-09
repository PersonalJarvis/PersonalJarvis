import type { Terminal } from "@xterm/xterm";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  bindTerminalScrollRegion,
  captureWheelForTerminalHistory,
  forwardWheelToTerminal,
  lineAtThumbTop,
  readTerminalScrollView,
  scrollThumbGeometry,
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
  { baseY = 0, viewportY = 0 } = {},
): Terminal & { scrollLines: ReturnType<typeof vi.fn> } {
  return {
    rows: 24,
    modes: { mouseTrackingMode: tracking },
    buffer: { active: { type, baseY, viewportY } },
    scrollLines: vi.fn(),
  } as unknown as Terminal & { scrollLines: ReturnType<typeof vi.fn> };
}

function wheel(init: WheelEventInit): WheelEvent {
  return new WheelEvent("wheel", { deltaMode: WheelEvent.DOM_DELTA_PIXEL, ...init });
}

describe("terminalScrollSurface", () => {
  it("reads the exact xterm position for every pane the same way", () => {
    const term = terminalDouble("none", "normal", { baseY: 80, viewportY: 40 });
    const view = readTerminalScrollView(term);

    expect(view).toEqual({ rows: 24, maxLine: 80, line: 40, altScreen: false });
    expect(scrollThumbGeometry(view, 208)).toEqual({ top: 80, height: 48 });
    expect(lineAtThumbTop(view, 0, 208)).toBe(0);
    expect(lineAtThumbTop(view, 80, 208)).toBe(40);
    expect(lineAtThumbTop(view, 160, 208)).toBe(80);
  });

  it("fills the whole track before any history exists — never a fake position", () => {
    const empty = readTerminalScrollView(terminalDouble("none", "normal"));
    expect(empty.maxLine).toBe(0);
    expect(scrollThumbGeometry(empty, 200)).toEqual({ top: 0, height: 200 });

    const alt = readTerminalScrollView(terminalDouble("any", "alternate"));
    expect(alt.altScreen).toBe(true);
    expect(scrollThumbGeometry(alt, 200)).toEqual({ top: 0, height: 200 });
  });

  it("keeps the wheel on xterm history while a normal-buffer CLI tracks the mouse", () => {
    const term = terminalDouble("any", "normal", { baseY: 300, viewportY: 300 });
    const handler = captureWheelForTerminalHistory(term);

    // 120 wheel pixels = three rows, handled here — never a mouse report.
    expect(handler(wheel({ deltaY: 120 }))).toBe(false);
    expect(term.scrollLines).toHaveBeenCalledWith(3);

    // Sub-row trackpad deltas accumulate instead of being rounded away.
    term.scrollLines.mockClear();
    expect(handler(wheel({ deltaY: -25 }))).toBe(false);
    expect(term.scrollLines).not.toHaveBeenCalled();
    expect(handler(wheel({ deltaY: -25 }))).toBe(false);
    expect(term.scrollLines).toHaveBeenCalledWith(-1);

    // Line-mode wheels scroll whole lines 1:1.
    term.scrollLines.mockClear();
    expect(
      handler(wheel({ deltaY: 2, deltaMode: WheelEvent.DOM_DELTA_LINE })),
    ).toBe(false);
    expect(term.scrollLines).toHaveBeenCalledWith(2);
  });

  it("stays native wherever xterm's default behaviour is already right", () => {
    // No tracking: xterm scrolls its own viewport without help.
    const plain = terminalDouble("none", "normal");
    expect(captureWheelForTerminalHistory(plain)(wheel({ deltaY: 120 }))).toBe(
      true,
    );
    expect(plain.scrollLines).not.toHaveBeenCalled();

    // Alternate screen: the app owns the screen and keeps its protocols.
    const alt = terminalDouble("any", "alternate");
    expect(captureWheelForTerminalHistory(alt)(wheel({ deltaY: 120 }))).toBe(
      true,
    );
    expect(alt.scrollLines).not.toHaveBeenCalled();

    // Modifier chords and horizontal gestures stay native too.
    const tracked = terminalDouble("any", "normal");
    const handler = captureWheelForTerminalHistory(tracked);
    expect(handler(wheel({ deltaY: 120, shiftKey: true }))).toBe(true);
    expect(handler(wheel({ deltaY: 120, ctrlKey: true }))).toBe(true);
    expect(handler(wheel({ deltaY: 10, deltaX: 50 }))).toBe(true);
    expect(tracked.scrollLines).not.toHaveBeenCalled();
  });

  it("recognises tracking negotiated through the real xterm parser", async () => {
    const term = await newTerminal();
    const scrollLines = vi.spyOn(term, "scrollLines");
    const handler = captureWheelForTerminalHistory(term);
    try {
      expect(handler(wheel({ deltaY: 120 }))).toBe(true);

      await write(term, "\x1b[?1000h\x1b[?1006h");
      expect(handler(wheel({ deltaY: 120 }))).toBe(false);
      expect(scrollLines).toHaveBeenCalledWith(3);

      await write(term, "\x1b[?1006l\x1b[?1000l");
      expect(handler(wheel({ deltaY: 120 }))).toBe(true);
    } finally {
      term.dispose();
    }
  });

  it("forwards a rail wheel to the xterm screen unchanged", () => {
    const host = document.createElement("div");
    const screen = document.createElement("div");
    screen.className = "xterm-screen";
    host.append(screen);
    const seen: WheelEvent[] = [];
    screen.addEventListener("wheel", (event) => seen.push(event));

    forwardWheelToTerminal(
      host,
      wheel({ deltaY: 120, deltaX: 5, shiftKey: true }),
    );

    expect(seen).toHaveLength(1);
    expect(seen[0].deltaY).toBe(120);
    expect(seen[0].deltaX).toBe(5);
    expect(seen[0].shiftKey).toBe(true);
  });

  it("contains wheel input inside the terminal region", () => {
    const region = document.createElement("div");
    const parentSaw = vi.fn();
    document.body.append(region);
    document.body.addEventListener("wheel", parentSaw);
    const unbind = bindTerminalScrollRegion(region);

    region.dispatchEvent(
      new WheelEvent("wheel", { deltaY: 120, bubbles: true, cancelable: true }),
    );
    expect(parentSaw).not.toHaveBeenCalled();

    unbind();
    document.body.removeEventListener("wheel", parentSaw);
    region.remove();
  });
});
