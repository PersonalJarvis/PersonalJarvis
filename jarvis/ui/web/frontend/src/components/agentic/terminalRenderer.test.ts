import { afterEach, describe, expect, it } from "vitest";
import type { ITerminalAddon, Terminal } from "@xterm/xterm";

import {
  MAX_WEBGL_PANES,
  attachTerminalRenderer,
  resetWebglPaneCount,
  type RendererDeps,
  type WebglLike,
} from "./terminalRenderer";

/** A terminal that only records which addons it was handed. */
function fakeTerminal(opts: { refuse?: (addon: ITerminalAddon) => boolean } = {}) {
  const loaded: ITerminalAddon[] = [];
  const term = {
    loadAddon(addon: ITerminalAddon) {
      if (opts.refuse?.(addon)) throw new Error("no context");
      loaded.push(addon);
    },
  } as unknown as Terminal;
  return { term, loaded };
}

class FakeWebgl implements WebglLike {
  disposed = false;
  private listeners: Array<() => void> = [];
  activate() {}
  dispose() {
    this.disposed = true;
  }
  onContextLoss(listener: () => void) {
    this.listeners.push(listener);
    return {
      dispose: () => {
        this.listeners = this.listeners.filter((l) => l !== listener);
      },
    };
  }
  loseContext() {
    for (const l of [...this.listeners]) l();
  }
}

class FakeCanvas implements ITerminalAddon {
  activate() {}
  dispose() {}
}

function deps(): RendererDeps & { webgls: FakeWebgl[] } {
  const webgls: FakeWebgl[] = [];
  return {
    webgls,
    createWebgl: () => {
      const w = new FakeWebgl();
      webgls.push(w);
      return w;
    },
    createCanvas: () => new FakeCanvas(),
  };
}

afterEach(() => resetWebglPaneCount());

describe("attachTerminalRenderer", () => {
  it("draws with WebGL when the page can give it a context", () => {
    const { term, loaded } = fakeTerminal();
    const r = attachTerminalRenderer(term, undefined, deps());
    expect(r.kind).toBe("webgl");
    expect(loaded[0]).toBeInstanceOf(FakeWebgl);
  });

  it("falls back to canvas where WebGL cannot load", () => {
    const { term, loaded } = fakeTerminal({ refuse: (a) => a instanceof FakeWebgl });
    const r = attachTerminalRenderer(term, undefined, deps());
    expect(r.kind).toBe("canvas");
    expect(loaded).toHaveLength(1);
    expect(loaded[0]).toBeInstanceOf(FakeCanvas);
  });

  it("switches a pane to canvas in place when its context is lost", () => {
    const d = deps();
    const { term, loaded } = fakeTerminal();
    let repainted = 0;
    const r = attachTerminalRenderer(term, () => repainted++, d);
    d.webgls[0].loseContext();
    expect(d.webgls[0].disposed).toBe(true);
    expect(r.kind).toBe("canvas");
    expect(loaded.at(-1)).toBeInstanceOf(FakeCanvas);
    // The fallback surface starts empty — the caller is told to repaint.
    expect(repainted).toBe(1);
  });

  it("stops taking contexts past the cap instead of evicting another pane's", () => {
    const d = deps();
    const kinds = Array.from({ length: MAX_WEBGL_PANES + 2 }, () =>
      attachTerminalRenderer(fakeTerminal().term, undefined, d).kind,
    );
    expect(kinds.filter((k) => k === "webgl")).toHaveLength(MAX_WEBGL_PANES);
    expect(kinds.slice(-2)).toEqual(["canvas", "canvas"]);
  });

  it("gives the slot back when a pane goes away or loses its context", () => {
    const d = deps();
    const panes = Array.from({ length: MAX_WEBGL_PANES }, () =>
      attachTerminalRenderer(fakeTerminal().term, undefined, d),
    );
    panes[0].dispose();
    d.webgls[1].loseContext();
    const next = [
      attachTerminalRenderer(fakeTerminal().term, undefined, d).kind,
      attachTerminalRenderer(fakeTerminal().term, undefined, d).kind,
      attachTerminalRenderer(fakeTerminal().term, undefined, d).kind,
    ];
    expect(next).toEqual(["webgl", "webgl", "canvas"]);
  });
});
