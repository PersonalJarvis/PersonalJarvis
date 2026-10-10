import type { PaneMovePosition, TerminalState } from "@/lib/agenticIdeApi";
import { isSplit, treeLayout, treeLeaves, type LayoutNode, type PaneBox } from "./treeLayout";

/** i18n keys of the drop-target labels; translate where rendered. */
export const DOCK_LABELS: Record<PaneMovePosition, string> = {
  swap: "ide_panes.dock.swap", left: "ide_panes.dock.left", right: "ide_panes.dock.right",
  above: "ide_panes.dock.above", below: "ide_panes.dock.below",
};

/**
 * A workspace holds any number of panes in any shape. The only guard is on ONE
 * request: a launch opens at most this many at once. Mirrors
 * `MAX_PANES_PER_REQUEST` in `jarvis/agentic_ide/session.py`.
 */
export const MAX_PANES_PER_REQUEST = 100;

/** How wide the even grid prefers to be; a preference, never a limit. Mirrors `BALANCED_GRID_COLUMNS`. */
export const BALANCED_GRID_COLUMNS = 4;

/**
 * Columns of the even grid: two panes share a row, three to eight use two
 * rows, up to sixteen one more row per four panes, beyond that about square.
 * Mirrors `balanced_columns`.
 */
export function balancedColumns(count: number): number {
  if (count <= 2) return Math.max(1, count);
  const wide = BALANCED_GRID_COLUMNS;
  if (count > wide * wide) return Math.ceil(Math.sqrt(count));
  const rows = count <= 2 * wide ? 2 : Math.ceil(count / wide);
  return Math.min(wide, Math.ceil(count / rows));
}

/** The even grid for `keys`, dealt row by row. */
export function balancedLayout(keys: readonly string[]): LayoutNode | null {
  if (!keys.length) return null;
  const columns = balancedColumns(keys.length);
  const split = (direction: "row" | "column", children: LayoutNode[]): LayoutNode =>
    children.length === 1 ? children[0] : { direction, children, weights: children.map(() => 1) };
  const rows: LayoutNode[] = [];
  for (let i = 0; i < keys.length; i += columns) rows.push(split("row", keys.slice(i, i + columns).map((pane) => ({ pane }))));
  return split("column", rows);
}

export function workspaceLayout(tree: LayoutNode | null | undefined, terminals: readonly Pick<TerminalState, "key">[]): LayoutNode | null {
  const keys = terminals.map((terminal) => terminal.key);
  const leaves = treeLeaves(tree);
  return tree && leaves.length === keys.length && new Set(leaves).size === keys.length && leaves.every((key) => keys.includes(key))
    ? tree : balancedLayout(keys);
}

export function layoutSpan(tree: LayoutNode | null, axis: "row" | "column"): number {
  if (!tree) return 0;
  if (!isSplit(tree)) return 1;
  const spans = tree.children.map((child) => layoutSpan(child, axis));
  return tree.direction === axis ? spans.reduce((a, b) => a + b, 0) : Math.max(0, ...spans);
}

export function isBalancedWorkspace(tree: LayoutNode | null | undefined, terminals: readonly Pick<TerminalState, "key" | "name">[]): boolean {
  const current = treeLayout(workspaceLayout(tree, terminals), terminals).boxes;
  const balanced = treeLayout(balancedLayout(terminals.map((terminal) => terminal.key)), terminals).boxes;
  return current.every((box, index) => box && balanced[index] &&
    (["x", "y", "w", "h"] as const).every((axis) => Math.abs(box[axis] - balanced[index]![axis]) < 0.00001));
}

/** Preview only: the server remains authoritative for the saved split tree. */
export function previewDock(tree: LayoutNode, source: string, target: string, position: PaneMovePosition): LayoutNode {
  if (source === target || !treeLeaves(tree).includes(source) || !treeLeaves(tree).includes(target)) return tree;
  const swap = (node: LayoutNode): LayoutNode => isSplit(node)
    ? { ...node, children: node.children.map(swap) }
    : { pane: node.pane === source ? target : node.pane === target ? source : node.pane };
  if (position === "swap") return swap(tree);
  const remove = (node: LayoutNode): LayoutNode | null => {
    if (!isSplit(node)) return node.pane === source ? null : node;
    const children: LayoutNode[] = [], weights: number[] = [];
    node.children.forEach((child, index) => {
      const kept = remove(child);
      if (kept) { children.push(kept); weights.push(node.weights[index] ?? 1); }
    });
    return children.length === 0 ? null : children.length === 1 ? children[0] : { ...node, children, weights };
  };
  const insert = (node: LayoutNode): LayoutNode => {
    if (isSplit(node)) return { ...node, children: node.children.map(insert) };
    if (node.pane !== target) return node;
    const pair = [{ pane: source }, node];
    if (position === "right" || position === "below") pair.reverse();
    return { direction: position === "left" || position === "right" ? "row" : "column", children: pair, weights: [1, 1] };
  };
  return insert(remove(tree)!);
}

/** Preview splitting a pane in a specific direction with a new terminal. */
export function previewSplit(
  tree: LayoutNode | null,
  targetKey: string,
  addedKey: string,
  direction: "right" | "down" | "left" | "above"
): LayoutNode {
  if (!tree) return { pane: addedKey };
  const insert = (node: LayoutNode): LayoutNode => {
    if (isSplit(node)) return { ...node, children: node.children.map(insert) };
    if (node.pane !== targetKey) return node;
    const pair = [{ pane: addedKey }, node];
    if (direction === "right" || direction === "down") pair.reverse();
    return {
      direction: direction === "left" || direction === "right" ? "row" : "column",
      children: pair,
      weights: [1, 1],
    };
  };
  return insert(tree);
}

/** The nearest outer quarter is a docking edge; the middle swaps whole panes. */
export function dockPosition(x: number, y: number, rect: Pick<DOMRect, "left" | "top" | "width" | "height">): PaneMovePosition {
  const horizontal = (x - rect.left) / rect.width, vertical = (y - rect.top) / rect.height;
  const edges: [PaneMovePosition, number][] = [["left", horizontal], ["right", 1 - horizontal], ["above", vertical], ["below", 1 - vertical]];
  edges.sort((a, b) => a[1] - b[1]);
  return edges[0][1] <= 0.25 ? edges[0][0] : "swap";
}

/** Keep terminal DOM nodes as siblings while their saved rectangles change. */
export function paneStyle(box: PaneBox): React.CSSProperties {
  return {
    position: "absolute", left: `calc(${box.x * 100}% + ${box.x > 0 ? 4 : 0}px)`, top: `calc(${box.y * 100}% + ${box.y > 0 ? 4 : 0}px)`,
    width: `calc(${box.w * 100}% - ${(box.x > 0 ? 4 : 0) + (box.x + box.w < 0.99999 ? 4 : 0)}px)`,
    height: `calc(${box.h * 100}% - ${(box.y > 0 ? 4 : 0) + (box.y + box.h < 0.99999 ? 4 : 0)}px)`,
  };
}
