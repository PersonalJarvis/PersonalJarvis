/**
 * The workspace's split tree, and the geometry it draws as.
 *
 * The workspace used to be a flat "columns × slots" grid (`./paneLayout`, now
 * retired). That model made one split direction local and the other global by
 * construction: "split right" could only mean a full-height column, so
 * splitting the top pane of a stack restructured the whole workspace — the
 * exact complaint of 2026-08-12, delivered as a drawing.
 *
 * The BACKEND now owns the layout as a split tree (`jarvis/agentic_ide/
 * layout_tree.py`): a `row` lays its children side by side, a `column` stacks
 * them, a leaf is one pane, and every split carves only the clicked pane's
 * rectangle. This module is the tree's client half:
 *
 * * `treeLayout` turns the tree the session state carries into the same kind
 *   of fractional boxes and draggable seams the grid has always painted —
 *   panes stay absolutely positioned siblings inside ONE container, so
 *   nothing is ever re-parented and no terminal remounts.
 * * `dragSeam` / `evenSeam` / `evenedTree` rewrite WEIGHTS, immutably, and
 *   are the only edits a client ever makes to the tree. Structure changes
 *   (split, close, move) are asked of the backend, whose answer carries the
 *   new tree — one authority, never two placement arithmetics drifting.
 *
 * ## Weights, not pixels
 *
 * A weight is a plain multiplier against its siblings (1 and 1 are equal, 2
 * and 1 is a third), never pixels — pixels do not survive a window resize, a
 * second monitor, or the app opening on a laptop. The seam-drag math is the
 * proven arithmetic from the retired module, unchanged: pixels become weight
 * through the workspace's size along the drag axis, scaled by the fraction of
 * that axis the seam's own container spans (a nested stack's seam divides the
 * stack, not the window).
 *
 * ## Wire form IS the working form
 *
 * The session state's `layout` field arrives as exactly these shapes —
 * `{pane}` leaves and `{direction, children, weights}` splits — so there is
 * no parse step and no second representation to keep in sync. What the drag
 * hook posts back (`saveLayoutWeights`) is the same value.
 */

/** One pane, referenced by its terminal KEY ("t1") — stable across renames. */
export interface LayoutLeaf {
  pane: string;
}

/** A container: children side by side (`row`) or stacked (`column`). */
export interface LayoutSplit {
  direction: "row" | "column";
  children: LayoutNode[];
  weights: number[];
}

export type LayoutNode = LayoutLeaf | LayoutSplit;

export function isSplit(node: LayoutNode): node is LayoutSplit {
  return typeof node === "object" && node !== null && "children" in node;
}

/** Smallest a pane may be dragged to, on either axis. */
export const MIN_SEAM_PANE_PX = 120;

/** A pane's rectangle, as fractions of the workspace on both axes. */
export interface PaneBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * A draggable boundary between two siblings of one container.
 *
 * `x`/`y`/`w`/`h` describe the LINE: a vertical seam has no width and a
 * horizontal one no height, so the renderer positions it by its centre and
 * stretches it along the other axis.
 *
 * `path` + `boundary` are the seam's address: the container's child-index
 * path from the root, and the gap it divides (between children `boundary-1`
 * and `boundary`). A drag resolves them against the tree it STARTED from, so
 * a slow pointer cannot land its weights on a container that moved.
 */
export interface PaneSeam {
  id: string;
  orientation: "vertical" | "horizontal";
  x: number;
  y: number;
  w: number;
  h: number;
  /** Child-index path from the root to the container this seam divides. */
  path: number[];
  /** The gap's index within that container: between boundary-1 and boundary. */
  boundary: number;
  /** Combined weight of every sibling the drag redistributes within. */
  groupWeight: number;
  /**
   * Fraction of the workspace axis the container spans along the drag axis —
   * what turns a pointer's pixels into weight. 1 for a root-level seam; a
   * seam inside a half-width stack works against that half.
   */
  axisFraction: number;
  /** Plain-language name, used as the seam's tooltip and accessible label. */
  label: string;
}

/** Everything the grid needs to draw a workspace at the sizes it was given. */
export interface WorkspaceLayout {
  /** One box per input pane, in input order. */
  boxes: (PaneBox | undefined)[];
  seams: PaneSeam[];
}

function cleanWeight(value: unknown): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number > 0 ? number : 1;
}

/** Every pane key in reading order — depth-first, left/top first. */
export function treeLeaves(node: LayoutNode | null | undefined): string[] {
  if (!node) return [];
  if (!isSplit(node)) return [node.pane];
  return node.children.flatMap((child) => treeLeaves(child));
}

const firstLeaf = (node: LayoutNode): string =>
  isSplit(node) ? firstLeaf(node.children[0]) : node.pane;
const lastLeaf = (node: LayoutNode): string =>
  isSplit(node) ? lastLeaf(node.children[node.children.length - 1]) : node.pane;

/** The minimum a pane must know for this module: its key, and a spoken name. */
export interface SizedPane {
  key: string;
  name: string;
}

