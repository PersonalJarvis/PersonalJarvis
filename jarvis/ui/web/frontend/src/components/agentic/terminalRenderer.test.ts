import { afterEach, describe, expect, it } from "vitest";
import type { ITerminalAddon, Terminal } from "@xterm/xterm";

import {
  MAX_WEBGL_PANES,
  attachTerminalRenderer,
  clearTerminalTextureAtlas,
  forgetUploadedAtlasPages,
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
  private mergeListeners: Array<() => void> = [];
  onRemoveTextureAtlasCanvas(listener: () => void) {
    this.mergeListeners.push(listener);
    return { dispose: () => {} };
  }
  mergeAtlasPages() {
    for (const l of [...this.mergeListeners]) l();
  }
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

describe("clearTerminalTextureAtlas", () => {
  function clearingTerminal() {
    const t = fakeTerminal();
    let clears = 0;
    Object.assign(t.term, {
      clearTextureAtlas: () => {
        clears += 1;
      },
    });
    return { ...t, clears: () => clears };
  }

  it("makes every other WebGL pane forget the shared atlas it points into", () => {
    const a = clearingTerminal();
    const b = clearingTerminal();
    attachTerminalRenderer(a.term, undefined, deps());
    attachTerminalRenderer(b.term, undefined, deps());
    clearTerminalTextureAtlas(a.term);
    expect(a.clears()).toBe(1);
    expect(b.clears()).toBe(1);
  });

  it("leaves canvas and released panes alone", () => {
    const a = clearingTerminal();
    const b = clearingTerminal();
    const c = clearingTerminal();
    const d = deps();
    attachTerminalRenderer(a.term, undefined, d);
    const rb = attachTerminalRenderer(b.term, undefined, d);
    const refusing = { ...d, createWebgl: () => { throw new Error("no context"); } };
    attachTerminalRenderer(c.term, undefined, refusing);
    rb.dispose();
    clearTerminalTextureAtlas(a.term);
    expect(b.clears()).toBe(0);
    expect(c.clears()).toBe(0);
    clearTerminalTextureAtlas(c.term);
    expect(a.clears()).toBe(1);
  });
});

/** A WebGL-drawn terminal exposing the GPU page copies the addon keeps. */
function glTerminal(pages: number) {
  const textures = Array.from({ length: pages }, (_, i) => ({ version: i + 1 }));
  const refreshed: Array<[number, number]> = [];
  const term = {
    rows: 24,
    loadAddon() {},
    refresh(start: number, end: number) {
      refreshed.push([start, end]);
    },
    _core: { _renderService: { _renderer: { value: { _glyphRenderer: { value: { _atlasTextures: textures } } } } } },
  } as unknown as Terminal;
  return { term, textures, refreshed };
}

describe("an atlas page merge", () => {
  it("makes every WebGL pane re-upload its pages and repaint", async () => {
    const d = deps();
    const a = glTerminal(3);
    const b = glTerminal(3);
    attachTerminalRenderer(a.term, undefined, d);
    attachTerminalRenderer(b.term, undefined, d);

    d.webgls[0].mergeAtlasPages();
    d.webgls[1].mergeAtlasPages();

    // Invalidated synchronously: the merging pane uploads in the same frame.
    expect(a.textures.every((t) => t.version === -1)).toBe(true);
    expect(b.textures.every((t) => t.version === -1)).toBe(true);
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
    // One repaint each, however many panes reported the merge.
    expect(a.refreshed).toEqual([[0, 23]]);
    expect(b.refreshed).toEqual([[0, 23]]);
  });

  it("leaves a terminal without the addon's internals alone", () => {
    const { term } = fakeTerminal();
    expect(() => forgetUploadedAtlasPages(term)).not.toThrow();
  });
});
