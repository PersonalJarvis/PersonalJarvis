/**
 * The split tree's client half: the geometry it draws, and the weight edits
 * a drag makes. Structure edits are the backend's (`layout_tree.py`) — these
 * tests cover what the GRID does with the tree it is handed.
 */
import { describe, expect, it } from "vitest";

import {
  dragSeam,
  evenSeam,
  evenedTree,
  isEvenTree,
  treeLayout,
  treeLeaves,
  type LayoutNode,
  type LayoutSplit,
} from "./treeLayout";

const panes = (...keys: string[]) =>
  keys.map((key) => ({ key, name: key.toUpperCase() }));

/** The reported drawing: T1|T3 in the top half, T2 full width below. */
const drawnShape: LayoutNode = {
  direction: "column",
  children: [
    {
      direction: "row",
      children: [{ pane: "t1" }, { pane: "t3" }],
      weights: [1, 1],
    },
    { pane: "t2" },
  ],
  weights: [1, 1],
};

describe("treeLayout", () => {
  it("draws the split-beside-the-top-pane shape the flat grid never could", () => {
    const { boxes } = treeLayout(drawnShape, panes("t1", "t3", "t2"));
    expect(boxes[0]).toEqual({ x: 0, y: 0, w: 0.5, h: 0.5 }); // t1 top-left
    expect(boxes[1]).toEqual({ x: 0.5, y: 0, w: 0.5, h: 0.5 }); // t3 top-right
    expect(boxes[2]).toEqual({ x: 0, y: 0.5, w: 1, h: 0.5 }); // t2 FULL width
  });

  it("puts a seam on every sibling boundary, addressed by container path", () => {
    const { seams } = treeLayout(drawnShape, panes("t1", "t3", "t2"));
    expect(seams).toHaveLength(2);
    const vertical = seams.find((seam) => seam.orientation === "vertical");
    const horizontal = seams.find((seam) => seam.orientation === "horizontal");
    expect(vertical).toMatchObject({ path: [0], boundary: 1, x: 0.5, h: 0.5 });
    expect(horizontal).toMatchObject({ path: [], boundary: 1, y: 0.5, w: 1 });
    expect(vertical?.label).toContain("T1");
    expect(vertical?.label).toContain("T3");
  });

  it("sizes children by their weights", () => {
    const tree: LayoutNode = {
      direction: "row",
      children: [{ pane: "t1" }, { pane: "t2" }],
      weights: [3, 1],
    };
    const { boxes } = treeLayout(tree, panes("t1", "t2"));
    expect(boxes[0]?.w).toBeCloseTo(0.75);
    expect(boxes[1]?.x).toBeCloseTo(0.75);
    expect(boxes[1]?.w).toBeCloseTo(0.25);
  });

  it("treats missing or junk weights as an even share", () => {
    const tree: LayoutNode = {
      direction: "row",
      children: [{ pane: "t1" }, { pane: "t2" }],
      weights: [Number.NaN],
    };
    const { boxes } = treeLayout(tree, panes("t1", "t2"));
    expect(boxes[0]?.w).toBeCloseTo(0.5);
  });

  it("falls back to an even row when the tree does not cover the panes", () => {
    const stale: LayoutNode = { pane: "ghost" };
    const { boxes, seams } = treeLayout(stale, panes("t1", "t2"));
    expect(boxes[0]).toEqual({ x: 0, y: 0, w: 0.5, h: 1 });
    expect(boxes[1]).toEqual({ x: 0.5, y: 0, w: 0.5, h: 1 });
    expect(seams).toHaveLength(1);
  });

  it("lays a lone pane out as the whole workspace", () => {
    const { boxes, seams } = treeLayout(null, panes("t1"));
    expect(boxes[0]).toEqual({ x: 0, y: 0, w: 1, h: 1 });
    expect(seams).toEqual([]);
  });

  it("reads leaves left-to-right, top-to-bottom", () => {
    expect(treeLeaves(drawnShape)).toEqual(["t1", "t3", "t2"]);
  });
});

describe("dragSeam", () => {
  const row: LayoutNode = {
    direction: "row",
    children: [{ pane: "t1" }, { pane: "t2" }],
    weights: [1, 1],
  };

  it("trades weight between the two neighbours and nobody else", () => {
    const { seams } = treeLayout(row, panes("t1", "t2"));
    const dragged = dragSeam(row, seams[0], 250, 1000) as LayoutSplit;
    expect(dragged.weights[0]).toBeCloseTo(1.5);
    expect(dragged.weights[1]).toBeCloseTo(0.5);
  });

  it("never drags a pane below its pixel floor", () => {
    const { seams } = treeLayout(row, panes("t1", "t2"));
    const dragged = dragSeam(row, seams[0], 100000, 1000) as LayoutSplit;
    // 120 px of 1000 is 0.24 of the pair's combined weight of 2.
    expect(dragged.weights[1]).toBeCloseTo(0.24);
  });

  it("converts pixels against the container's own span, not the window's", () => {
    // The stack occupies the right HALF, so its seam divides 500 of 1000 px.
    const nested: LayoutNode = {
      direction: "row",
      children: [
        { pane: "t1" },
        {
          direction: "column",
          children: [{ pane: "t2" }, { pane: "t3" }],
          weights: [1, 1],
        },
      ],
      weights: [1, 1],
    };
    const { seams } = treeLayout(nested, panes("t1", "t2", "t3"));
    const inner = seams.find((seam) => seam.orientation === "horizontal");
    expect(inner?.axisFraction).toBeCloseTo(1); // full height…
    const vertical = seams.find((seam) => seam.orientation === "vertical");
    expect(vertical?.axisFraction).toBeCloseTo(1); // …and full width at root
  });

  it("changes nothing when the workspace reshaped under the gesture", () => {
    const { seams } = treeLayout(row, panes("t1", "t2"));
    const reshaped: LayoutNode = { pane: "t1" };
    expect(dragSeam(reshaped, seams[0], 100, 1000)).toBe(reshaped);
  });

  it("leaves the tree it was given untouched", () => {
    const { seams } = treeLayout(row, panes("t1", "t2"));
    dragSeam(row, seams[0], 250, 1000);
    expect((row as LayoutSplit).weights).toEqual([1, 1]);
  });
});