/**
 * Lay the workspace out: the tree in, boxes and seams out.
 *
 * The tree normally covers exactly the session's panes — the backend keeps it
 * that way. When it does not (a half-applied update racing a poll), the panes
 * are laid out as one even row instead: every pane visible somewhere beats a
 * blank rectangle, and the next state push repairs the shape.
 */
export function treeLayout(
  tree: LayoutNode | null | undefined,
  panes: readonly SizedPane[],
): WorkspaceLayout {
  if (panes.length === 0) return { boxes: [], seams: [] };

  const keys = new Set(panes.map((pane) => pane.key));
  const covered = treeLeaves(tree);
  const usable =
    tree != null &&
    covered.length === keys.size &&
    covered.every((pane) => keys.has(pane));
  const root: LayoutNode = usable
    ? (tree as LayoutNode)
    : panes.length === 1
      ? { pane: panes[0].key }
      : {
          direction: "row",
          children: panes.map((pane) => ({ pane: pane.key })),
          weights: panes.map(() => 1),
        };

  const names = new Map(panes.map((pane) => [pane.key, pane.name]));
  const byKey = new Map<string, PaneBox>();
  const seams: PaneSeam[] = [];

  const walk = (node: LayoutNode, box: PaneBox, path: number[]): void => {
    if (!isSplit(node)) {
      byKey.set(node.pane, box);
      return;
    }
    const along = node.direction === "row" ? box.w : box.h;
    const weights = node.children.map((_, index) => cleanWeight(node.weights[index]));
    const total = weights.reduce((sum, weight) => sum + weight, 0) || 1;
    let cursor = node.direction === "row" ? box.x : box.y;
    // "root" for the top-level container, else the child-index path — short,
    // stable while the structure stands, and readable in a test failure.
    const at = path.length === 0 ? "root" : path.join(".");
    node.children.forEach((child, index) => {
      const share = (weights[index] / total) * along;
      if (index > 0) {
        const before = names.get(lastLeaf(node.children[index - 1])) ?? "the pane before";
        const after = names.get(firstLeaf(child)) ?? "the pane after";
        seams.push(
          node.direction === "row"
            ? {
                id: `${at}:${index}`,
                orientation: "vertical",
                x: cursor,
                y: box.y,
                w: 0,
                h: box.h,
                path,
                boundary: index,
                groupWeight: total,
                axisFraction: box.w,
                label: `Drag to resize ${before} and ${after}`,
              }
            : {
                id: `${at}:${index}`,
                orientation: "horizontal",
                x: box.x,
                y: cursor,
                w: box.w,
                h: 0,
                path,
                boundary: index,
                groupWeight: total,
                axisFraction: box.h,
                label: `Drag to resize ${before} and ${after}`,
              },
        );
      }
      walk(
        child,
        node.direction === "row"
          ? { x: cursor, y: box.y, w: share, h: box.h }
          : { x: box.x, y: cursor, w: box.w, h: share },
        [...path, index],
      );
      cursor += share;
    });
  };

  walk(root, { x: 0, y: 0, w: 1, h: 1 }, []);
  return { boxes: panes.map((pane) => byKey.get(pane.key)), seams };
}

/** The container a seam's path names, resolved against ``root`` — or null. */
function containerAt(root: LayoutNode, path: number[]): LayoutSplit | null {
  let node: LayoutNode = root;
  for (const index of path) {
    if (!isSplit(node)) return null;
    const child: LayoutNode | undefined = node.children[index];
    if (!child) return null;
    node = child;
  }
  return isSplit(node) ? node : null;
}

/** ``root`` with the container at ``path`` carrying ``weights`` — immutably. */
function withWeights(root: LayoutNode, path: number[], weights: number[]): LayoutNode {
  if (!isSplit(root)) return root;
  if (path.length === 0) return { ...root, weights };
  const [head, ...rest] = path;
  const child = root.children[head];
  if (!child) return root;
  const children = [...root.children];
  children[head] = withWeights(child, rest, weights);
  return { ...root, children };
}

/**
 * Move one seam by ``deltaPx`` and hand back the tree that produces it.
 *
 * The two neighbours trade weight and nothing else changes — that is what
 * makes a drag local rather than a re-layout. Both sides are held at
 * `MIN_SEAM_PANE_PX`, and two panes that cannot both meet the minimum share
 * what there is evenly rather than one being dragged out of existence. A seam
 * whose container no longer exists (the workspace reshaped mid-gesture)
 * changes nothing.
 */
