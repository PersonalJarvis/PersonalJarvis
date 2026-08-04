/**
 * What size every pane actually gets, and where the seams between them are.
 *
 * `./layout` answers "which column and which slot" — the arrangement. This
 * module answers "how much room", which used to have no answer at all: the grid
 * asked CSS for equal tracks, so every pane in a band was the same width and
 * every pane in a column the same height, always.
 *
 * That is wrong in the two ways people actually notice:
 *
 * * **Splitting a pane resized the whole workspace.** "Split right" added a
 *   column, and a column is a share of the window — so splitting one pane made
 *   every OTHER pane narrower. Splitting means halving the thing you split
 *   everywhere else, and that is what it means here now: the new pane takes half
 *   of its anchor's room and nothing else moves.
 * * **The seams could not be moved.** A workspace is not a set of equally
 *   interesting panes. The one being read wants width; the three being watched
 *   do not. So every boundary — between two columns, between two bands, between
 *   two panes stacked in one column — is draggable.
 *
 * ## Weights, not pixels
 *
 * A pane's size is stored as a WEIGHT relative to its siblings, never as pixels.
 * Pixels do not survive a window resize, a second monitor, or the app opening on
 * a laptop; a weight does, and it is also what makes "half" mean half at every
 * window size.
 *
 * Two families of weight, each attached to the most stable identity available:
 *
 * * `columns` — by column index, because a column has no id of its own (the
 *   backend renumbers them on every open and close). `remapColumnWeights` carries
 *   them across such a renumber by matching on the panes each column holds.
 * * `panes` — by call-sign, which IS stable for a pane's whole life.
 *
 * There used to be a third, `bands`, for the height of each line of columns.
 * The workspace does not wrap into lines any more (see the header of
 * `./layout`), so there is exactly one line and nothing to divide between two
 * of them.
 *
 * ## Fractions, not a CSS grid
 *
 * The layout is returned as fractional rectangles because one CSS grid cannot
 * express it: `grid-template-columns` is shared by every row, so a column's
 * width could never be dragged independently of the panes stacked below it.
 * Panes are therefore positioned absolutely inside one container — which keeps
 * the property the grid was chosen for in the first place, that a pane is never
 * re-parented and so never unmounts (see the header of `./layout`).
 */
import type { Positioned } from "./layout";

/** Smallest a pane may be dragged to, on either axis. */
export const MIN_SEAM_PANE_PX = 120;

/** A pane's place in the workspace: what this module needs on top of `Positioned`. */
export interface Sized extends Positioned {
  /** Call-sign — the key its height weight is stored under. */
  name: string;
}

/**
 * How the workspace is divided, relative to its siblings at each level.
 *
 * Every value is a plain multiplier against its neighbours: two columns at 1 and
 * 1 are equal, at 2 and 1 the first is twice as wide. A missing entry is 1, so
 * an empty object is the even split the workspace has always started with, and a
 * layout stored by an older build simply picks up defaults for what it lacks.
 */
export interface PaneWeights {
  /** Width of each column, by column index (left to right). */
  columns: number[];
  /** Height of each pane within its own column's stack, by call-sign. */
  panes: Record<string, number>;
}

/** The even split — what a workspace looks like before anything is dragged. */
export function evenWeights(): PaneWeights {
  return { columns: [], panes: {} };
}

/** A pane's rectangle, as fractions of the workspace on both axes. */
export interface PaneBox {
  x: number;
  y: number;
  w: number;
  h: number;
  /** Its column's index among all columns, left to right. */
  columnIndex: number;
  /** Its place in that column's stack, top to bottom. */
  slotIndex: number;
}

/** Which boundary a seam is, and therefore which weights a drag changes. */
export type SeamKind = "column" | "pane";

/**
 * A draggable boundary between two neighbours.
 *
 * `x`/`y`/`w`/`h` describe the LINE: a vertical seam has no width and a
 * horizontal one no height, so the renderer positions it by its centre and
 * stretches it along the other axis.
 */
export interface PaneSeam {
  id: string;
  kind: SeamKind;
  orientation: "vertical" | "horizontal";
  x: number;
  y: number;
  w: number;
  h: number;
  /**
   * The two neighbours, as keys into the matching weight family: column indexes
   * for `column`, call-signs for `pane`.
   */
  before: string | number;
  after: string | number;
  /** Combined weight of every sibling the drag redistributes within. */
  groupWeight: number;
  /**
   * Fraction of the workspace axis those siblings span. Always 1 now that a
   * workspace is one line of columns — the columns span its full width, and a
   * column's stack its full height — but kept as a field because it is what
   * turns a pointer's pixels into weight, and reading `deltaPx / (axisPx * f)`
   * at the drag site is what makes that conversion checkable.
   */
  axisFraction: number;
  /** Plain-language name, used as the seam's tooltip and accessible label. */
  label: string;
}

