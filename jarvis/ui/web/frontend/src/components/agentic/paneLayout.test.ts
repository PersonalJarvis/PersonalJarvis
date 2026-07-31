import { describe, expect, it } from "vitest";
import {
  dragSeam,
  evenSeam,
  evenWeights,
  MIN_SEAM_PANE_PX,
  paneArrangement,
  paneLayout,
  panesFromArrangement,
  remapColumnWeights,
  sameArrangement,
  weightsAfterSplit,
  type PaneSeam,
  type PaneWeights,
  type Sized,
} from "./paneLayout";

/** A pane, the way the workspace state carries one. */
const pane = (name: string, column: number, slot = 0): Sized => ({ name, column, slot });

/** Rounded to the nearest hundredth — fractions carry float noise. */
const round = (value: number) => Math.round(value * 100) / 100;

function seamOf(seams: PaneSeam[], id: string): PaneSeam {
  const found = seams.find((seam) => seam.id === id);
  if (!found) throw new Error(`no seam ${id} in ${seams.map((s) => s.id).join(", ")}`);
  return found;
}

describe("paneLayout", () => {
  it("splits an untouched workspace evenly, which is where it starts", () => {
    const { boxes } = paneLayout([pane("A", 0), pane("B", 1), pane("C", 2)], 10);
    expect(boxes.map((box) => round(box.w))).toEqual([0.33, 0.33, 0.33]);
    expect(boxes.map((box) => round(box.x))).toEqual([0, 0.33, 0.67]);
    expect(boxes.every((box) => box.h === 1)).toBe(true);
  });

  it("gives a column its weight and leaves every other column alone", () => {
    // The whole point of weights: the middle column is twice as wide, and the
    // two beside it are unchanged relative to each other.
    const weights: PaneWeights = { ...evenWeights(), columns: [1, 2, 1] };
    const { boxes } = paneLayout([pane("A", 0), pane("B", 1), pane("C", 2)], 10, weights);
    expect(boxes.map((box) => round(box.w))).toEqual([0.25, 0.5, 0.25]);
  });

  it("stacks a column's panes by their own weights", () => {
    const weights: PaneWeights = { ...evenWeights(), panes: { B: 3 } };
    const { boxes } = paneLayout(
      [pane("A", 0), pane("B", 0, 1), pane("C", 0, 2)],
      10,
      weights,
    );
    expect(boxes.map((box) => round(box.h))).toEqual([0.2, 0.6, 0.2]);
    expect(boxes.map((box) => round(box.y))).toEqual([0, 0.2, 0.8]);
  });

  it("lets two bands have DIFFERENT column widths", () => {
    // A CSS grid cannot express this — `grid-template-columns` is shared by
    // every row — and that limitation is why the workspace stopped being one.
    const panes = Array.from({ length: 4 }, (_, i) => pane(`T${i}`, i));
    const weights: PaneWeights = { ...evenWeights(), columns: [3, 1, 1, 1] };
    const { boxes } = paneLayout(panes, 2, weights);
    expect(round(boxes[0].w)).toBe(0.75);
    expect(round(boxes[1].w)).toBe(0.25);
    // The second band never saw the first band's weights.
    expect(round(boxes[2].w)).toBe(0.5);
    expect(round(boxes[3].w)).toBe(0.5);
  });

  it("offers a seam for every boundary and none for an edge", () => {
    const { seams } = paneLayout(
      [pane("A", 0), pane("B", 0, 1), pane("C", 1)],
      10,
    );
    expect(seams.map((seam) => seam.id).sort()).toEqual(["column:0:1", "pane:A:B"]);
    expect(seamOf(seams, "column:0:1").orientation).toBe("vertical");
    expect(seamOf(seams, "pane:A:B").orientation).toBe("horizontal");
  });

  it("offers a seam between two bands", () => {
    const panes = Array.from({ length: 4 }, (_, i) => pane(`T${i}`, i));
    const { seams } = paneLayout(panes, 2);
    const band = seamOf(seams, "band:0:1");
    expect(band.orientation).toBe("horizontal");
    expect(band.y).toBe(0.5);
    expect(band.w).toBe(1);
  });

  it("reports the height below which the workspace scrolls instead of shrinking", () => {
    // Two bands whose tallest columns hold 2 and 1 panes: three minimum pane
    // heights, not the six a least-common-multiple row grid used to demand.
    const panes = [
      pane("A", 0),
      pane("B", 0, 1),
      pane("C", 1),
      pane("D", 2),
      pane("E", 3),
    ];
    expect(paneLayout(panes, 2).minHeightUnits).toBe(3);
  });

  it("closes gaps in column numbers rather than rendering a blank stripe", () => {
    const { boxes } = paneLayout([pane("A", 0), pane("B", 7)], 10);
    expect(boxes.map((box) => round(box.x))).toEqual([0, 0.5]);
  });

  it("has nothing to lay out for an empty workspace", () => {
    expect(paneLayout([], 10)).toEqual({
      boxes: [],
      seams: [],
      bands: 0,
      minHeightUnits: 0,
    });
  });
});

