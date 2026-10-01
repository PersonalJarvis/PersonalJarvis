import type { PaneMovePosition, TerminalState } from "@/lib/agenticIdeApi";
import { isSplit, treeLayout, treeLeaves, type LayoutNode, type PaneBox } from "./treeLayout";

export const DOCK_LABELS: Record<PaneMovePosition, string> = {
  swap: "Swap positions", left: "Place on the left", right: "Place on the right",
  above: "Place above", below: "Place below",
};

/**
 * The largest grid a workspace draws, and so how many agents it holds. Mirrors
 * `MAX_GRID_COLUMNS` / `MAX_GRID_ROWS` in `jarvis/agentic_ide/session.py`; the
 * live count limit comes from the server's `max_terminals`.
 */
export const MAX_GRID_COLUMNS = 4;
export const MAX_GRID_ROWS = 4;
export const MAX_WORKSPACE_PANES = MAX_GRID_COLUMNS * MAX_GRID_ROWS;
export const GRID_LIMIT_HINT = `A workspace holds at most ${MAX_GRID_COLUMNS} columns and ${MAX_GRID_ROWS} rows.`;

/**
 * Columns of the even grid: two panes share a row, three to eight use two
 * rows, beyond that one more row per four panes. Mirrors `balanced_columns`.
 */
export function balancedColumns(count: number): number {
  if (count <= 2) return Math.max(1, count);
  const rows = count <= 2 * MAX_GRID_COLUMNS ? 2 : Math.ceil(count / MAX_GRID_COLUMNS);
  return Math.min(MAX_GRID_COLUMNS, Math.ceil(count / rows));
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
  return tree && fitsWorkspace(tree) && leaves.length === keys.length && new Set(leaves).size === keys.length && leaves.every((key) => keys.includes(key))
    ? tree : balancedLayout(keys);
}

export function layoutSpan(tree: LayoutNode | null, axis: "row" | "column"): number {
  if (!tree) return 0;
  if (!isSplit(tree)) return 1;
  const spans = tree.children.map((child) => layoutSpan(child, axis));
  return tree.direction === axis ? spans.reduce((a, b) => a + b, 0) : Math.max(0, ...spans);
}

export function fitsWorkspace(tree: LayoutNode | null): boolean {
  return layoutSpan(tree, "row") <= MAX_GRID_COLUMNS && layoutSpan(tree, "column") <= MAX_GRID_ROWS;
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

/** Whether one more pane split off `anchorKey` stays within the pane limit and the grid. */
export function canSplitFit(
  tree: LayoutNode | null | undefined,
  terminals: readonly Pick<TerminalState, "key">[],
  anchorKey: string | undefined,
  direction: "right" | "down" | "left" | "above",
  maxPanes: number = MAX_WORKSPACE_PANES,
): boolean {
  if (terminals.length >= maxPanes) return false;
  if (terminals.length === 0 || !anchorKey) return true;
  const current = workspaceLayout(tree, terminals);
  if (!current) return true;
  const preview = previewSplit(current, anchorKey, "__preview__", direction);
  return fitsWorkspace(preview);
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