/** Everything a workspace needs to draw itself at the sizes it was given. */
export interface WorkspaceLayout {
  /** One box per input pane, in input order. */
  boxes: PaneBox[];
  seams: PaneSeam[];
  /**
   * Columns the workspace holds, left to right.
   *
   * Purely informational now: the workspace is drawn at exactly the size it was
   * given on both axes, so nothing multiplies this by a minimum width any more
   * (see the header of `./layout`).
   */
  columns: number;
}

function weightAt(list: readonly number[], index: number): number {
  const value = list[index];
  return Number.isFinite(value) && (value as number) > 0 ? (value as number) : 1;
}

function paneWeight(weights: PaneWeights, name: string): number {
  const value = weights.panes[name];
  return Number.isFinite(value) && value > 0 ? value : 1;
}

/**
 * Lay a workspace out.
 *
 * Every column the panes name gets a place on ONE line, however many there are:
 * the workspace never re-arranges itself to fit a window, and never grows past
 * it either (see the header of `./layout`). The boxes are fractions of whatever
 * area the caller draws them in, so "one more pane" is always "everything a
 * little smaller" and never "scroll to find it".
 */
export function paneLayout<T extends Sized>(
  panes: readonly T[],
  weights: PaneWeights = evenWeights(),
): WorkspaceLayout {
  if (panes.length === 0) {
    return { boxes: [], seams: [], columns: 0 };
  }

  // Columns in left-to-right order with gaps closed, exactly as `paneGrid` does
  // — a half-applied close must never render as a blank stripe.
  const ordered = [...new Set(panes.map((p) => p.column))].sort((a, b) => a - b);
  const columnIndex = new Map(ordered.map((column, index) => [column, index]));

  // Each column's panes, top to bottom, as indexes into `panes`.
  const stacks: number[][] = ordered.map(() => []);
  panes.forEach((pane, index) => {
    stacks[columnIndex.get(pane.column) ?? 0].push(index);
  });
  for (const stack of stacks) stack.sort((a, b) => panes[a].slot - panes[b].slot);

  const columnWeights = stacks.map((_, index) => weightAt(weights.columns, index));
  const columnTotal = columnWeights.reduce((sum, w) => sum + w, 0) || 1;

  const boxes: PaneBox[] = new Array(panes.length);
  const seams: PaneSeam[] = [];
  let xCursor = 0;

  stacks.forEach((stack, index) => {
    const width = columnWeights[index] / columnTotal;

    // --------------------------------------------------- panes in this column
    const stackWeights = stack.map((pane) => paneWeight(weights, panes[pane].name));
    const stackTotal = stackWeights.reduce((sum, w) => sum + w, 0) || 1;
    let stackCursor = 0;
    stack.forEach((pane, slot) => {
      const share = stackWeights[slot] / stackTotal;
      boxes[pane] = {
        x: xCursor,
        y: stackCursor,
        w: width,
        h: share,
        columnIndex: index,
        slotIndex: slot,
      };
      if (slot > 0) {
        seams.push({
          id: `pane:${panes[stack[slot - 1]].name}:${panes[pane].name}`,
          kind: "pane",
          orientation: "horizontal",
          x: xCursor,
          y: stackCursor,
          w: width,
          h: 0,
          before: panes[stack[slot - 1]].name,
          after: panes[pane].name,
          groupWeight: stackTotal,
          axisFraction: 1,
          label: `Drag to resize ${panes[stack[slot - 1]].name} and ${panes[pane].name}`,
        });
      }
      stackCursor += share;
    });

    if (index > 0) {
      seams.push({
        id: `column:${index - 1}:${index}`,
        kind: "column",
        orientation: "vertical",
        x: xCursor,
        y: 0,
        w: 0,
        h: 1,
        before: index - 1,
        after: index,
        groupWeight: columnTotal,
        axisFraction: 1,
        label: "Drag to resize these two columns of terminals",
      });
    }
    xCursor += width;
  });

  return { boxes, seams, columns: stacks.length };
}

// ---------------------------------------------------------------- dragging

function readSide(weights: PaneWeights, seam: PaneSeam, side: string | number): number {
  if (seam.kind === "pane") return paneWeight(weights, String(side));
  return weightAt(weights.columns, Number(side));
}