export function dragSeam(
  root: LayoutNode,
  seam: PaneSeam,
  deltaPx: number,
  axisPx: number,
): LayoutNode {
  const span = axisPx * seam.axisFraction;
  if (!Number.isFinite(span) || span <= 0) return root;
  const node = containerAt(root, seam.path);
  if (!node || seam.boundary < 1 || seam.boundary >= node.children.length) return root;

  const weights = node.children.map((_, index) => cleanWeight(node.weights[index]));
  const before = weights[seam.boundary - 1];
  const after = weights[seam.boundary];
  const total = before + after;
  const perPx = seam.groupWeight / span;
  const min = MIN_SEAM_PANE_PX * perPx;

  let nextBefore: number;
  if (total <= min * 2) {
    nextBefore = total / 2;
  } else {
    const wanted = before + deltaPx * perPx;
    nextBefore = Math.min(total - min, Math.max(min, wanted));
  }
  weights[seam.boundary - 1] = nextBefore;
  weights[seam.boundary] = total - nextBefore;
  return withWeights(root, seam.path, weights);
}

/**
 * How many pane stripes a subtree lines up along ``direction``.
 *
 * "Even" is judged in TERMINALS, not in tree children: a nested group holding
 * two side-by-side stacks is two stripes wide and must be given twice the
 * weight of a lone pane for every terminal to come out the same width. A
 * split running the other way does not multiply — its widest member is how
 * much room the group needs. Without this, a workspace like
 * ``row[pane, group-of-two, pane]`` "evens" to quarters and the grouped
 * terminals end up half as wide as their neighbours (reported 2026-08-12).
 */
function axisSpan(node: LayoutNode, direction: "row" | "column"): number {
  if (!isSplit(node)) return 1;
  const spans = node.children.map((child) => axisSpan(child, direction));
  if (spans.length === 0) return 1;
  return node.direction === direction
    ? spans.reduce((sum, span) => sum + span, 0)
    : Math.max(...spans);
}

/**
 * Level a seam's two neighbours, leaving every other pane alone.
 *
 * Their combined weight is dealt out by stripe count, so "level" means the
 * TERMINALS either side end up the same size even when one side is a nested
 * group — the same yardstick `evenedTree` and `isEvenTree` use.
 */
export function evenSeam(root: LayoutNode, seam: PaneSeam): LayoutNode {
  const node = containerAt(root, seam.path);
  if (!node || seam.boundary < 1 || seam.boundary >= node.children.length) return root;
  const weights = node.children.map((_, index) => cleanWeight(node.weights[index]));
  const before = axisSpan(node.children[seam.boundary - 1], node.direction);
  const after = axisSpan(node.children[seam.boundary], node.direction);
  const total = weights[seam.boundary - 1] + weights[seam.boundary];
  weights[seam.boundary - 1] = (total * before) / (before + after);
  weights[seam.boundary] = (total * after) / (before + after);
  return withWeights(root, seam.path, weights);
}

/**
 * The same tree with every TERMINAL back at an equal share of the workspace.
 *
 * Weights are stripe counts rather than a flat 1, so a nested group is given
 * as much room as the terminals it lines up — the arrangement itself is never
 * touched, only how the boundaries fall.
 */
export function evenedTree(root: LayoutNode): LayoutNode {
  if (!isSplit(root)) return root;
  return {
    ...root,
    children: root.children.map((child) => evenedTree(child)),
    weights: root.children.map((child) => axisSpan(child, root.direction)),
  };
}

/**
 * Same structure, same panes in the same places — weights ignored.
 *
 * The drag hook keeps its locally dragged weights only while the server's
 * tree still has this shape; a structural change (a split, a close, a move,
 * another client) always wins over an in-flight size preference.
 */
export function sameShape(
  a: LayoutNode | null | undefined,
  b: LayoutNode | null | undefined,
): boolean {
  if (!a || !b) return !a && !b;
  const aSplit = isSplit(a);
  const bSplit = isSplit(b);
  if (!aSplit || !bSplit) {
    return !aSplit && !bSplit && (a as LayoutLeaf).pane === (b as LayoutLeaf).pane;
  }
  if (a.direction !== b.direction || a.children.length !== b.children.length) {
    return false;
  }
  return a.children.every((child, index) => sameShape(child, b.children[index]));
}

/**
 * How far apart two sibling weights may be and still count as the same size.
 *
 * Relative, because weights are multipliers: 7 and 7.0000001 are the same
 * pane on any physical screen, and so are 1 and 1.00000001.
 */
const EVEN_EPSILON = 1e-4;

/**
 * Is every TERMINAL already at the share `evenedTree` would give it?
 *
 * Judged per stripe, the way `evenedTree` deals weights out — the button this
 * answers for disables on "already even", so the two must agree or the fix
 * for an uneven workspace is a control that refuses the click (the 2026-08-12
 * report: equal container weights over a nested group read as "even" while
 * one terminal drew four times the size of another).
 */
export function isEvenTree(root: LayoutNode | null | undefined): boolean {
  if (!root || !isSplit(root)) return true;
  const shares = root.children.map(
    (child, index) => cleanWeight(root.weights[index]) / axisSpan(child, root.direction),
  );
  const first = shares[0];
  if (!shares.every((share) => Math.abs(share - first) <= EVEN_EPSILON * first)) {
    return false;
  }
  return root.children.every((child) => isEvenTree(child));
}
