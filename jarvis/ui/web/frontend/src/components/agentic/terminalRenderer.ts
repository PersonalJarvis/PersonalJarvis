/**
 * Which engine draws a terminal pane's glyphs — and what happens when it dies.
 *
 * WebGL first. A coding agent's output SCROLLS, and a scroll moves every row
 * of the viewport, so each new line is a full-screen repaint. The canvas
 * renderer does that repaint on the main thread, one `drawImage` + `clip` per
 * cell run; the WebGL renderer uploads the cell data and draws the whole
 * screen in one GPU call. Measured on 2026-09-28 with eight panes each
 * scrolling a line every 120 ms (the shape of eight busy agents): the canvas
 * renderer held one core at 88 % and fell to ~38 fps, WebGL used 8 % at a
 * steady 60 fps. The same app profile showed the canvas path as the single
 * largest cost in the IDE view (`drawImage`, `clip`, `save`/`restore`) — on
 * a machine already saturated by the agents themselves, that is the work that
 * turned into multi-second frames, panes painting black, and half-drawn rows.
 *
 * Canvas stays as the fallback, and is never worse than what shipped before:
 *
 * - no WebGL at all (a headless box, a blocklisted GPU, a driver that refuses
 *   the context) — the addon throws on load and the pane goes straight to
 *   canvas;
 * - too many contexts — a page may only hold so many live WebGL contexts
 *   before the browser silently kills the oldest, and a 3D scene elsewhere in
 *   the app needs its own. Past {@link MAX_WEBGL_PANES} a new pane takes
 *   canvas instead of evicting someone else's context;
 * - context LOST later (a GPU reset, a driver update, VRAM exhausted by a
 *   local model) — the addon is disposed and canvas takes over in place.
 *   Lost is terminal for that pane's WebGL: it does not try again, because a
 *   GPU that just dropped a context is exactly the one that would drop it
 *   again (AP-32: survive the loss, release what was held).
 *
 * One more trap, and it is the reason every glyph-cache clear in a pane goes
 * through {@link clearTerminalTextureAtlas}: the WebGL addon SHARES its glyph
 * atlas between every terminal with the same font and theme. A clear on one
 * pane empties that shared atlas but only forgets the glyph positions of the
 * pane that asked; every other WebGL pane keeps drawing its unchanged rows
 * from coordinates that now point at other glyphs. Seen 2026-09-28: splitting
 * a new pane (whose mount restates theme and size, each with a clear) turned
 * the idle rows of three neighbouring panes into glyph soup.
 *
 * And the atlas can reshuffle itself without anyone asking. Once it holds the
 * maximum number of pages it MERGES four of them into one and shifts every
 * later page down an index. Each pane keeps its own GPU copy of every page and
 * decides whether to re-upload by comparing a per-INDEX version number — but
 * after the shift, index i holds a different page whose version can equal the
 * one the pane uploaded for the old page there. The pane then keeps sampling
 * the old texture with the new coordinates: overprinted, half-legible rows.
 * Maximizing and restoring a pane is what fills the atlas fastest (a whole
 * window of new glyphs, then every hidden neighbour repainting at once), which
 * is how it was reported (2026-09-28). Measured in a six-pane harness: 8 merges
 * left 6 panes drawing from a stale page; with {@link forgetUploadedAtlasPages}
 * on every merge, none. So a merge makes every WebGL pane re-upload its pages.
 */

import { CanvasAddon } from "@xterm/addon-canvas";
import { WebglAddon } from "@xterm/addon-webgl";
import type { ITerminalAddon, Terminal } from "@xterm/xterm";

/**
 * How many panes may draw with WebGL at once.
 *
 * Chromium keeps 16 live WebGL contexts per page and evicts the oldest past
 * that. Twelve leaves room for the app's own 3D views without either side
 * losing a context to the other; a workspace holds at most eight panes, so
 * this only bites when several workspaces keep their panes mounted.
 */
export const MAX_WEBGL_PANES = 12;

export type TerminalRendererKind = "webgl" | "canvas" | "dom";

/** The addon surface this module needs — narrowed so tests can hand in fakes. */
export interface WebglLike extends ITerminalAddon {
  onContextLoss: (listener: () => void) => { dispose(): void };
  /** Fires for each page a shared-atlas merge removes (see the module comment). */
  onRemoveTextureAtlasCanvas?: (listener: () => void) => { dispose(): void };
}

export interface RendererDeps {
  createWebgl: () => WebglLike;
  createCanvas: () => ITerminalAddon;
}

const realDeps: RendererDeps = {
  createWebgl: () => new WebglAddon(),
  createCanvas: () => new CanvasAddon(),
};

let webglPanes = 0;

/** One repaint per frame after an atlas merge, however many panes report it. */
let repaintScheduled = false;

/** Terminals currently drawing with WebGL — the ones sharing a glyph atlas. */
const webglTerminals = new Set<Terminal>();

/** Test hook: forget every pane counted so far. */
export function resetWebglPaneCount(): void {
  webglPanes = 0;
  repaintScheduled = false;
  webglTerminals.clear();
}

/**
 * Drop `term`'s cached glyphs — and, when it draws with WebGL, make every other
 * WebGL pane forget its glyph positions too, since the atlas they point into
 * was just emptied under them (see the module comment). Each pane re-rasterizes
 * on its next frame; this only runs on a theme, font or size change.
 */
export function clearTerminalTextureAtlas(term: Terminal): void {
  term.clearTextureAtlas?.();
  if (!webglTerminals.has(term)) return;
  for (const other of webglTerminals) {
    if (other !== term) other.clearTextureAtlas?.();
  }
}

