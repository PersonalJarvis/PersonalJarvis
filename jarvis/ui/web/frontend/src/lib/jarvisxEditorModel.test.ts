import { describe, expect, it } from "vitest";

import {
  EMPTY_DOC,
  addShape,
  canRedo,
  canUndo,
  commit,
  createHistory,
  cropFromDrag,
  deleteShape,
  exportSize,
  finishGesture,
  fitScale,
  hitHandle,
  hitTest,
  imageToView,
  isMeaningful,
  moveShape,
  nextCounterNumber,
  redo,
  replacePresent,
  resizeShape,
  restyleShape,
  setCrop,
  stepZoom,
  toolForKey,
  undo,
  updateShape,
  viewToImage,
  visibleArea,
  type BoxShape,
  type CounterShape,
  type SegmentShape,
  type Shape,
} from "@/lib/jarvisxEditorModel";

const style = { color: "#ff3b30", width: 4, shadow: false };

function box(id: string, x: number, y: number, w: number, h: number, kind: BoxShape["kind"] = "rect"): BoxShape {
  return { id, kind, x, y, w, h, ...style };
}

function counter(id: string, n: number): CounterShape {
  return { id, kind: "counter", x: 10 * n, y: 10, n, ...style };
}

describe("shape edits", () => {
  it("adds, moves, resizes and deletes shapes without mutating the old document", () => {
    const doc0 = EMPTY_DOC;
    const doc1 = addShape(doc0, box("a", 10, 10, 100, 50));
    expect(doc0.shapes).toHaveLength(0);
    expect(doc1.shapes).toHaveLength(1);

    const doc2 = updateShape(doc1, "a", (s) => moveShape(s, 5, -5));
    expect(doc2.shapes[0]).toMatchObject({ x: 15, y: 5, w: 100, h: 50 });
    expect(doc1.shapes[0]).toMatchObject({ x: 10, y: 10 });

    const doc3 = updateShape(doc2, "a", (s) => resizeShape(s, "se", { x: 215, y: 105 }));
    expect(doc3.shapes[0]).toMatchObject({ x: 15, y: 5, w: 200, h: 100 });

    expect(deleteShape(doc3, "a").shapes).toHaveLength(0);
    // Deleting something that is not there changes nothing.
    expect(deleteShape(doc3, "missing")).toBe(doc3);
  });

  it("normalises a box dragged through its opposite corner", () => {
    const flipped = resizeShape(box("a", 100, 100, 50, 50), "nw", { x: 200, y: 180 });
    expect(flipped).toMatchObject({ x: 150, y: 150, w: 50, h: 30 });
  });

  it("moves both ends of a segment and drags one end by its handle", () => {
    const arrow: SegmentShape = { id: "s", kind: "arrow", x1: 0, y1: 0, x2: 100, y2: 0, ...style };
    expect(moveShape(arrow, 10, 20)).toMatchObject({ x1: 10, y1: 20, x2: 110, y2: 20 });
    expect(resizeShape(arrow, "end", { x: 50, y: 50 })).toMatchObject({ x1: 0, y1: 0, x2: 50, y2: 50 });
    expect(hitHandle(arrow, { x: 99, y: 1 }, 4)).toBe("end");
  });

  it("moves every point of a freehand stroke", () => {
    const pen: Shape = { id: "p", kind: "pen", points: [{ x: 0, y: 0 }, { x: 5, y: 5 }], ...style };
    expect(moveShape(pen, 1, 2)).toMatchObject({ points: [{ x: 1, y: 2 }, { x: 6, y: 7 }] });
  });

  it("restyles only what a shape uses", () => {
    const blur = box("b", 0, 0, 10, 10, "blur");
    expect(restyleShape(blur, { color: "#ffffff" }).color).toBe(style.color);
    const text: Shape = { id: "t", kind: "text", x: 0, y: 0, text: "hi", fontSize: 16, ...style };
    expect(restyleShape(text, { fontSize: 48 })).toMatchObject({ fontSize: 48 });
    expect(restyleShape(box("r", 0, 0, 5, 5), { fontSize: 48 })).not.toHaveProperty("fontSize");
  });

  it("drops a click that never became a shape", () => {
    expect(isMeaningful(box("a", 0, 0, 1, 1))).toBe(false);
    expect(isMeaningful(box("a", 0, 0, 20, 20))).toBe(true);
    expect(isMeaningful({ id: "t", kind: "text", x: 0, y: 0, text: "  ", fontSize: 16, ...style })).toBe(false);
  });
});

describe("hit testing", () => {
  const shapes: Shape[] = [box("under", 0, 0, 200, 200, "highlight"), box("outline", 50, 50, 100, 100)];

  it("grabs a hollow rectangle by its outline only", () => {
    expect(hitTest(shapes, { x: 50, y: 100 }, 3)).toBe("outline");
    // Inside the hollow rect, the filled highlight underneath is what is hit.
    expect(hitTest(shapes, { x: 100, y: 100 }, 3)).toBe("under");
    expect(hitTest(shapes, { x: 400, y: 400 }, 3)).toBeNull();
  });

  it("returns the topmost shape", () => {
    const stacked = [box("a", 0, 0, 50, 50, "highlight"), box("b", 0, 0, 50, 50, "highlight")];
    expect(hitTest(stacked, { x: 25, y: 25 }, 2)).toBe("b");
  });
});