function writeSide(
  weights: PaneWeights,
  seam: PaneSeam,
  side: string | number,
  value: number,
): PaneWeights {
  if (seam.kind === "pane") {
    return { ...weights, panes: { ...weights.panes, [String(side)]: value } };
  }
  const list = [...weights.columns];
  const index = Number(side);
  // A weight nobody has dragged yet is absent, not zero — fill the gap with the
  // default so writing index 5 of an empty list does not create four holes that
  // read back as 0 and collapse their columns.
  while (list.length <= index) list.push(1);
  list[index] = value;
  return { ...weights, columns: list };
}

/**
 * Move one seam by ``deltaPx`` and hand back the weights that produce it.
 *
 * The two neighbours trade weight and nothing else changes — that is what makes
 * a drag local rather than a re-layout. `axisPx` is the workspace's size along
 * the drag axis, which is how pixels become weight, and both sides are held at
 * `MIN_SEAM_PANE_PX` so a seam can be pushed to the edge but never through it.
 */
export function dragSeam(
  weights: PaneWeights,
  seam: PaneSeam,
  deltaPx: number,
  axisPx: number,
): PaneWeights {
  const span = axisPx * seam.axisFraction;
  if (!Number.isFinite(span) || span <= 0) return weights;

  const before = readSide(weights, seam, seam.before);
  const after = readSide(weights, seam, seam.after);
  const total = before + after;
  const perPx = seam.groupWeight / span;
  const min = MIN_SEAM_PANE_PX * perPx;

  // Two panes that cannot both meet the minimum share what there is evenly —
  // better a pair of equally cramped panes than one dragged out of existence.
  if (total <= min * 2) {
    const half = total / 2;
    return writeSide(writeSide(weights, seam, seam.before, half), seam, seam.after, half);
  }

  const wanted = before + deltaPx * perPx;
  const next = Math.min(total - min, Math.max(min, wanted));
  return writeSide(
    writeSide(weights, seam, seam.before, next),
    seam,
    seam.after,
    total - next,
  );
}

/**
 * Give a seam's two neighbours the same size, leaving every other pane alone.
 *
 * This is what a double-click on a splitter does elsewhere, and it is genuinely
 * the useful reset: "make these two even again" is a request people have, while
 * "put the whole workspace back" almost never is.
 */
export function evenSeam(weights: PaneWeights, seam: PaneSeam): PaneWeights {
  const half = (readSide(weights, seam, seam.before) + readSide(weights, seam, seam.after)) / 2;
  return writeSide(writeSide(weights, seam, seam.before, half), seam, seam.after, half);
}

/**
 * How close two fractions have to be before nobody could see the difference.
 *
 * A fraction of the workspace, so on a 4K monitor this is four thousandths of a
 * pixel — far below anything a screen can draw, and loose enough that a seam
 * dragged to visually-even (or the float noise a chain of drags leaves behind)
 * counts as even rather than leaving the toolbar offering a button that would
 * change nothing.
 */
const EVEN_EPSILON = 1e-4;

/**
 * Is every terminal already sharing its space equally with its siblings?
 *
 * Answered by comparing the layout the current weights produce against the one
 * `evenWeights` produces, rather than by inspecting the weights themselves.
 * That is the honest question — "would evening this out move anything on
 * screen" — and it is the same answer for weights of `[1, 1]` and `[7, 7]`,
 * which no comparison of the stored numbers alone would give.
 */
export function isEvenLayout<T extends Sized>(
  panes: readonly T[],
  weights: PaneWeights,
): boolean {
  const current = paneLayout(panes, weights);
  const even = paneLayout(panes, evenWeights());
  return current.boxes.every((box, index) => {
    const target = even.boxes[index];
    if (!target) return false;
    return (
      Math.abs(box.x - target.x) <= EVEN_EPSILON &&
      Math.abs(box.y - target.y) <= EVEN_EPSILON &&
      Math.abs(box.w - target.w) <= EVEN_EPSILON &&
      Math.abs(box.h - target.h) <= EVEN_EPSILON
    );
  });
}

// ------------------------------------------------------- splits and closes

/** Which column index each pane sits in, gaps closed, for a set of panes. */
function columnIndexes<T extends Sized>(panes: readonly T[]): Map<string, number> {
  const ordered = [...new Set(panes.map((p) => p.column))].sort((a, b) => a - b);
  const byColumn = new Map(ordered.map((column, index) => [column, index]));
  return new Map(panes.map((pane) => [pane.name, byColumn.get(pane.column) ?? 0]));
}

/** How many columns a set of panes occupies. */
function columnCount<T extends Sized>(panes: readonly T[]): number {
  return new Set(panes.map((p) => p.column)).size;
}

