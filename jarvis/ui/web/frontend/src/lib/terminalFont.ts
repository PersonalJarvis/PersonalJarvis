/**
 * Keep xterm's cell grid in step with the font it actually draws with.
 *
 * A terminal is a grid: xterm measures ONE glyph, calls that the cell width,
 * and lays out every column from it. Measure it before the display font has
 * loaded and the whole grid is built on the fallback's metrics — at 13px,
 * Consolas advances 7.15px where JetBrains Mono advances 7.80px, a 9% error.
 * Every glyph is then painted 0.65px wider than the cell it owns, so nine
 * characters in, the text sits a full column off its grid. That is the smeared,
 * overlapping terminal output reported in the browser on 2026-07-28: measured
 * live in Chrome, all six panes had a 7px cell while JetBrains Mono was loaded
 * and drawing at 7.80px, and each pane had told its pty ~102 columns where only
 * ~94 fit.
 *
 * Two things make this easy to get wrong, and both were:
 *
 * * **`document.fonts.ready` does not mean "the terminal font is here."** It
 *   resolves once nothing is PENDING, and a font no element has rendered yet
 *   was never requested, so it is not pending. Measured in Chrome on the live
 *   app: `document.fonts.status === "loaded"` while
 *   `document.fonts.check('400 13px "JetBrains Mono"') === false`. Waiting on
 *   it therefore re-fits at a moment that proves nothing — and, worse, retires
 *   the one chance to fix the grid. The font has to be asked for BY NAME.
 * * **A re-fit does not re-measure.** xterm re-measures only when `fontFamily`
 *   or `fontSize` CHANGE (`CharSizeService` subscribes to exactly those two),
 *   and its options setter ignores a write of the identical string. `fit()`
 *   reads the cached, wrong cell width and faithfully recomputes the same wrong
 *   grid, so clearing the texture atlas and re-fitting cannot repair this on
 *   its own.
 *
 * Hence: ask for the font by name, then WATCH — the only signal that cannot lie
 * is the measurement itself, so a font that arrives late (slow CDN, an @import
 * whose stylesheet had not been parsed when it was first asked for) is still
 * caught. Offline, where the font never arrives, the fallback IS the font being
 * drawn, the measurement never changes, and nothing happens.
 */

/**
 * The one monospace stack every terminal in the app draws with.
 *
 * Shared rather than repeated per component so that the measurement here and
 * the glyphs on screen can never describe different fonts.
 */
export const TERMINAL_FONT_STACK =
  "'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, 'Courier New', monospace";

/** The display font at the head of the stack — the one that arrives late. */
const DISPLAY_FAMILY = '"JetBrains Mono"';

/** The weights xterm paints with: body text, and the bold spans a TUI uses. */
const WEIGHTS = [400, 700] as const;

/** Measured over a run of glyphs so per-character rounding cannot dominate. */
const SAMPLE = "W".repeat(32);

/**
 * Sub-pixel noise floor. Well under the ~0.65px error this exists to catch, and
 * comfortably above the jitter of repeating the same measurement.
 */
const ADVANCE_EPSILON = 0.05;

/**
 * Appended to force a re-measure, and harmless: the stack already ends in
 * `monospace`, so a second one changes the STRING without changing which font
 * resolves. Toggled rather than appended forever so repeated syncs stay stable.
 */
const REMEASURE_SUFFIX = ", monospace";

/**
 * The slice of xterm's API this needs — kept tiny so tests need no terminal.
 * The fields are optional because xterm declares them so; in practice every
 * terminal in this app sets font family and size at construction.
 */
export interface TerminalFontTarget {
  options: { fontFamily?: string; fontSize?: number; letterSpacing?: number };
}

/**
 * xterm's own default size, used only when a terminal left `fontSize` unset.
 * The comparison here is a BEFORE/AFTER delta at one fixed size, and advance
 * width scales linearly with it, so any consistent yardstick detects the same
 * change — this never has to match what is on screen.
 */
const FALLBACK_FONT_SIZE = 15;

