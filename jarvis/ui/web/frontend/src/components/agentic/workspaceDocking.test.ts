import { describe, expect, it } from "vitest";
import { balancedLayout, dockPosition, fitsWorkspace, layoutSpan, previewDock, workspaceLayout } from "./workspaceDocking";
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

  it("rejects a third row or fifth column while allowing swaps in a full workspace", () => {
    const full = balancedLayout(["a", "b", "c", "d", "e", "f", "g", "h"])!;
    expect(fitsWorkspace(previewDock(full, "e", "b", "above"))).toBe(false);
    expect(fitsWorkspace(previewDock(full, "e", "b", "left"))).toBe(false);
    expect(fitsWorkspace(previewDock(full, "a", "h", "swap"))).toBe(true);
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
