import { describe, expect, it } from "vitest";
import { BALANCED_GRID_COLUMNS, balancedColumns, balancedLayout, dockPosition, layoutSpan, previewDock, workspaceLayout } from "./workspaceDocking";
import { treeLayout, treeLeaves } from "./treeLayout";

describe("workspace docking geometry", () => {
  it("balances defaults from one through eight without exceeding two rows", () => {
    for (let count = 1; count <= 8; count++) {
      const tree = balancedLayout(Array.from({ length: count }, (_, i) => String(i)));
      expect(layoutSpan(tree, "row")).toBe(count <= 2 ? count : Math.ceil(count / 2));
      expect(layoutSpan(tree, "column")).toBe(count <= 2 ? 1 : 2);
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

  it("grows a row at a time up to sixteen panes, then about square", () => {
    // Same table as `test_an_even_grid_grows_square_past_sixteen`.
    const expected: [number, number, number][] = [
      [1, 1, 1], [2, 2, 1], [6, 3, 2], [8, 4, 2], [9, 3, 3], [12, 4, 3], [16, 4, 4],
      [17, 5, 4], [20, 5, 4], [25, 5, 5], [30, 6, 5], [100, 10, 10],
    ];
    for (const [count, columns, rows] of expected) {
      expect(balancedColumns(count)).toBe(columns);
      const tree = balancedLayout(Array.from({ length: count }, (_, i) => String(i)));
      expect(layoutSpan(tree, "row")).toBe(columns);
      expect(layoutSpan(tree, "column")).toBe(rows);
    }
    expect(BALANCED_GRID_COLUMNS).toBe(4);
  });

  it("docks a pane on any side, however wide or tall the workspace already is", () => {
    const keys = Array.from({ length: 16 }, (_, i) => `p${i}`);
    const full = balancedLayout(keys)!;
    const fifthRow = previewDock(full, "p4", "p1", "above");
    const fifthColumn = previewDock(full, "p4", "p1", "left");
    expect(layoutSpan(fifthRow, "column")).toBe(5);
    expect(layoutSpan(fifthColumn, "row")).toBe(5);
    expect(treeLeaves(fifthColumn).sort()).toEqual([...keys].sort());
    // What the grid draws is the docked tree itself, never a re-dealt grid.
    expect(workspaceLayout(fifthColumn, keys.map((key) => ({ key })))).toBe(fifthColumn);
  });

  it("distinguishes the four edges from the center regardless of card aspect ratio", () => {
    const rect = { left: 10, top: 20, width: 800, height: 400 };
    expect(dockPosition(20, 220, rect)).toBe("left");
    expect(dockPosition(800, 220, rect)).toBe("right");
    expect(dockPosition(410, 25, rect)).toBe("above");
    expect(dockPosition(410, 415, rect)).toBe("below");
    expect(dockPosition(410, 220, rect)).toBe("swap");
  });

  it("draws a wide saved row as it was saved", () => {
    const terminals = Array.from({ length: 6 }, (_, i) => ({ key: `t${i}` }));
    const saved = { direction: "row" as const, children: terminals.map(({ key }) => ({ pane: key })), weights: terminals.map(() => 1) };
    const rendered = workspaceLayout(saved, terminals);
    expect(layoutSpan(rendered, "row")).toBe(6);
    expect(layoutSpan(rendered, "column")).toBe(1);
    expect(treeLeaves(rendered)).toEqual(terminals.map((terminal) => terminal.key));
  });

  it("falls back to the even grid only when the saved tree does not match the panes", () => {
    const terminals = Array.from({ length: 3 }, (_, i) => ({ key: `t${i}` }));
    const stale = { direction: "row" as const, children: [{ pane: "t0" }, { pane: "gone" }], weights: [1, 1] };
    expect(treeLeaves(workspaceLayout(stale, terminals))).toEqual(["t0", "t1", "t2"]);
  });
});
