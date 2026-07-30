import type { Terminal } from "@xterm/xterm";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  bindTerminalScrollSurface,
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

describe("terminalScrollSurface", () => {
  it("uses the real xterm parser to distinguish scrollback from a TUI", async () => {
    const term = await newTerminal();
    const host = document.createElement("div");
    const dispose = bindTerminalScrollSurface(host, term);
    try {
      expect(terminalScrollOwner(term)).toBe("terminal");
      expect(host.dataset.scrollOwner).toBe("terminal");

      await write(
        term,
        "\x1b[?1049h\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h",
      );

      expect(term.buffer.active.type).toBe("alternate");
      expect(term.modes.mouseTrackingMode).toBe("any");
      expect(host.dataset.scrollOwner).toBe("application");

      await write(
        term,
        "\x1b[?1006l\x1b[?1003l\x1b[?1002l\x1b[?1000l\x1b[?1049l",
      );

      expect(term.buffer.active.type).toBe("normal");
      expect(term.modes.mouseTrackingMode).toBe("none");
      expect(host.dataset.scrollOwner).toBe("terminal");
    } finally {
      dispose();
      term.dispose();
    }
  });

  it("lets the terminal receive a wheel but contains the workspace scroll", async () => {
    const parent = document.createElement("div");
    const host = document.createElement("div");
    const xterm = document.createElement("div");
    parent.append(host);
    host.append(xterm);
    const term = await newTerminal();
    const xtermWheel = vi.fn();
    const workspaceWheel = vi.fn();
    xterm.addEventListener("wheel", xtermWheel);
    parent.addEventListener("wheel", workspaceWheel);
    const dispose = bindTerminalScrollSurface(host, term);
    try {
      const accepted = xterm.dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true,
          cancelable: true,
          deltaY: 120,
        }),
      );

      expect(xtermWheel).toHaveBeenCalledOnce();
      expect(workspaceWheel).not.toHaveBeenCalled();
      expect(accepted).toBe(false);
    } finally {
      dispose();
      term.dispose();
    }
  });
});