describe("dragSeam", () => {
  const twoColumns = paneLayout([pane("A", 0), pane("B", 1)], 10);
  const seam = seamOf(twoColumns.seams, "column:0:1");

  it("moves width from one side to the other and nowhere else", () => {
    // 1000 px wide, dragged 250 px right: the left pane takes three quarters.
    const next = dragSeam(evenWeights(), seam, 250, 1000);
    const { boxes } = paneLayout([pane("A", 0), pane("B", 1)], 10, next);
    expect(round(boxes[0].w)).toBe(0.75);
    expect(round(boxes[1].w)).toBe(0.25);
  });

  it("leaves a third column untouched while its neighbours trade", () => {
    const panes = [pane("A", 0), pane("B", 1), pane("C", 2)];
    const three = paneLayout(panes, 10);
    // 900 px wide, dragged 300 px right: A would swallow B whole, so B stops at
    // its 120 px minimum and A takes the rest of the two-thirds they share.
    const next = dragSeam(evenWeights(), seamOf(three.seams, "column:0:1"), 300, 900);
    const { boxes } = paneLayout(panes, 10, next);
    expect(round(boxes[0].w)).toBe(0.53);
    expect(round(boxes[1].w)).toBe(0.13);
    // The one that was not part of the drag keeps exactly its third.
    expect(round(boxes[2].w)).toBe(0.33);
  });

  it("stops a pane at the minimum instead of dragging it out of existence", () => {
    // A control that can be dragged to nothing has no way back.
    const next = dragSeam(evenWeights(), seam, 100000, 1000);
    const { boxes } = paneLayout([pane("A", 0), pane("B", 1)], 10, next);
    expect(Math.round(boxes[1].w * 1000)).toBe(MIN_SEAM_PANE_PX);
  });

  it("shares evenly when neither side can meet the minimum", () => {
    const next = dragSeam(evenWeights(), seam, 500, MIN_SEAM_PANE_PX);
    const { boxes } = paneLayout([pane("A", 0), pane("B", 1)], 10, next);
    expect(round(boxes[0].w)).toBe(0.5);
  });

  it("converts pixels against the BAND's height, not the window's", () => {
    // Two panes stacked in a column of a two-band workspace share half the
    // height, so 100 px is twice the fraction it would be across the whole one.
    const panes = [pane("A", 0), pane("B", 0, 1), pane("C", 1), pane("D", 2)];
    const layout = paneLayout(panes, 2);
    const stacked = seamOf(layout.seams, "pane:A:B");
    expect(stacked.axisFraction).toBe(0.5);
    const next = dragSeam(evenWeights(), stacked, 100, 1000);
    const { boxes } = paneLayout(panes, 2, next);
    // 100 px of a 500 px band is a fifth, moved from B to A.
    expect(round(boxes[0].h / 0.5)).toBe(0.7);
  });

  it("does nothing when the workspace has not been measured yet", () => {
    expect(dragSeam(evenWeights(), seam, 40, 0)).toEqual(evenWeights());
  });
});

describe("evenSeam", () => {
  it("makes two neighbours equal without disturbing the rest", () => {
    const panes = [pane("A", 0), pane("B", 1), pane("C", 2)];
    const seams = paneLayout(panes, 10).seams;
    const lopsided: PaneWeights = { ...evenWeights(), columns: [3, 1, 4] };
    const next = evenSeam(lopsided, seamOf(seams, "column:0:1"));
    expect(next.columns).toEqual([2, 2, 4]);
  });
});