export interface TerminalFontDeps {
  /** Advance width, in px, of one cell in `stack` at `fontSize`. */
  measureAdvance: (fontSize: number, stack: string) => number | null;
  /** The document's font set; `null` where the API does not exist. */
  fonts: FontFaceSet | null;
  /** Device pixel ratio; the canvas renderer rounds in device pixels. */
  devicePixelRatio: number;
}

function currentDpr(): number {
  if (typeof window === "undefined") return 1;
  return window.devicePixelRatio || 1;
}

/**
 * Give each cell back the sliver of a pixel xterm's canvas renderer floors away.
 *
 * The canvas renderer builds its grid on `Math.floor(advance * dpr)` device
 * pixels per cell, but keeps drawing the font's real, unfloored glyphs into it.
 * JetBrains Mono at 13px advances 7.80px, so at dpr 1 every cell is 7px wide and
 * every glyph overhangs its own cell by 0.8px — a tenth of a character, painted
 * over its neighbour, compounding across the line into the smeared text this
 * whole module exists to stop. The overhang is invisible on a font whose advance
 * happens to land on a whole pixel, which is why the fallback font looked fine.
 *
 * `letterSpacing` is added to the cell width in whole device pixels, so handing
 * back the floored fraction as one pixel of spacing makes the cell at least as
 * wide as the glyph it holds. Integral by construction: a fractional value would
 * be rounded away by the renderer and, worse, would land box-drawing characters
 * and the text beneath them on different sub-pixel offsets.
 *
 * Returns whether anything changed — a caller that re-fits and resizes its pty
 * should do so only then.
 */
export function alignTerminalCells(
  term: TerminalFontTarget,
  deps: Partial<TerminalFontDeps> = {},
): boolean {
  const measure = deps.measureAdvance ?? measureAdvance;
  const dpr = deps.devicePixelRatio ?? currentDpr();
  // Nothing here is worth a pane for. This module only makes glyphs sit
  // straight, so anything it does not recognise — a stubbed terminal, an engine
  // that exposes its options differently — leaves the terminal exactly as it is
  // rather than throwing out of a mount effect and taking the pane with it.
  const options = term?.options;
  if (!options) return false;
  const size = options.fontSize ?? FALLBACK_FONT_SIZE;
  const advance = measure(size, options.fontFamily || TERMINAL_FONT_STACK);
  // Unmeasurable (jsdom, no 2D context): leave the terminal exactly as it is.
  if (advance === null) return false;

  const device = advance * dpr;
  // The epsilon keeps a hair over a whole pixel (7.0000001) from being treated
  // as an overhang and buying a pixel of spacing nothing needed.
  const overhangs = device - Math.floor(device) > ADVANCE_EPSILON;
  const wanted = overhangs ? 1 : 0;
  if ((options.letterSpacing ?? 0) === wanted) return false;
  options.letterSpacing = wanted;
  return true;
}

/**
 * One cell's advance width, or `null` where it cannot be measured — jsdom has
 * no 2D context, and an environment that cannot measure must not be told the
 * font changed.
 */
export function measureAdvance(
  fontSize: number,
  stack: string = TERMINAL_FONT_STACK,
): number | null {
  if (typeof document === "undefined") return null;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = document.createElement("canvas").getContext?.("2d") ?? null;
  } catch {
    return null;
  }
  if (!ctx) return null;
  ctx.font = `${fontSize}px ${stack}`;
  const width = ctx.measureText(SAMPLE).width;
  if (!Number.isFinite(width) || width <= 0) return null;
  return width / SAMPLE.length;
}

function browserFonts(): FontFaceSet | null {
  if (typeof document === "undefined") return null;
  return document.fonts ?? null;
}

/**
 * Ask for the terminal's display font, align the cell grid to it, and keep both
 * honest for as long as the pane lives.
 *
 * `onChanged` runs after the terminal has been adjusted and fires only on a REAL
 * change, so the caller can do the expensive half — invalidate the glyph atlas,
 * re-fit, and tell the pty its new size — without rate-limiting. It fires
 * synchronously during this call if the grid needed aligning at mount, which is
 * the ordinary case: the floored cell is wrong from the first frame, whether or
 * not a font arrives later.
 *
 * Returns a dispose function; calling it stops all watching.
 */