/**
 * The slice of the WebGL addon's internals that holds a pane's GPU copies of
 * the atlas pages. Not public API: every step is optional, so a future addon
 * that renames any of it degrades to doing nothing rather than throwing.
 */
interface GlyphRendererInternals {
  _core?: {
    _renderService?: {
      _renderer?: {
        value?: { _glyphRenderer?: { value?: { _atlasTextures?: Array<{ version: number }> } } };
      };
    };
  };
}

/**
 * Make `term` re-upload every atlas page on its next frame — what the addon
 * itself does when a pane switches atlas (`setAtlas`).
 */
export function forgetUploadedAtlasPages(term: Terminal): void {
  const textures = (term as unknown as GlyphRendererInternals)._core?._renderService?._renderer
    ?.value?._glyphRenderer?.value?._atlasTextures;
  if (!Array.isArray(textures)) return;
  for (const texture of textures) texture.version = -1;
}


/** The slice of the WebGL renderer that draws a frame. Not public API either. */
interface WebglRendererInternals {
  _core?: {
    _renderService?: {
      _renderer?: {
        value?: {
          _gl?: WebGL2RenderingContext;
          renderRows?: (start: number, end: number) => void;
          __clearsEveryFrame?: boolean;
        };
      };
    };
  };
}

/**
 * Make `term`'s WebGL renderer empty its canvas before it draws each frame.
 *
 * The addon never calls `gl.clear`: it "clears" by painting one rectangle the
 * size of the screen in the theme's background colour, then draws every row
 * over it. A pane's background is transparent (alpha 0, so the glass shell
 * behind it shows through), and with alpha blending that rectangle paints
 * nothing — the previous frame stays in the drawing buffer wherever the
 * browser has not wiped it. It does not wipe a canvas it never presented, and
 * a pane rebuilding behind its curtain (`visibility: hidden` after a reload
 * or a workspace switch) is exactly that: the replayed screen, the reset, the
 * agent's repaint were drawn on top of each other, and the pane was revealed
 * with two readable texts in the same cells and a cursor left at the top-left
 * corner (2026-09-29). Every frame redraws the whole model, so a real clear
 * first loses nothing.
 */
export function clearEveryFrame(term: Terminal): void {
  const renderer = (term as unknown as WebglRendererInternals)._core?._renderService?._renderer
    ?.value;
  const gl = renderer?._gl;
  const renderRows = renderer?.renderRows;
  if (!renderer || !gl || typeof renderRows !== "function" || renderer.__clearsEveryFrame) {
    return;
  }
  renderer.__clearsEveryFrame = true;
  renderer.renderRows = function (this: unknown, start: number, end: number) {
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    renderRows.call(this, start, end);
  };
}

/**
 * An atlas merge just shifted the shared pages: every WebGL pane re-uploads
 * them. Synchronous for the invalidation — the merge happens inside one pane's
 * frame, before that pane uploads — and one repaint per animation frame for
 * everyone else, however many pages and panes reported the same merge.
 */
function handleAtlasMerge(): void {
  for (const term of webglTerminals) forgetUploadedAtlasPages(term);
  if (repaintScheduled) return;
  repaintScheduled = true;
  const repaint = () => {
    repaintScheduled = false;
    for (const term of webglTerminals) term.refresh?.(0, Math.max(0, term.rows - 1));
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(repaint);
  else repaint();
}

export interface AttachedRenderer {
  /** What is drawing right now — changes from "webgl" to "canvas" on a loss. */
  readonly kind: TerminalRendererKind;
  /** Release the renderer's slot. The terminal's own dispose frees the addon. */
  dispose(): void;
}

/**
 * Give `term` the fastest renderer it can keep. Call after `term.open()`.
 *
 * `onFallback` hears about a WebGL pane that had to fall back later, so the
 * caller can repaint — a pane that swaps renderers mid-life starts from an
 * empty surface.
 */
export function attachTerminalRenderer(
  term: Terminal,
  onFallback?: () => void,
  deps: RendererDeps = realDeps,
): AttachedRenderer {
  let kind: TerminalRendererKind = "dom";
  let counted = false;
  let released = false;

  const release = () => {
    if (!counted) return;
    counted = false;
    webglPanes = Math.max(0, webglPanes - 1);
    webglTerminals.delete(term);
  };

  const loadCanvas = (): TerminalRendererKind => {
    try {
      term.loadAddon(deps.createCanvas());
      return "canvas";
    } catch {
      // No 2D canvas either: xterm keeps its DOM renderer, which draws
      // correctly, just more slowly. Nothing to report — it still works.
      return "dom";
    }
  };

  if (webglPanes < MAX_WEBGL_PANES) {
    let webgl: WebglLike | null = null;
    try {
      webgl = deps.createWebgl();
      term.loadAddon(webgl);
      clearEveryFrame(term);
      webglPanes += 1;
      webglTerminals.add(term);
      counted = true;
      kind = "webgl";
    } catch {
      // No usable WebGL here — canvas is the answer, and that is not an error.
      try {
        webgl?.dispose();
      } catch {
        /* a half-activated addon has nothing left worth releasing */
      }
      webgl = null;
    }
    if (webgl) {
      const mergeSubscription = webgl.onRemoveTextureAtlasCanvas?.(handleAtlasMerge);
      const lossSubscription = webgl.onContextLoss(() => {
        lossSubscription.dispose();
        mergeSubscription?.dispose();
        release();
        try {
          webgl?.dispose();
        } catch {
          /* the context is already gone; dispose only tidies listeners */
        }
        webgl = null;
        if (released) return;
        kind = loadCanvas();
        onFallback?.();
      });
    }
  }
  if (kind === "dom") kind = loadCanvas();

  return {
    get kind() {
      return kind;
    },
    dispose() {
      released = true;
      release();
    },
  };
}
