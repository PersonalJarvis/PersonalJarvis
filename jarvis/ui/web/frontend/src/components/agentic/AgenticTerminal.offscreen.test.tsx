/**
 * A pane nobody is looking at must not draw.
 *
 * Dozens of terminals share one browser main thread with the keyboard, so a
 * pane scrolled out of the grid — or hidden behind a maximized sibling — used
 * to spend real frames painting pixels nobody could see, at the direct expense
 * of the pane being typed into. These pin the contract: withhold while hidden,
 * replay in ONE write on return, in order.
 */
import { render } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const harness = vi.hoisted(() => ({
  writes: [] as string[],
  handlers: null as null | {
    onOutput: (text: string) => void;
    onExit: (code: number) => void;
    onReady: (info: { resumed: boolean; reattached: boolean }) => void;
    onTrouble: (message: string, retrying: boolean) => void;
  },
  observed: [] as Element[],
  fire: null as null | ((intersecting: boolean) => void),
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    options: Record<string, unknown> = {};
    unicode = { activeVersion: "" };
    // A pane takes over the terminal's protocol replies on mount, because the
    // backend answers those instead (see ./terminalQueries). A stand-in without
    // a parser is a terminal xterm never shipped.
    parser = {
      registerOscHandler: () => ({ dispose() {} }),
      registerCsiHandler: () => ({ dispose() {} }),
    };

    loadAddon() {}
    open() {}
    focus() {}
    paste() {}
    getSelection() {
      return "";
    }
    onData() {
      return { dispose() {} };
    }
    write(text: string) {
      harness.writes.push(text);
    }
    resize() {}
    dispose() {}
    clearTextureAtlas() {}
  },
}));

vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: class {} }));
vi.mock("@xterm/addon-canvas", () => ({ CanvasAddon: class {} }));
vi.mock("@xterm/addon-unicode11", () => ({ Unicode11Addon: class {} }));

vi.mock("./paneSocket", () => ({
  openPaneSocket: (_opts: unknown, handlers: typeof harness.handlers) => {
    harness.handlers = handlers;
    return { send() {}, close() {} };
  },
}));
vi.mock("./terminalPaste", () => ({
  installPasteBridge: () => () => undefined,
}));
vi.mock("./paneFileDrag", () => ({
  usePaneFileDrag: () => ({ dragging: false, handlers: {} }),
}));
vi.mock("@/lib/editActions", () => ({ attachTerminalBridge: () => undefined }));
vi.mock("@/lib/agenticIdeApi", () => ({ attachToTerminal: vi.fn() }));

import { AgenticTerminal } from "./AgenticTerminal";

class ResizeObserverHarness implements ResizeObserver {
  constructor(_callback: ResizeObserverCallback) {}
  observe() {}
  unobserve() {}
  disconnect() {}
}

class IntersectionObserverHarness {
  constructor(private readonly callback: IntersectionObserverCallback) {
    harness.fire = (intersecting: boolean) => {
      this.callback(
        harness.observed.map((target) => ({ target, isIntersecting: intersecting })) as
          unknown as IntersectionObserverEntry[],
        this as unknown as IntersectionObserver,
      );
    };
  }
  observe(target: Element) {
    harness.observed.push(target);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
  root = null;
  rootMargin = "";
  thresholds = [];
}

function mount() {
  return render(
    <AgenticTerminal
      name="Dana"
      displayName="Claude Code"
      appearance="dark"
      fontSize={13}
    />,
  );
}

describe("AgenticTerminal off-screen output", () => {
  beforeEach(() => {
    harness.writes = [];
    harness.handlers = null;
    harness.observed = [];
    harness.fire = null;
    globalThis.ResizeObserver = ResizeObserverHarness;
    globalThis.IntersectionObserver =
      IntersectionObserverHarness as unknown as typeof IntersectionObserver;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("writes straight through while the pane is on screen", () => {
    mount();
    act(() => harness.handlers?.onOutput("visible output"));
    expect(harness.writes).toContain("visible output");
  });

  it("withholds output while hidden and replays it in ONE write", () => {
    mount();
    act(() => harness.fire?.(false));

    act(() => {
      harness.handlers?.onOutput("frame one ");
      harness.handlers?.onOutput("frame two ");
      harness.handlers?.onOutput("frame three");
    });
    expect(harness.writes).toEqual([]);

    act(() => harness.fire?.(true));
    expect(harness.writes).toEqual(["frame one frame two frame three"]);
  });

  it("keeps the exit banner BEHIND the output it follows", () => {
    // Writing the banner straight to xterm while output is parked would put
    // "[exited]" above the lines that explain why.
    mount();
    act(() => harness.fire?.(false));
    act(() => {
      harness.handlers?.onOutput("the last thing it said");
      harness.handlers?.onExit(1);
    });
    act(() => harness.fire?.(true));

    const replayed = harness.writes.join("");
    expect(replayed.indexOf("the last thing it said")).toBeLessThan(
      replayed.indexOf("exited"),
    );
  });

  it("stays visible where IntersectionObserver does not exist", () => {
    // Old engines and bare test environments: the pane must keep drawing
    // rather than go permanently silent.
    // @ts-expect-error - deliberately removing the API
    delete globalThis.IntersectionObserver;
    mount();
    act(() => harness.handlers?.onOutput("still drawn"));
    expect(harness.writes).toContain("still drawn");
  });
});
