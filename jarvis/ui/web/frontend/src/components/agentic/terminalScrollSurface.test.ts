import type { Terminal } from "@xterm/xterm";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  bindTerminalScrollRegion,
  captureWheelForTerminalHistory,
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
  { baseY = 0, viewportY = 0, normalBaseY = 0 } = {},
): Terminal & { scrollLines: ReturnType<typeof vi.fn> } {
  return {
    rows: 24,
    modes: { mouseTrackingMode: tracking },
    buffer: {
      active: { type, baseY, viewportY },
      normal: { baseY: normalBaseY, viewportY: normalBaseY },
    },
    scrollLines: vi.fn(),
  } as unknown as Terminal & { scrollLines: ReturnType<typeof vi.fn> };
}

function wheel(init: WheelEventInit): WheelEvent {
  return new WheelEvent("wheel", { deltaMode: WheelEvent.DOM_DELTA_PIXEL, ...init });
}

describe("terminalScrollSurface", () => {
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

  it("contains wheel input inside the terminal region", () => {
    const region = document.createElement("div");
    const parentSaw = vi.fn();
    document.body.append(region);
    document.body.addEventListener("wheel", parentSaw);
    const unbind = bindTerminalScrollRegion(region);

    const event = new WheelEvent("wheel", {
      deltaY: 120,
      bubbles: true,
      cancelable: true,
    });
    region.dispatchEvent(event);
    expect(parentSaw).not.toHaveBeenCalled();
    // Nothing in there scrolls on its own, so the notch is cancelled too.
    expect(event.defaultPrevented).toBe(true);

    unbind();
    document.body.removeEventListener("wheel", parentSaw);
    region.remove();
  });

  /**
   * The prompt receipt is drawn INSIDE the terminal region — it has to be, it
   * points at the pane it is talking about — and a long delivered prompt
   * scrolls within it. Cancelling every wheel in the region cancelled that one
   * as well, so the card could show that it had more text and could not be read
   * past it.
   */
  describe("overlays drawn inside the region", () => {
    /** A box that really has somewhere to scroll, as jsdom does not lay out. */
    function scrollBox({ scrollTop = 0 } = {}): HTMLElement {
      const box = document.createElement("div");
      box.style.overflowY = "auto";
      Object.defineProperties(box, {
        scrollHeight: { value: 400, configurable: true },
        clientHeight: { value: 200, configurable: true },
        scrollTop: { value: scrollTop, writable: true, configurable: true },
      });
      return box;
    }

    /** Region → overlay → the element the pointer is actually over. */
    function paneWith(overlay: HTMLElement): {
      region: HTMLElement;
      target: HTMLElement;
      unbind: () => void;
    } {
      const region = document.createElement("div");
      const target = document.createElement("span");
      overlay.append(target);
      region.append(overlay);
      document.body.append(region);
      return { region, target, unbind: bindTerminalScrollRegion(region) };
    }

    function wheelOn(target: HTMLElement, deltaY: number): WheelEvent {
      const event = new WheelEvent("wheel", {
        deltaY,
        bubbles: true,
        cancelable: true,
      });
      target.dispatchEvent(event);
      return event;
    }

    it("leaves the notch to a scrollable card that still has room", () => {
      const { region, target, unbind } = paneWith(scrollBox());
      const parentSaw = vi.fn();
      document.body.addEventListener("wheel", parentSaw);

      const event = wheelOn(target, 120);
      expect(event.defaultPrevented).toBe(false);
      // Still contained: the workspace behind the pane must not move either way.
      expect(parentSaw).not.toHaveBeenCalled();

      unbind();
      document.body.removeEventListener("wheel", parentSaw);
      region.remove();
    });

    it("takes it back once that card has reached the end it is heading for", () => {
      // Parked at the top: there is room downwards and none upwards, and only
      // the second of those may fall through to a scroll chain.
      const { region, target, unbind } = paneWith(scrollBox({ scrollTop: 0 }));

      expect(wheelOn(target, 120).defaultPrevented).toBe(false);
      expect(wheelOn(target, -120).defaultPrevented).toBe(true);

      unbind();
      region.remove();
    });

    it("never hands a wheel inside the terminal back to the browser", () => {
      // xterm's viewport is a real scroller, and it is answered by xterm's own
      // handlers — a parent listener must not add native scrolling on top.
      const surface = document.createElement("div");
      surface.className = "xterm";
      const viewport = scrollBox({ scrollTop: 80 });
      viewport.className = "xterm-viewport";
      surface.append(viewport);

      const region = document.createElement("div");
      region.append(surface);
      document.body.append(region);
      const unbind = bindTerminalScrollRegion(region);

      expect(wheelOn(viewport, 120).defaultPrevented).toBe(true);

      unbind();
      region.remove();
    });
  });
});