describe("evening out", () => {
  it("evenSeam levels exactly the two neighbours", () => {
    const tree: LayoutNode = {
      direction: "row",
      children: [{ pane: "t1" }, { pane: "t2" }, { pane: "t3" }],
      weights: [3, 1, 2],
    };
    const { seams } = treeLayout(tree, panes("t1", "t2", "t3"));
    const levelled = evenSeam(tree, seams[0]) as LayoutSplit;
    expect(levelled.weights).toEqual([2, 2, 2]);
    expect((tree as LayoutSplit).weights).toEqual([3, 1, 2]);
  });

  it("evenedTree resets every container", () => {
    const dragged: LayoutNode = {
      direction: "column",
      children: [
        {
          direction: "row",
          children: [{ pane: "t1" }, { pane: "t3" }],
          weights: [3, 1],
        },
        { pane: "t2" },
      ],
      weights: [5, 1],
    };
    expect(isEvenTree(dragged)).toBe(false);
    const evened = evenedTree(dragged);
    expect(isEvenTree(evened)).toBe(true);
  });

  it("isEvenTree calls equal weights even whatever their scale", () => {
    const tree: LayoutNode = {
      direction: "row",
      children: [{ pane: "t1" }, { pane: "t2" }],
      weights: [7, 7],
    };
    expect(isEvenTree(tree)).toBe(true);
  });

  /**
   * The reported workspace of 2026-08-12: a row whose middle child is a
   * NESTED group of four terminals (two side-by-side stacks). Equal container
   * weights drew the grouped terminals at half the width of their neighbours,
   * yet the old per-container check called that "even" and disabled the
   * button — the click the report says "doesn't work" never ran.
   */
  const nestedWorkspace: LayoutNode = {
    direction: "row",
    children: [
      {
        direction: "column",
        children: [{ pane: "t2" }, { pane: "t8" }],
        weights: [1, 1],
      },
      { pane: "t1" },
      {
        direction: "column",
        children: [
          {
            direction: "row",
            children: [{ pane: "t3" }, { pane: "t6" }],
            weights: [1, 1],
          },
          {
            direction: "row",
            children: [{ pane: "t4" }, { pane: "t5" }],
            weights: [1, 1],
          },
        ],
        weights: [1, 1],
      },
      {
        direction: "column",
        children: [{ pane: "t9" }, { pane: "t11" }],
        weights: [1, 1],
      },
    ],
    weights: [1, 1, 1, 1],
  };

  it("calls a nested group at container-equal weights UNEVEN", () => {
    expect(isEvenTree(nestedWorkspace)).toBe(false);
  });

  it("evens a nested group to equal TERMINAL widths, arrangement untouched", () => {
    const evened = evenedTree(nestedWorkspace);
    expect(isEvenTree(evened)).toBe(true);
    expect(treeLeaves(evened)).toEqual(treeLeaves(nestedWorkspace));
    const { boxes } = treeLayout(
      evened,
      panes("t2", "t8", "t1", "t3", "t6", "t4", "t5", "t9", "t11"),
    );
    // Five stripes across: t2-stack, t1, t3, t6, t9-stack — every terminal
    // one fifth wide, including the four inside the nested group.
    for (const box of boxes) expect(box?.w).toBeCloseTo(0.2);
    // And the stacks still split at the middle.
    const t3 = boxes[3];
    expect(t3?.h).toBeCloseTo(0.5);
  });

  it("evenSeam levels the TERMINALS beside a nested group, not its wrapper", () => {
    const { seams } = treeLayout(
      nestedWorkspace,
      panes("t2", "t8", "t1", "t3", "t6", "t4", "t5", "t9", "t11"),
    );
    // The root seam between t1 and the nested group (children 1 and 2).
    const seam = seams.find(
      (candidate) => candidate.path.length === 0 && candidate.boundary === 2,
    );
    expect(seam).toBeDefined();
    const levelled = evenSeam(nestedWorkspace, seam!) as LayoutSplit;
    // Their combined weight of 2 splits one third / two thirds: the group
    // lines up two terminals, so each of the three ends up the same width.
    expect(levelled.weights[1]).toBeCloseTo(2 / 3);
    expect(levelled.weights[2]).toBeCloseTo(4 / 3);
  });
});
