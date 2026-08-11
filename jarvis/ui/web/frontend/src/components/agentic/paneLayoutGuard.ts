/**
 * Detects a workspace whose PIXELS disagree with its layout.
 *
 * `paneLayout` cannot produce overlapping boxes — its fractions partition the
 * canvas exactly. But the boxes reach the screen through more than one hand:
 * React writes them as style props, a seam drag paints them straight onto the
 * DOM (`paintDraggedLayout`), moves glide there over 300 ms, and maximize swaps
 * the whole style object. Any of those paths interrupted at the wrong moment
 * can leave a pane standing where another one is — and every status the app
 * shows stays green, because the DATA is correct and only the pixels lie
 * (maintainer report 2026-08-11: a terminal half-covered by its neighbour
 * while the workspace claimed all was well).
 *
 * So the grid measures what is actually on screen and asks three questions no
 * healthy workspace ever answers yes to:
 *
 * * do two visible panes overlap? Neighbours are a `GRID_GAP_PX` apart by
 *   construction, so ANY intersection is a bug — how deep does not matter.
 * * does a pane stick out of the canvas? The canvas is `overflow-hidden`, so
 *   an escaped pane is silently clipped mid-word.
 * * is a terminal's rendered content bigger than its pane? That is a missed
 *   refit: the PTY still thinks the pane is wide, and everything past the edge
 *   is cut off.
 *
 * Pure functions over plain rectangles, so the answer is testable without a
 * browser laying anything out. The grid owns the measuring and the repair.
 */

/** One visible pane, measured from the DOM. All values are viewport px. */
export interface MeasuredPane {
  name: string;
  left: number;
  top: number;
  width: number;
  height: number;
  /**
   * The terminal's rendered screen (xterm's `.xterm-screen`), when the pane
   * holds one. Sized `cols x cellWidth` by the fit — so it may be a little
   * SMALLER than the pane (the remainder under one character cell) but must
   * never be bigger. Absent for a pane still connecting.
   */
  content?: { left: number; top: number; width: number; height: number };
}

/** The canvas the panes must stay inside, measured from the DOM. */
export interface MeasuredCanvas {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Everything wrong with what is on screen. Every list empty = healthy. */
export interface LayoutViolations {
  /** Pairs of pane names whose boxes intersect. */
  overlaps: Array<[string, string]>;
  /** Panes reaching past the canvas edge, into the overflow clip. */
  escaped: string[];
  /** Panes whose terminal content is wider or taller than the pane itself. */
  clipped: string[];
}

/**
 * Sub-pixel slack before a fault counts.
 *
 * Fractional percentages resolve to fractional pixels, and the browser rounds
 * them per element — two neighbours can honestly report edges 0.4 px into each
 * other. One pixel is far above that noise and far below anything a person
 * could see, let alone the half-a-terminal the report was about.
 */
const TOLERANCE_PX = 1;

/**
 * Extra slack for the content check only.
 *
 * The measured screen is the live xterm element mid-everything — font metrics
 * settling, a devicePixelRatio rounding the cell width up — so it gets a
 * little more room than the boxes do. A stale fit is off by whole character
 * cells (8–17 px each), never by three pixels.
 */
const CONTENT_TOLERANCE_PX = 3;

function intersects(a: MeasuredPane, b: MeasuredPane): boolean {
  return (
    a.left + TOLERANCE_PX < b.left + b.width &&
    b.left + TOLERANCE_PX < a.left + a.width &&
    a.top + TOLERANCE_PX < b.top + b.height &&
    b.top + TOLERANCE_PX < a.top + a.height
  );
}

/** Measure a set of visible panes against the three rules above. */
export function findLayoutViolations(
  panes: readonly MeasuredPane[],
  canvas: MeasuredCanvas,
): LayoutViolations {
  const overlaps: Array<[string, string]> = [];
  const escaped: string[] = [];
  const clipped: string[] = [];

  for (let i = 0; i < panes.length; i += 1) {
    const pane = panes[i];
    // A pane the browser reports as sizeless is hidden (`display: none` under
    // a maximize, a jsdom test with no layout at all) — nothing to judge.
    if (pane.width <= 0 || pane.height <= 0) continue;

    for (let j = i + 1; j < panes.length; j += 1) {
      const other = panes[j];
      if (other.width <= 0 || other.height <= 0) continue;
      if (intersects(pane, other)) overlaps.push([pane.name, other.name]);
    }

    if (
      pane.left < canvas.left - TOLERANCE_PX ||
      pane.top < canvas.top - TOLERANCE_PX ||
      pane.left + pane.width > canvas.left + canvas.width + TOLERANCE_PX ||
      pane.top + pane.height > canvas.top + canvas.height + TOLERANCE_PX
    ) {
      escaped.push(pane.name);
    }

    const content = pane.content;
    if (
      content &&
      content.width > 0 &&
      content.height > 0 &&
      (content.left + content.width > pane.left + pane.width + CONTENT_TOLERANCE_PX ||
        content.top + content.height > pane.top + pane.height + CONTENT_TOLERANCE_PX)
    ) {
      clipped.push(pane.name);
    }
  }

  return { overlaps, escaped, clipped };
}

/** Is there anything to repair at all? */
export function hasLayoutViolations(violations: LayoutViolations): boolean {
  return (
    violations.overlaps.length > 0 ||
    violations.escaped.length > 0 ||
    violations.clipped.length > 0
  );
}

/** One line for the console, naming what was found. */
export function describeLayoutViolations(violations: LayoutViolations): string {
  const parts: string[] = [];
  if (violations.overlaps.length > 0) {
    parts.push(
      `overlapping panes: ${violations.overlaps.map((pair) => pair.join("+")).join(", ")}`,
    );
  }
  if (violations.escaped.length > 0) {
    parts.push(`panes outside the canvas: ${violations.escaped.join(", ")}`);
  }
  if (violations.clipped.length > 0) {
    parts.push(`terminals bigger than their pane: ${violations.clipped.join(", ")}`);
  }
  return parts.join("; ");
}
