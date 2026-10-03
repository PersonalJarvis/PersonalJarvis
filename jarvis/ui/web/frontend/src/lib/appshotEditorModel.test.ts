import { describe, expect, it } from "vitest";

import {
  ARROW_STYLES,
  BACKGROUND_PRESETS,
  DEFAULT_BACKGROUND,
  TOOL_KEYS,
  bounds,
  clampRect,
  commit,
  constrainEnd,
  contrastOn,
  emptyHistory,
  frameLayout,
  hitTest,
  isMeaningful,
  nextCounter,
  presetCss,
  rectFrom,
  redo,
  strokeWidths,
  taperedArrowOutline,
  textSize,
  toolForKey,
  translate,
  undo,
  viewport,
  withId,
  type Op,
} from "@/lib/appshotEditorModel";

const pen = withId({ kind: "pen", points: [{ x: 1, y: 1 }], color: "#fff", width: 3 });
const crop = withId({ kind: "crop", rect: { x: 10, y: 10, w: 50, h: 40 } });

describe("appshot editor model", () => {
  it("builds the same rectangle for a drag in any direction", () => {
    expect(rectFrom({ x: 50, y: 40 }, { x: 10, y: 10 })).toEqual({ x: 10, y: 10, w: 40, h: 30 });
  });

  it("clamps a rectangle to the picture and drops one that leaves nothing", () => {
    expect(clampRect({ x: -10, y: 5, w: 50, h: 500 }, 100, 80)).toEqual({ x: 0, y: 5, w: 40, h: 75 });
    expect(clampRect({ x: 120, y: 0, w: 20, h: 20 }, 100, 80)).toBeNull();
  });

  it("shows the last crop, and the whole picture again once it is undone", () => {
    expect(viewport([pen, crop], 200, 100)).toEqual(crop.rect);
    expect(viewport([pen], 200, 100)).toEqual({ x: 0, y: 0, w: 200, h: 100 });
  });

  it("scales stroke and text sizes with the picture", () => {
    expect(strokeWidths(1400, 800)).toEqual([2, 4, 6, 9, 13]);
    expect(strokeWidths(2800, 1600)[0]).toBe(4);
    expect(strokeWidths(300, 200)[0]).toBe(2);
    expect(textSize(6)).toBe(30);
  });
});

