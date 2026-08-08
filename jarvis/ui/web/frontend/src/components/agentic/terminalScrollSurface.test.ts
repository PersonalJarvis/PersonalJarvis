import type { Terminal } from "@xterm/xterm";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  applicationPageNotches,
  bindTerminalScrollRegion,
  createLiveTuiWheelHandler,
  forwardWheelToTerminal,
  installLiveTuiCursorTracking,
  lineAtThumbTop,
  readTerminalScrollView,
  scrollApplication,
  scrollLiveCursor,
  scrollThumbGeometry,
  strokeUnits,
  terminalScrollOwner,
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

  it("tracks hidden-cursor live views through the real xterm parser", async () => {
    const term = await newTerminal();
    const states: boolean[] = [];
    const tracking = installLiveTuiCursorTracking(term.parser, (active) =>
      states.push(active),
    );
    try {
      await write(term, "\x1b[?25l");
      await write(term, "\x1b[?25l");
      expect(states).toEqual([true]);

      tracking.reset();
      expect(states).toEqual([true, false]);
      await write(term, "\x1b[?25l");
      await write(term, "\x1b[?25h");
      expect(states).toEqual([true, false, true, false]);

      await write(term, "\x1b[?25l");
      await write(term, "\x1b[!p");
      expect(states).toEqual([true, false, true, false, true, false]);

      await write(term, "\x1b[?25l");
      await write(term, "\x1bc");
      expect(states).toEqual([
        true,
        false,
        true,
        false,
        true,
        false,
        true,
        false,
      ]);
    } finally {
      tracking.dispose();
      term.dispose();
    }
  });

  it("offers NO thumb geometry for an application-owned screen", () => {
    // The regression this pins: twice a shape was drawn in this track — once
    // parked in the middle, once at a measured offset — and both times the
    // maintainer read it as "you are here" and both times it was wrong. A zero
    // height is the instruction to draw nothing at all.
    const view = readTerminalScrollView(terminalDouble("any", "alternate"));

    expect(view).toEqual({ owner: "application", rows: 24, maxLine: 0, line: 0 });
    expect(scrollThumbGeometry(view, 200)).toEqual({ top: 0, height: 0 });
    expect(scrollThumbGeometry(view, 900)).toEqual({ top: 0, height: 0 });
  });

  it("converts a stroke into whole relay units and carries the remainder", () => {
    // 3px per unit: five units and 2px left over for the next pointer event,
    // so a slow drag accumulates instead of being rounded away to nothing.
    expect(strokeUnits(17)).toEqual({ units: 5, consumedPx: 15 });
    expect(strokeUnits(-17)).toEqual({ units: 5, consumedPx: -15 });
    expect(strokeUnits(2)).toEqual({ units: 0, consumedPx: 0 });
    expect(strokeUnits(0)).toEqual({ units: 0, consumedPx: 0 });
    expect(strokeUnits(50, 25)).toEqual({ units: 2, consumedPx: 50 });
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

  it("reveals all eight rows after explicit live-view activation", () => {
    const term = terminalDouble("none", "normal");
    const agents = Array.from(
      { length: 8 },
      (_, index) => `agent-${index + 1}`,
    );
    let selected = 4;
    term.input.mockImplementation((data: string) => {
      for (const sequence of data.matchAll(/\x1b\[([AB])/g)) {
        selected = Math.max(
          0,
          Math.min(
            agents.length - 1,
            selected + (sequence[1] === "B" ? 1 : -1),
          ),
        );
      }
    });
    const visible = () => {
      const start = Math.min(Math.max(0, selected - 4), agents.length - 5);
      return agents.slice(start, start + 5);
    };
    const handleWheel = createLiveTuiWheelHandler(term, () => true);

    expect(visible()).toEqual(agents.slice(0, 5));

    expect(
      handleWheel(
        new WheelEvent("wheel", {
          deltaMode: WheelEvent.DOM_DELTA_PIXEL,
          deltaY: 120,
        }),
      ),
    ).toBe(false);
    expect(term.input).toHaveBeenCalledWith("\x1b[B".repeat(3), true);
    expect(visible()).toEqual(agents.slice(3, 8));

    expect(handleWheel(new WheelEvent("wheel", { deltaY: -40 }))).toBe(false);
    expect(term.input).toHaveBeenLastCalledWith("\x1b[A", true);
  });

  it("keeps both wheel directions native without explicit activation", () => {
    const term = terminalDouble("none", "normal");
    const handleWheel = createLiveTuiWheelHandler(term, () => false);

    expect(handleWheel(new WheelEvent("wheel", { deltaY: 120 }))).toBe(true);
    expect(handleWheel(new WheelEvent("wheel", { deltaY: -120 }))).toBe(true);
    expect(term.input).not.toHaveBeenCalled();
  });

  it("keeps Shift+wheel as access to exact xterm history", () => {
    const term = terminalDouble("none", "normal");
    const handleWheel = createLiveTuiWheelHandler(term, () => true);

    expect(
      handleWheel(new WheelEvent("wheel", { deltaY: -120, shiftKey: true })),
    ).toBe(true);
    expect(term.input).not.toHaveBeenCalled();
  });

  it("leaves negotiated normal-buffer mouse tracking untouched", () => {
    const term = terminalDouble("any", "normal");
    Object.assign(term.modes, { applicationCursorKeysMode: true });
    const handleWheel = createLiveTuiWheelHandler(term, () => true);

    expect(
      handleWheel(
        new WheelEvent("wheel", {
          deltaMode: WheelEvent.DOM_DELTA_LINE,
          deltaY: -2,
        }),
      ),
    ).toBe(true);
    expect(term.input).not.toHaveBeenCalled();
  });

  it("leaves alternate-screen mouse protocols untouched", () => {
    const term = terminalDouble("any", "alternate");
    const handleWheel = createLiveTuiWheelHandler(term, () => true);

    expect(handleWheel(new WheelEvent("wheel", { deltaY: 120 }))).toBe(true);
    expect(term.input).not.toHaveBeenCalled();
  });

  it("accumulates fine trackpad input without moving two histories", () => {
    const term = terminalDouble("none", "normal");
    const handleWheel = createLiveTuiWheelHandler(term, () => true);

    expect(handleWheel(new WheelEvent("wheel", { deltaY: 20 }))).toBe(false);
    expect(term.input).not.toHaveBeenCalled();
    expect(handleWheel(new WheelEvent("wheel", { deltaY: 20 }))).toBe(false);
    expect(term.input).toHaveBeenCalledWith("\x1b[B", true);
  });

  it("emits no more cursor rows than one bounded wheel action", () => {
    const term = terminalDouble("none", "normal");

    expect(scrollLiveCursor(term, 1, 100)).toBe(12);
    expect(term.input).toHaveBeenCalledOnce();
    expect(term.input).toHaveBeenCalledWith("\x1b[B".repeat(12), true);
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