describe("counters", () => {
  it("numbers the next badge one past the highest", () => {
    expect(nextCounterNumber([])).toBe(1);
    expect(nextCounterNumber([counter("a", 1), counter("b", 2)])).toBe(3);
    // A deleted middle badge does not get its number reused while a higher one exists.
    expect(nextCounterNumber([counter("a", 1), counter("c", 3)])).toBe(4);
  });
});

describe("history", () => {
  it("undoes and redoes whole edits", () => {
    let h = createHistory();
    h = commit(h, addShape(h.present, box("a", 0, 0, 10, 10)));
    h = commit(h, addShape(h.present, box("b", 0, 0, 10, 10)));
    expect(h.present.shapes.map((s) => s.id)).toEqual(["a", "b"]);
    h = undo(h);
    expect(h.present.shapes.map((s) => s.id)).toEqual(["a"]);
    expect(canRedo(h)).toBe(true);
    h = redo(h);
    expect(h.present.shapes.map((s) => s.id)).toEqual(["a", "b"]);
    h = undo(undo(h));
    expect(h.present.shapes).toHaveLength(0);
    expect(canUndo(h)).toBe(false);
    // Undo at the start is a no-op.
    expect(undo(h)).toBe(h);
  });

  it("records a drag as ONE step however many frames it painted", () => {
    let h = createHistory(addShape(EMPTY_DOC, box("a", 0, 0, 10, 10)));
    const before = h.present;
    for (let i = 1; i <= 5; i += 1) {
      h = replacePresent(h, updateShape(h.present, "a", (s) => moveShape(s, 1, 0)));
    }
    expect(h.past).toHaveLength(0);
    h = finishGesture(h, before);
    expect(h.past).toHaveLength(1);
    expect(h.present.shapes[0]).toMatchObject({ x: 5 });
    expect(undo(h).present.shapes[0]).toMatchObject({ x: 0 });
  });

  it("a new edit clears the redo stack", () => {
    let h = createHistory();
    h = commit(h, addShape(h.present, box("a", 0, 0, 10, 10)));
    h = undo(h);
    h = commit(h, addShape(h.present, box("b", 0, 0, 10, 10)));
    expect(canRedo(h)).toBe(false);
  });
});

describe("crop, zoom and export math", () => {
  it("clamps a crop to the image and rounds it to whole pixels", () => {
    expect(cropFromDrag({ x: -20, y: 10.4 }, { x: 50.6, y: 500 }, 100, 80)).toEqual({ x: 0, y: 10, w: 51, h: 70 });
    // Dragged backwards: same rect.
    expect(cropFromDrag({ x: 50.6, y: 500 }, { x: -20, y: 10.4 }, 100, 80)).toEqual({ x: 0, y: 10, w: 51, h: 70 });
    // Too small to be a crop.
    expect(cropFromDrag({ x: 10, y: 10 }, { x: 12, y: 12 }, 100, 80)).toBeNull();
  });

  it("exports at native resolution, cropped or not, whatever the zoom", () => {
    expect(exportSize(EMPTY_DOC, 2880, 1800)).toEqual({ width: 2880, height: 1800 });
    const cropped = setCrop(EMPTY_DOC, { x: 100, y: 50, w: 640, h: 360 });
    expect(exportSize(cropped, 2880, 1800)).toEqual({ width: 640, height: 360 });
    expect(visibleArea(cropped, 2880, 1800)).toEqual({ x: 100, y: 50, w: 640, h: 360 });
  });

  it("maps view pixels to image pixels and back through zoom and crop", () => {
    const area = { x: 100, y: 50, w: 640, h: 360 };
    const img = viewToImage({ x: 32, y: 18 }, 0.5, area);
    expect(img).toEqual({ x: 164, y: 86 });
    expect(imageToView(img, 0.5, area)).toEqual({ x: 32, y: 18 });
  });

  it("fits content into the stage without ever enlarging it", () => {
    expect(fitScale(1000, 800, 2000, 1000, 0)).toBe(0.5);
    expect(fitScale(1000, 800, 200, 100, 0)).toBe(1);
  });

  it("steps zoom through the fixed ladder", () => {
    expect(stepZoom(1, 1)).toBe(1.5);
    expect(stepZoom(1, -1)).toBe(0.75);
    expect(stepZoom(0.6, 1)).toBe(0.75);
  });

  it("maps a tool key to its tool", () => {
    expect(toolForKey("r")).toBe("rect");
    expect(toolForKey("N")).toBe("counter");
    expect(toolForKey("q")).toBeNull();
  });
});