describe("tools and keys", () => {
  it("uses CleanShot X's one-letter keys, each once", () => {
    const keys = TOOL_KEYS.map((entry) => entry.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(toolForKey("V")).toBe("move");
    expect(toolForKey("a")).toBe("arrow");
    expect(toolForKey("f")).toBe("filled");
    expect(toolForKey("d")).toBe("pen");
    expect(toolForKey("m")).toBe("highlight");
    expect(toolForKey("c")).toBe("counter");
    expect(toolForKey("h")).toBe("spotlight");
    expect(toolForKey("p")).toBe("redact");
    expect(toolForKey("k")).toBe("crop");
    expect(toolForKey("b")).toBe("background");
    expect(toolForKey("q")).toBeNull();
  });
});

describe("constraints", () => {
  const start = { x: 0, y: 0 };

  it("snaps a line to 45° with Shift", () => {
    const end = constrainEnd("line", start, { x: 100, y: 10 }, true, null);
    expect(end.y).toBeCloseTo(0, 5);
    const diagonal = constrainEnd("arrow", start, { x: 100, y: 90 }, true, null);
    expect(diagonal.x).toBeCloseTo(diagonal.y, 5);
  });

  it("makes a square with Shift and leaves a free drag alone", () => {
    expect(constrainEnd("rect", start, { x: 100, y: 40 }, true, null)).toEqual({ x: 100, y: 100 });
    expect(constrainEnd("rect", start, { x: 100, y: 40 }, false, null)).toEqual({ x: 100, y: 40 });
  });

  it("keeps a crop's aspect ratio in any drag direction", () => {
    const end = constrainEnd("crop", start, { x: -160, y: -10 }, false, 16 / 9);
    expect(end.x).toBe(-160);
    expect(end.y).toBeCloseTo(-90, 5);
  });
});

describe("counter", () => {
  it("numbers on from the highest badge, so undo frees the number", () => {
    const one = withId({ kind: "counter", at: { x: 1, y: 1 }, n: 1, color: "#f00", size: 12 });
    const two = withId({ kind: "counter", at: { x: 5, y: 5 }, n: 2, color: "#f00", size: 12 });
    expect(nextCounter([])).toBe(1);
    expect(nextCounter([one, two])).toBe(3);
    expect(nextCounter([one])).toBe(2);
  });
});

describe("selection and moving", () => {
  const arrow = withId({ kind: "arrow", from: { x: 10, y: 10 }, to: { x: 110, y: 10 }, color: "#f00", width: 4 });
  const box = withId({ kind: "rect", rect: { x: 200, y: 200, w: 50, h: 50 }, color: "#f00", width: 4 });
  const text = withId({ kind: "text", at: { x: 300, y: 20 }, text: "Hi", color: "#fff", size: 20, style: "label" });

  it("finds the annotation under the pointer, top-most first", () => {
    const covering = withId({ kind: "filled", rect: { x: 190, y: 190, w: 80, h: 80 }, color: "#000", width: 4 });
    expect(hitTest([arrow, box], { x: 60, y: 12 }, 3)?.id).toBe(arrow.id);
    expect(hitTest([arrow, box], { x: 60, y: 40 }, 3)).toBeNull();
    expect(hitTest([box, covering], { x: 220, y: 220 }, 3)?.id).toBe(covering.id);
  });

  it("picks what sits in a spotlight or redaction before the area itself", () => {
    // Live 2026-10-03: dragging a caption inside a spotlight moved the spotlight.
    const spot = withId({ kind: "spotlight", rect: { x: 280, y: 0, w: 200, h: 200 } });
    expect(hitTest([text, spot], { x: 310, y: 30 }, 2, () => 30)?.id).toBe(text.id);
    expect(hitTest([text, spot], { x: 450, y: 150 }, 2, () => 30)?.id).toBe(spot.id);
  });

  it("picks an outline at its edge, so what it frames stays reachable", () => {
    expect(hitTest([box], { x: 225, y: 225 }, 2)).toBeNull();
    expect(hitTest([box], { x: 201, y: 225 }, 2)?.id).toBe(box.id);
    const ring = withId({ kind: "ellipse", rect: { x: 0, y: 0, w: 100, h: 100 }, color: "#f00", width: 4 });
    expect(hitTest([ring], { x: 50, y: 50 }, 2)).toBeNull();
    expect(hitTest([ring], { x: 50, y: 2 }, 2)?.id).toBe(ring.id);
  });

  it("never selects a crop", () => {
    expect(hitTest([crop], { x: 20, y: 20 }, 3)).toBeNull();
  });

  it("measures text with the given measure, padded for a label", () => {
    const measured = bounds(text, () => 30);
    expect(measured.w).toBeGreaterThan(30);
    expect(hitTest([text], { x: 310, y: 30 }, 2, () => 30)?.id).toBe(text.id);
  });

  it("moves every kind of annotation and keeps its id", () => {
    expect(translate(arrow, 5, 5)).toMatchObject({ id: arrow.id, from: { x: 15, y: 15 }, to: { x: 115, y: 15 } });
    expect(translate(box, -10, 0).rect.x).toBe(190);
    expect(translate(text, 0, 10).at.y).toBe(30);
    expect(translate(pen, 1, 1).points[0]).toEqual({ x: 2, y: 2 });
  });

  it("drops a stray click instead of keeping an empty shape", () => {
    expect(isMeaningful({ kind: "line", from: { x: 0, y: 0 }, to: { x: 1, y: 1 }, color: "#f00", width: 2 })).toBe(false);
    expect(isMeaningful({ kind: "redact", rect: { x: 0, y: 0, w: 2, h: 40 }, mode: "blur", block: 8 })).toBe(false);
    expect(isMeaningful({ kind: "spotlight", rect: { x: 0, y: 0, w: 40, h: 40 } })).toBe(true);
  });
});

describe("history", () => {
  it("undoes and redoes whole states, and a new edit drops the redo", () => {
    let history = emptyHistory();
    history = commit(history, [pen]);
    history = commit(history, [pen, crop]);
    history = undo(history);
    expect(history.present).toEqual([pen]);
    history = redo(history);
    expect(history.present).toEqual([pen, crop]);
    history = undo(history);
    history = commit(history, []);
    expect(history.future).toEqual([]);
    expect(undo(emptyHistory())).toEqual(emptyHistory());
  });

  it("undoes a move as one step", () => {
    const moved: Op[] = [translate(pen, 10, 0)];
    const history = undo(commit(commit(emptyHistory(), [pen]), moved));
    expect(history.present).toEqual([pen]);
  });
});

describe("background frame", () => {
  it("adds the same padding on every side and leaves an unframed picture alone", () => {
    expect(frameLayout(1000, 500, DEFAULT_BACKGROUND)).toEqual({
      width: 1000,
      height: 500,
      image: { x: 0, y: 0, w: 1000, h: 500 },
      radius: 0,
    });
    const framed = frameLayout(1000, 500, { ...DEFAULT_BACKGROUND, enabled: true, padding: 0.1, radius: 12 });
    expect(framed.image).toEqual({ x: 100, y: 100, w: 1000, h: 500 });
    expect(framed.width).toBe(1200);
    expect(framed.height).toBe(700);
    expect(framed.radius).toBe(12);
  });

  it("caps the padding", () => {
    const framed = frameLayout(100, 100, { ...DEFAULT_BACKGROUND, enabled: true, padding: 5 });
    expect(framed.image.x).toBe(25);
  });

  it("previews every preset as CSS", () => {
    for (const preset of BACKGROUND_PRESETS) expect(presetCss(preset)).toBeTruthy();
    expect(presetCss({ id: "x", stops: ["#111111"] })).toBe("#111111");
  });
});

describe("contrast", () => {
  it("puts dark ink on light colours and white on dark ones", () => {
    expect(contrastOn("#ffcc00")).toBe("#111111");
    expect(contrastOn("#ffffff")).toBe("#111111");
    expect(contrastOn("#0a84ff")).toBe("#ffffff");
    expect(contrastOn("#1c1c1e")).toBe("#ffffff");
  });
});

describe("tapered arrow", () => {
  const width = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

  it("starts as a fine point, swells to the neck and ends in a wider swept-back head", () => {
    const outline = taperedArrowOutline({ x: 0, y: 0 }, { x: 200, y: 0 }, 6);
    const [tailTop, neckTop, barbTop, tip, barbBottom, neckBottom, tailBottom] = outline;
    expect(outline).toHaveLength(7);
    expect(tip).toEqual({ x: 200, y: 0 });
    const tail = width(tailTop, tailBottom);
    const neck = width(neckTop, neckBottom);
    const head = width(barbTop, barbBottom);
    expect(tail).toBeLessThan(neck);
    expect(neck).toBeLessThan(head);
    // The barbs sit behind the neck: the head's base is concave.
    expect(barbTop.x).toBeLessThan(neckTop.x);
  });

  it("keeps a short arrow's head in proportion and draws nothing for a dot", () => {
    const short = taperedArrowOutline({ x: 0, y: 0 }, { x: 20, y: 0 }, 13);
    expect(short[2].x).toBeGreaterThanOrEqual(0);
    expect(taperedArrowOutline({ x: 5, y: 5 }, { x: 5.5, y: 5 }, 6)).toEqual([]);
  });

  it("grows with the stroke size", () => {
    const thin = taperedArrowOutline({ x: 0, y: 0 }, { x: 300, y: 0 }, 2);
    const thick = taperedArrowOutline({ x: 0, y: 0 }, { x: 300, y: 0 }, 13);
    expect(width(thick[2], thick[4])).toBeGreaterThan(width(thin[2], thin[4]));
  });

  it("offers three arrow looks", () => {
    expect(ARROW_STYLES).toEqual(["tapered", "classic", "double"]);
  });
});