export function syncTerminalFont(
  term: TerminalFontTarget,
  onChanged: () => void,
  deps: Partial<TerminalFontDeps> = {},
): () => void {
  const measure = deps.measureAdvance ?? measureAdvance;
  const fonts = deps.fonts !== undefined ? deps.fonts : browserFonts();

  let disposed = false;
  let cleanup = () => {};
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    cleanup();
  };

  // Align to the font that is on screen RIGHT NOW, before anything is awaited:
  // the overhang does not need a late font to appear, only a font whose advance
  // is not a whole number of device pixels.
  if (alignTerminalCells(term, deps)) onChanged();

  // A terminal whose options this cannot read is one to leave alone, not one to
  // throw over — this runs inside a mount effect, where an exception costs the
  // whole pane. See the same guard in `alignTerminalCells`.
  const options = term?.options;
  if (!options) return dispose;

  // No font API to wait on: whatever is measured now is the font that will be
  // drawn, and the grid above is already aligned to it.
  if (!fonts) return dispose;

  // The size the baseline was taken at, held separately: the user can change
  // the font size while the font is still in flight, and comparing the new
  // size's advance against the old size's would report a change that is only
  // the zoom the caller already handled.
  const baseSize = options.fontSize ?? FALLBACK_FONT_SIZE;
  const stackOf = () => options.fontFamily || TERMINAL_FONT_STACK;
  let baseline = measure(baseSize, stackOf());
  // Nothing measurable here (jsdom, a headless canvas-less runtime). Watching
  // would compare null to null forever.
  if (baseline === null) return dispose;

  // The stack as this pane was built with it. Re-measures alternate between it
  // and a padded copy, so the string always differs from the one xterm holds
  // (its setter drops an identical write, and that write is the re-measure)
  // without the padding accumulating across a long-lived pane.
  const originalStack = stackOf();
  let remeasures = 0;

  const settle = () => {
    if (disposed) return;
    const now = measure(baseSize, stackOf());
    if (now === null || baseline === null) return;
    if (Math.abs(now - baseline) < ADVANCE_EPSILON) return;
    baseline = now;
    // Force the re-measure xterm will not do on its own — see the file header.
    // Both forms keep the generic fallback the original stack ends with, so a
    // terminal still waiting on every named font always has one to fall back to.
    remeasures += 1;
    options.fontFamily =
      remeasures % 2 === 1 ? originalStack + REMEASURE_SUFFIX : originalStack;
    // The new font has a new advance, so the cell has a new fraction to give
    // back. Order matters: align AFTER the re-measure, never before.
    alignTerminalCells(term, deps);
    onChanged();
  };

  // Ask for the font BY NAME — the request `document.fonts.ready` was waiting
  // for and would never see, because until something renders in it, nobody has
  // asked. A rejection is ordinary (offline, or the @font-face rule has not
  // been parsed yet); the listener below covers the late arrival either way.
  for (const weight of WEIGHTS) {
    try {
      void fonts
        .load(`${weight} ${baseSize}px ${DISPLAY_FAMILY}`)
        .then(settle, () => undefined);
    } catch {
      /* older FontFaceSet, or a family it refuses to parse — the watch covers it */
    }
  }

  // Everything the explicit request cannot cover: a stylesheet that lands after
  // it, a font swapped in later, a second family finishing after the first.
  const onLoadingDone = () => settle();
  try {
    fonts.addEventListener?.("loadingdone", onLoadingDone);
    cleanup = () => fonts.removeEventListener?.("loadingdone", onLoadingDone);
  } catch {
    /* no event target here — the explicit request above still settles */
  }
  try {
    void fonts.ready?.then(settle, () => undefined);
  } catch {
    /* no `ready` in this engine */
  }

  return dispose;
}
