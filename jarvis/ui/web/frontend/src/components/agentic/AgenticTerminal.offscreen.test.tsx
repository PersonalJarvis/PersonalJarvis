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

/**
 * Recorded in `writes` rather than counted separately, because the ORDER is the
 * contract: a reset after the replay wipes the screen it was supposed to draw.
 *
 * Hoisted because `vi.mock` factories run before the module body.
 */
const RESET = vi.hoisted(() => "<reset>");

const harness = vi.hoisted(() => ({
  writes: [] as string[],
  handlers: null as null | {
    onOutput: (text: string) => void;
    onReplay: (text: string) => void;
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
    // The pane claims keystrokes on mount (paste, and the newline chord in
    // ./terminalNewline) — a stand-in without these is a terminal xterm never
    // shipped either.
    attachCustomKeyEventHandler() {}
    input() {}
    getSelection() {
      return "";
    }
    onData() {
      return { dispose() {} };
    }
    write(text: string) {
      harness.writes.push(text);
    }
    reset() {
      harness.writes.push(RESET);
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

  it("writes a flooded hidden pane out rather than losing its frame", () => {
    // The bytes that DREW the agent's interface sit at the FRONT of the
    // stream, and an Ink-based TUI never repaints it unprompted. A hidden pane
    // that overflows must therefore hand what it holds to xterm — discarding
    // the front is what left panes showing a spinner row over empty space
    // (2026-07-27). Costly? One parse nobody paints. Correct? Always.
    mount();
    act(() => harness.fire?.(false));

    const opening = "THE PROMPT BOX";
    const flood = "z".repeat(16 * 1024);
    act(() => {
      harness.handlers?.onOutput(opening);
      for (let i = 0; i < 40; i += 1) harness.handlers?.onOutput(flood);
    });
    act(() => harness.fire?.(true));

    const replayed = harness.writes.join("");
    expect(replayed.startsWith(opening)).toBe(true);
    expect(replayed).toHaveLength(opening.length + 40 * flood.length);
  });

  it("clears the screen before drawing a re-joined one", () => {
    // The pane has been running; then the socket drops and comes back, and the
    // server hands over the bytes that drew the screen the agent is looking at.
    // Written onto what is still there, they draw that interface a SECOND time
    // over the copy already on screen — and because an Ink TUI skips unchanged
    // cells with cursor moves rather than overwriting them, the two copies
    // interleave character by character instead of stacking (2026-07-29).
    mount();
    act(() => harness.handlers?.onOutput("the screen as it was"));
    harness.writes = [];

    act(() => harness.handlers?.onReplay("the screen as it is"));

    expect(harness.writes).toEqual([RESET, "the screen as it is"]);
  });

  it("drops output parked before a replay rather than painting it after", () => {
    // Parked output was captured from the screen the replay REPLACES. Written
    // afterwards it paints the older screen over the newer one; the replay is
    // the whole truth by the time it arrives.
    mount();
    act(() => harness.fire?.(false));
    act(() => harness.handlers?.onOutput("older than the replay"));
    expect(harness.writes).toEqual([]);

    act(() => harness.handlers?.onReplay("the current screen"));
    act(() => harness.fire?.(true));

    const drawn = harness.writes.join("");
    expect(drawn).not.toContain("older than the replay");
    expect(drawn).toContain("the current screen");
    // Still reset first, even though the write itself waited for the pane to
    // come back — a replay parked and un-parked is still a rebuild.
    expect(harness.writes[0]).toBe(RESET);
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
    // Anchored on the banner's SHAPE — "[<pane name> …" — rather than on the
    // wording of the explanation inside it. This assertion is about ordering,
    // and it spent a while red because it matched the word "exited" that
    // `explainExit` has since reworded to "stopped".
    expect(replayed.indexOf("the last thing it said")).toBeLessThan(
      replayed.indexOf("[Claude Code"),
    );
    expect(replayed).toContain("[Claude Code");
  });

  it("does not park while the whole document is hidden", () => {
    // The window behind an editor, minimized, or a background tab: every
    // element reports intersecting nothing, and showing the window again moves
    // no geometry, so no further callback ever arrives to undo it. Parking on
    // that verdict is what left freshly spawned panes black — for minutes on a
    // chatty CLI, and indefinitely on one that printed its prompt and went
    // quiet (2026-07-27).
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    mount();
    act(() => harness.fire?.(false));

    act(() => harness.handlers?.onOutput("the agent's opening screen"));
    expect(harness.writes).toContain("the agent's opening screen");
    hidden.mockRestore();
  });

  it("replays a parked pane when the window comes back", () => {
    // Parked while the document was still visible (a pane genuinely scrolled
    // out of the grid), then the app went behind another window and returned.
    // No geometry changed across that, so the observer says nothing and the
    // pane has to answer for itself.
    mount();
    act(() => harness.fire?.(false));
    act(() => harness.handlers?.onOutput("held while away"));
    expect(harness.writes).toEqual([]);

    // On screen by measurement — jsdom reports a zero box otherwise, which
    // would (correctly) read as "no area, still off screen".
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      top: 10, bottom: 400, left: 10, right: 400, width: 390, height: 390,
      x: 10, y: 10, toJSON: () => ({}),
    } as DOMRect);

    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(harness.writes).toEqual(["held while away"]);
  });

  it("un-parks itself when output arrives and the pane is on screen", () => {
    // The backstop for every state the observer never reports its way out of.
    // Two were found one live incident at a time (a hidden document, a pane
    // that came back at the size it left), and the symptom of the next one
    // would be identical: an agent working behind an empty rectangle while the
    // user is told the prompt was handed over. A parked pane that is still
    // being written to measures itself instead of waiting for a callback.
    mount();
    act(() => harness.fire?.(false));

    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      top: 10, bottom: 400, left: 10, right: 400, width: 390, height: 390,
      x: 10, y: 10, toJSON: () => ({}),
    } as DOMRect);

    act(() => harness.handlers?.onOutput("the prompt Jarvis just typed"));
    expect(harness.writes.join("")).toContain("the prompt Jarvis just typed");
  });

  it("keeps withholding from a pane that is genuinely off screen", () => {
    // The measurement must not become a way back for a pane scrolled out of a
    // tall grid — that pane is exactly what parking exists for.
    mount();
    act(() => harness.fire?.(false));

    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      top: 4000, bottom: 4400, left: 10, right: 400, width: 390, height: 390,
      x: 10, y: 4000, toJSON: () => ({}),
    } as DOMRect);

    act(() => harness.handlers?.onOutput("nobody is looking at this"));
    expect(harness.writes).toEqual([]);
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
