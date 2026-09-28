import { describe, expect, it } from "vitest";
import { MAX_GRID_COLUMNS, MAX_GRID_ROWS, MAX_WORKSPACE_PANES, balancedColumns, balancedLayout, canSplitFit, dockPosition, fitsWorkspace, layoutSpan, previewDock, workspaceLayout } from "./workspaceDocking";
import { treeLayout, treeLeaves } from "./treeLayout";

describe("workspace docking geometry", () => {
  it("balances defaults from one through eight without exceeding two rows", () => {
    for (let count = 1; count <= 8; count++) {
      const tree = balancedLayout(Array.from({ length: count }, (_, i) => String(i)));
      expect(layoutSpan(tree, "row")).toBe(count <= 2 ? count : Math.ceil(count / 2));
      expect(layoutSpan(tree, "column")).toBe(count <= 2 ? 1 : 2);
      expect(fitsWorkspace(tree)).toBe(true);
    }
  });

  it("changes two side-by-side panes to a vertical stack and back without losing identities", () => {
    const panes = [{ key: "a", name: "A" }, { key: "b", name: "B" }];
    const row = balancedLayout(["a", "b"])!;
    const stack = previewDock(row, "a", "b", "below");
    expect(treeLayout(stack, panes).boxes).toEqual([{ x: 0, y: 0.5, w: 1, h: 0.5 }, { x: 0, y: 0, w: 1, h: 0.5 }]);
    const again = previewDock(stack, "a", "b", "right");
    expect(treeLayout(again, panes).boxes).toEqual([{ x: 0.5, y: 0, w: 0.5, h: 1 }, { x: 0, y: 0, w: 0.5, h: 1 }]);
    expect(treeLeaves(row)).toEqual(["a", "b"]);
  });

  it("grows past eight panes a row at a time, never wider than four columns", () => {
    // Same table as `test_an_even_grid_never_grows_wider_than_four_columns`.
    const expected: [number, number, number][] = [[1, 1, 1], [2, 2, 1], [6, 3, 2], [8, 4, 2], [9, 3, 3], [12, 4, 3], [16, 4, 4]];
    for (const [count, columns, rows] of expected) {
      expect(balancedColumns(count)).toBe(columns);
      const tree = balancedLayout(Array.from({ length: count }, (_, i) => String(i)));
      expect(layoutSpan(tree, "row")).toBe(columns);
      expect(layoutSpan(tree, "column")).toBe(rows);
      expect(fitsWorkspace(tree)).toBe(true);
    }
    expect(MAX_WORKSPACE_PANES).toBe(MAX_GRID_COLUMNS * MAX_GRID_ROWS);
  });

  it("allows a third row where eight panes used to be the end", () => {
    const eight = balancedLayout(["a", "b", "c", "d", "e", "f", "g", "h"])!;
    expect(fitsWorkspace(previewDock(eight, "e", "b", "above"))).toBe(true);
    const terminals = ["a", "b", "c", "d", "e", "f", "g", "h"].map((key) => ({ key }));
    expect(canSplitFit(eight, terminals, "a", "down")).toBe(true);
  });

  it("rejects a fifth row or fifth column while allowing swaps in a full workspace", () => {
    const keys = Array.from({ length: 16 }, (_, i) => `p${i}`);
    const full = balancedLayout(keys)!;
    expect(fitsWorkspace(previewDock(full, "p4", "p1", "above"))).toBe(false);
    expect(fitsWorkspace(previewDock(full, "p4", "p1", "left"))).toBe(false);
    expect(fitsWorkspace(previewDock(full, "p0", "p15", "swap"))).toBe(true);
    const terminals = keys.map((key) => ({ key }));
    expect(canSplitFit(full, terminals.slice(0, 15), "p0", "right", 16)).toBe(false);
    expect(canSplitFit(full, terminals, "p0", "down", 16)).toBe(false);
  });

  it("distinguishes the four edges from the center regardless of card aspect ratio", () => {
    const rect = { left: 10, top: 20, width: 800, height: 400 };
    expect(dockPosition(20, 220, rect)).toBe("left");
    expect(dockPosition(800, 220, rect)).toBe("right");
    expect(dockPosition(410, 25, rect)).toBe("above");
    expect(dockPosition(410, 415, rect)).toBe("below");
    expect(dockPosition(410, 220, rect)).toBe("swap");
  });

  it("fits legacy single-row snapshots into the workspace bounds before drawing", () => {
    const terminals = Array.from({ length: 6 }, (_, i) => ({ key: `t${i}` }));
    const saved = { direction: "row" as const, children: terminals.map(({ key }) => ({ pane: key })), weights: terminals.map(() => 1) };
    const rendered = workspaceLayout(saved, terminals);
    expect(layoutSpan(rendered, "row")).toBe(3);
    expect(layoutSpan(rendered, "column")).toBe(2);
    expect(treeLeaves(rendered)).toEqual(terminals.map((terminal) => terminal.key));
  });
});