/**
 * Which column each pane sits in, by call-sign, gaps closed.
 *
 * This is the ARRANGEMENT the column weights are keyed to: the weights store
 * only "column 2 is wide", so they mean anything at all only together with the
 * mapping from panes to those indexes. The grid keeps the arrangement its
 * weights belong to and compares it against every new session — that is how it
 * notices the backend renumbered columns underneath index-keyed widths, which
 * happens on every open and close the grid itself did not perform (a terminal
 * opened by voice, a second client, the backend resuming after a restart).
 */
export function paneArrangement<T extends Sized>(
  panes: readonly T[],
): Record<string, number> {
  return Object.fromEntries(columnIndexes(panes));
}

/** Do two arrangements place exactly the same panes in the same columns? */
export function sameArrangement(
  a: Record<string, number>,
  b: Record<string, number>,
): boolean {
  const names = Object.keys(a);
  if (names.length !== Object.keys(b).length) return false;
  return names.every((name) => b[name] === a[name]);
}

/**
 * The pane list an arrangement describes — enough for `remapColumnWeights`.
 *
 * Column matching only reads names and columns, so every pane is slot 0.
 */
export function panesFromArrangement(
  arrangement: Record<string, number>,
): Sized[] {
  return Object.entries(arrangement).map(([name, column]) => ({
    name,
    column,
    slot: 0,
  }));
}

/**
 * Carry column widths across a renumber.
 *
 * The backend packs column numbers on every open and close, so column 3 after a
 * split is not the column 3 that was dragged wide a minute ago. Matching on the
 * panes each column HOLDS survives that: a column keeps its width as long as it
 * keeps most of its panes, and a column with no predecessor starts even.
 */
export function remapColumnWeights<T extends Sized>(
  weights: PaneWeights,
  before: readonly T[],
  after: readonly T[],
): PaneWeights {
  const wasIn = columnIndexes(before);
  const nowIn = columnIndexes(after);

  // For each new column, how many of its panes came from each old column.
  const votes = new Map<number, Map<number, number>>();
  for (const pane of after) {
    const old = wasIn.get(pane.name);
    if (old === undefined) continue;
    const now = nowIn.get(pane.name) ?? 0;
    const tally = votes.get(now) ?? new Map<number, number>();
    tally.set(old, (tally.get(old) ?? 0) + 1);
    votes.set(now, tally);
  }

  const claimed = new Set<number>();
  const columns: number[] = [];
  for (let index = 0; index < columnCount(after); index += 1) {
    const tally = votes.get(index);
    let best: number | null = null;
    let bestCount = 0;
    for (const [old, count] of tally ?? []) {
      // Ties go to the leftmost old column, so a split's two halves cannot both
      // claim the same ancestor and end up with the same width by accident.
      if (count > bestCount || (count === bestCount && best !== null && old < best)) {
        best = old;
        bestCount = count;
      }
    }
    if (best !== null && !claimed.has(best)) {
      claimed.add(best);
      columns.push(weightAt(weights.columns, best));
    } else {
      columns.push(1);
    }
  }

  const live = new Set(after.map((pane) => pane.name));
  const panes = Object.fromEntries(
    Object.entries(weights.panes).filter(([name]) => live.has(name)),
  );
  return { columns, panes };
}

/**
 * The weights a workspace has right after ``added`` was split off ``anchor``.
 *
 * A split HALVES its anchor. That is the whole point of the change: opening a
 * terminal beside a pane used to be a workspace-wide event — every other pane
 * lost width to make room for a new full-height column — and people reasonably
 * expect the pane they clicked to be the only one that changes.
 *
 * Halving now holds for every split, including the ones that used to land on
 * another line: a workspace is one line of columns, so the new pane and its
 * anchor are always neighbours sharing the room the anchor had.
 */
export function weightsAfterSplit<T extends Sized>(
  weights: PaneWeights,
  before: readonly T[],
  after: readonly T[],
  anchor: string,
  added: string,
): PaneWeights {
  const carried = remapColumnWeights(weights, before, after);
  const nowIn = columnIndexes(after);
  const anchorColumn = nowIn.get(anchor);
  const addedColumn = nowIn.get(added);
  if (anchorColumn === undefined || addedColumn === undefined) return carried;

  if (anchorColumn === addedColumn) {
    // Split down: the new pane takes half of the anchor's height, and every
    // other pane in the column keeps exactly the height it had.
    const half = paneWeight(carried, anchor) / 2;
    return {
      ...carried,
      panes: { ...carried.panes, [anchor]: half, [added]: half },
    };
  }

  // Split right: the two halves together occupy the width the anchor had.
  const half = weightAt(carried.columns, anchorColumn) / 2;
  const columns = [...carried.columns];
  while (columns.length <= Math.max(anchorColumn, addedColumn)) columns.push(1);
  columns[anchorColumn] = half;
  columns[addedColumn] = half;
  return { ...carried, columns };
}