describe("weightsAfterSplit", () => {
  it("halves the pane it was asked of and touches nothing else", () => {
    // The complaint this fixes: opening a terminal beside a pane used to make
    // every other pane on the line narrower.
    const before = [pane("A", 0), pane("B", 1), pane("C", 2)];
    const after = [pane("A", 0), pane("New", 1), pane("B", 2), pane("C", 3)];
    const next = weightsAfterSplit(evenWeights(), before, after, "A", "New", 10);
    const { boxes } = paneLayout(after, 10, next);
    // A held a third of the line; it and the pane split off it now hold a sixth
    // each — the same third between them.
    expect(round(boxes[0].w)).toBe(0.17);
    expect(round(boxes[1].w)).toBe(0.17);
    // ...and B and C are still exactly the third they each were.
    expect(round(boxes[2].w)).toBe(0.33);
    expect(round(boxes[3].w)).toBe(0.33);
  });

  it("halves a column that had already been dragged wide", () => {
    const before = [pane("A", 0), pane("B", 1)];
    const after = [pane("A", 0), pane("New", 1), pane("B", 2)];
    const wide: PaneWeights = { ...evenWeights(), columns: [3, 1] };
    const next = weightsAfterSplit(wide, before, after, "A", "New", 10);
    expect(next.columns).toEqual([1.5, 1.5, 1]);
  });

  it("halves the anchor's HEIGHT when the split went downwards", () => {
    const before = [pane("A", 0), pane("B", 1)];
    const after = [pane("A", 0), pane("New", 0, 1), pane("B", 1)];
    const next = weightsAfterSplit(evenWeights(), before, after, "A", "New", 10);
    const { boxes } = paneLayout(after, 10, next);
    expect(round(boxes[0].h)).toBe(0.5);
    expect(round(boxes[1].h)).toBe(0.5);
    // The pane beside them keeps its full height — that is what makes a split
    // down local to its own column.
    expect(boxes[2].h).toBe(1);
  });

  it("splits a stacked pane in half without resizing its neighbours", () => {
    const before = [pane("A", 0), pane("B", 0, 1)];
    const after = [pane("A", 0), pane("B", 0, 1), pane("New", 0, 2)];
    const next = weightsAfterSplit(evenWeights(), before, after, "B", "New", 10);
    const { boxes } = paneLayout(after, 10, next);
    expect(round(boxes[0].h)).toBe(0.5);
    expect(round(boxes[1].h)).toBe(0.25);
    expect(round(boxes[2].h)).toBe(0.25);
  });

  it("leaves the anchor alone when the new column wrapped onto another band", () => {
    // Halving would be a lie there: the two are not sharing a line, so the
    // anchor would just end up oddly narrow beside columns it never split from.
    const before = [pane("A", 0), pane("B", 1)];
    const after = [pane("A", 0), pane("New", 1), pane("B", 2)];
    const next = weightsAfterSplit(evenWeights(), before, after, "A", "New", 2);
    const { boxes } = paneLayout(after, 2, next);
    expect(round(boxes[0].w)).toBe(0.5);
    expect(round(boxes[1].w)).toBe(0.5);
  });
});

describe("remapColumnWeights", () => {
  it("keeps a dragged width on the column that kept its panes", () => {
    // The backend packs column numbers on every close, so column 2 afterwards
    // is not the column 2 that was dragged wide.
    const before = [pane("A", 0), pane("B", 1), pane("C", 2)];
    const after = [pane("A", 0), pane("C", 1)];
    const wide: PaneWeights = { ...evenWeights(), columns: [1, 1, 4] };
    expect(remapColumnWeights(wide, before, after).columns).toEqual([1, 4]);
  });

  it("starts a column nobody has seen before at the even width", () => {
    const before = [pane("A", 0)];
    const after = [pane("A", 0), pane("New", 1)];
    const wide: PaneWeights = { ...evenWeights(), columns: [4] };
    expect(remapColumnWeights(wide, before, after).columns).toEqual([4, 1]);
  });

  it("forgets the height of a pane that is gone", () => {
    const before = [pane("A", 0), pane("B", 0, 1)];
    const after = [pane("A", 0)];
    const weights: PaneWeights = { ...evenWeights(), panes: { A: 2, B: 3 } };
    expect(remapColumnWeights(weights, before, after).panes).toEqual({ A: 2 });
  });
});

describe("paneArrangement", () => {
  it("maps each pane to its column index with gaps closed", () => {
    // Backend column NUMBERS may have holes mid-close; the weights are keyed
    // by packed index, so the arrangement must speak the same language.
    const panes = [pane("A", 0), pane("B", 4), pane("C", 4, 1)];
    expect(paneArrangement(panes)).toEqual({ A: 0, B: 1, C: 1 });
  });

  it("round-trips through panesFromArrangement into remapColumnWeights", () => {
    // The restart story: only the arrangement survived (as stored JSON), the
    // pane objects did not — the synthesized list must carry a width the same
    // way the live list would have.
    const stored = paneArrangement([pane("A", 0), pane("B", 1), pane("C", 2)]);
    const wide: PaneWeights = { ...evenWeights(), columns: [1, 1, 4] };
    const after = [pane("B", 0), pane("C", 1)];
    expect(
      remapColumnWeights(wide, panesFromArrangement(stored), after).columns,
    ).toEqual([1, 4]);
  });
});

describe("sameArrangement", () => {
  it("accepts identical mappings and rejects moved or exchanged panes", () => {
    expect(sameArrangement({ A: 0, B: 1 }, { B: 1, A: 0 })).toBe(true);
    expect(sameArrangement({ A: 0, B: 1 }, { A: 0, B: 2 })).toBe(false);
    expect(sameArrangement({ A: 0 }, { A: 0, B: 1 })).toBe(false);
    expect(sameArrangement({ A: 0, B: 1 }, { A: 0, C: 1 })).toBe(false);
  });
});
