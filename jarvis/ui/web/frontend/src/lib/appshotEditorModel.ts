/**
 * The appshot editor's document: a base picture plus a list of annotations,
 * drawn in the picture's own pixel space, and an optional background frame
 * around the result. Pure data + one renderer, so the on-screen canvas and
 * the exported PNG are painted by the same code.
 *
 * Each annotation tool has a one-letter shortcut
 * (V move, A arrow, L line, R rectangle, F filled rectangle, E ellipse,
 * D draw, M highlighter, T text, C counter, H spotlight, P redact, K crop,
 * B background) for quick access while editing.
 *
 * A crop is an annotation too: the last one sets the visible part of the
 * picture, and undo brings the rest back.
 *
 * More pictures can join the first one (``image`` layers: an earlier appshot
 * or a file placed beside, below or on top). The document then spans all of
 * them — its extent — and they are painted under every annotation, so marks
 * can cross from one picture to the next.
 */

export type Tool =
  | "move"
  | "arrow"
  | "line"
  | "rect"
  | "filled"
  | "ellipse"
  | "pen"
  | "highlight"
  | "text"
  | "counter"
  | "spotlight"
  | "redact"
  | "crop"
  | "background";

export interface Point {
  x: number;
  y: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type TextStyle = "plain" | "label" | "outline";
export type RedactMode = "pixelate" | "blur";
/**
 * ``tapered``: a filled arrow shape that starts as a fine
 * point and swells towards a swept-back head. ``classic``: line plus head.
 * ``double``: a head at both ends.
 */
export type ArrowStyle = "tapered" | "classic" | "double";

export const ARROW_STYLES: readonly ArrowStyle[] = ["tapered", "classic", "double"];

type Shape =
  /** ``via``: the control point of a curved arrow or line (a quadratic curve); none = straight. */
  | { kind: "arrow"; from: Point; to: Point; color: string; width: number; style?: ArrowStyle; via?: Point }
  | { kind: "line"; from: Point; to: Point; color: string; width: number; via?: Point }
  | { kind: "rect" | "filled" | "ellipse"; rect: Rect; color: string; width: number }
  | { kind: "pen" | "highlight"; points: Point[]; color: string; width: number }
  | { kind: "text"; at: Point; text: string; color: string; size: number; style: TextStyle }
  | { kind: "counter"; at: Point; n: number; color: string; size: number }
  | { kind: "spotlight"; rect: Rect }
  | { kind: "redact"; rect: Rect; mode: RedactMode; block: number }
  | { kind: "crop"; rect: Rect }
  /** Another picture placed into the document; ``src`` names it in the editor's sources. */
  | { kind: "image"; rect: Rect; src: string };

/** One annotation. `id` survives moves, so a selection outlives an edit. */
export type Op = Shape & { id: number };

/** A shape before it has an id (what a tool produces). */
export type Draft = Shape;

/** Tool → its key, in toolbar order. */
export const TOOL_KEYS: readonly { tool: Tool; key: string }[] = [
  { tool: "move", key: "v" },
  { tool: "arrow", key: "a" },
  { tool: "line", key: "l" },
  { tool: "rect", key: "r" },
  { tool: "filled", key: "f" },
  { tool: "ellipse", key: "e" },
  { tool: "pen", key: "d" },
  { tool: "highlight", key: "m" },
  { tool: "text", key: "t" },
  { tool: "counter", key: "c" },
  { tool: "spotlight", key: "h" },
  { tool: "redact", key: "p" },
  { tool: "crop", key: "k" },
  { tool: "background", key: "b" },
];

/**
 * What a press with ``tool`` may take hold of instead of drawing:
 *
 * - ``any``: the select tool picks up every annotation, areas included;
 * - ``shapes``: every other drawing tool picks up any annotation under the
 *   pointer (not spotlights or redactions — they cover what is drawn in
 *   them), so anything drawn can be moved and reshaped again at once;
 * - ``grips``: pen and highlighter only take the selected annotation's
 *   grips, so writing over earlier ink never drags it along;
 * - ``none``: crop and background, which work on the whole picture.
 */
export function grabScope(tool: Tool): "any" | "shapes" | "grips" | "none" {
  if (tool === "move") return "any";
  if (tool === "pen" || tool === "highlight") return "grips";
  if (tool === "crop" || tool === "background") return "none";
  return "shapes";
}

/** The point at ``t`` on the quadratic curve ``a`` – ``via`` – ``b``. */
export function curveAt(a: Point, via: Point, b: Point, t: number): Point {
  const u = 1 - t;
  return {
    x: u * u * a.x + 2 * u * t * via.x + t * t * b.x,
    y: u * u * a.y + 2 * u * t * via.y + t * t * b.y,
  };
}

/** An arrow's or line's path: its two ends, or the sampled curve. */
export function segmentPath(op: { from: Point; to: Point; via?: Point }, steps = 32): Point[] {
  if (!op.via) return [op.from, op.to];
  const via = op.via;
  return Array.from({ length: steps + 1 }, (_, i) => curveAt(op.from, via, op.to, i / steps));
}

/** Where the bend grip sits: the middle of the line or of the curve. */
export function segmentMiddle(op: { from: Point; to: Point; via?: Point }): Point {
  if (!op.via) return { x: (op.from.x + op.to.x) / 2, y: (op.from.y + op.to.y) / 2 };
  return curveAt(op.from, op.via, op.to, 0.5);
}

/** A bend grip dropped this close to the straight line straightens it again. */
const STRAIGHT_SNAP = 4;

export function toolForKey(key: string): Tool | null {
  const lower = key.toLowerCase();
  return TOOL_KEYS.find((entry) => entry.key === lower)?.tool ?? null;
}

export const COLORS = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#0a84ff", "#af52de", "#ffffff", "#1c1c1e"];

/** Crop aspect ratios offered next to the crop tool (width / height). */
export const CROP_RATIOS: readonly { id: string; ratio: number | null }[] = [
  { id: "free", ratio: null },
  { id: "1:1", ratio: 1 },
  { id: "4:3", ratio: 4 / 3 },
  { id: "16:9", ratio: 16 / 9 },
];

let nextId = 1;

/** Give a finished shape its id. */
export function withId<T extends Draft>(shape: T): T & { id: number } {
  nextId += 1;
  return { ...shape, id: nextId };
}

/** The five stroke sizes (keys 1–5, the size slider), for a 1400 px picture. */
export const STROKE_LEVELS = [2, 4, 6, 9, 13] as const;

/** The stroke sizes for a picture of this size, in its own pixels. */
export function strokeWidths(width: number, height: number): number[] {
  const unit = Math.max(1, Math.max(width, height) / 1400);
  return STROKE_LEVELS.map((level) => Math.round(level * unit));
}

export function textSize(stroke: number): number {
  return Math.max(14, Math.round(stroke * 5));
}

/** Counter badge radius for a stroke width. */
export function counterSize(stroke: number): number {
  return Math.max(12, Math.round(stroke * 3));
}

/** A rectangle from two corners, in any drag direction. */
export function rectFrom(a: Point, b: Point): Rect {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    w: Math.abs(b.x - a.x),
    h: Math.abs(b.y - a.y),
  };
}

/**
 * Where the drag ends once Shift (or a crop ratio) has its say: lines snap to
 * 45°, boxes become squares, a crop keeps its aspect ratio.
 */
export function constrainEnd(tool: Tool, start: Point, end: Point, shift: boolean, ratio: number | null): Point {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if ((tool === "arrow" || tool === "line") && shift) {
    const angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
    const length = Math.hypot(dx, dy);
    return { x: start.x + Math.cos(angle) * length, y: start.y + Math.sin(angle) * length };
  }
  const boxRatio = tool === "crop" ? ratio : shift ? 1 : null;
  if (boxRatio !== null && boxRatio > 0 && (tool === "crop" || isBoxTool(tool))) {
    const w = Math.abs(dx);
    const h = Math.abs(dy);
    // Follow whichever side the pointer pulled further.
    const width = Math.max(w, h * boxRatio);
    const height = width / boxRatio;
    return { x: start.x + Math.sign(dx || 1) * width, y: start.y + Math.sign(dy || 1) * height };
  }
  return end;
}

function isBoxTool(tool: Tool): boolean {
  return tool === "rect" || tool === "filled" || tool === "ellipse" || tool === "spotlight" || tool === "redact";
}

/** The smallest rectangle around both. */
export function unionRect(a: Rect, b: Rect): Rect {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y };
}

/** The whole document: the first picture and every picture placed with it. */
export function extent(ops: readonly Draft[], width: number, height: number): Rect {
  let area: Rect = { x: 0, y: 0, w: width, h: height };
  for (const op of ops) if (op.kind === "image") area = unionRect(area, op.rect);
  return area;
}

/** The visible part of the document: the last crop, or all of it. */
export function viewport(ops: readonly Op[], width: number, height: number): Rect {
  for (let i = ops.length - 1; i >= 0; i -= 1) {
    const op = ops[i];
    if (op.kind === "crop") return op.rect;
  }
  return extent(ops, width, height);
}

/** Clamp a rect into ``area``; `null` when nothing of it is left. */
export function clampInto(rect: Rect, area: Rect): Rect | null {
  const x = Math.max(area.x, Math.min(area.x + area.w, rect.x));
  const y = Math.max(area.y, Math.min(area.y + area.h, rect.y));
  const right = Math.max(area.x, Math.min(area.x + area.w, rect.x + rect.w));
  const bottom = Math.max(area.y, Math.min(area.y + area.h, rect.y + rect.h));
  if (right - x < 2 || bottom - y < 2) return null;
  return { x, y, w: right - x, h: bottom - y };
}

/** Clamp a rect to a picture of this size; `null` when nothing of it is left. */
export function clampRect(rect: Rect, width: number, height: number): Rect | null {
  return clampInto(rect, { x: 0, y: 0, w: width, h: height });
}

// -- crop: an adjustable frame over the document ----------------------------------

/** A grip on the crop frame: four corners, and the four edges for a free crop. */
export type CropHandle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";

/** The smallest crop, in document pixels. */
const MIN_CROP = 8;

/** The grips the crop frame shows; a fixed ratio keeps only the corners. */
export function cropHandles(rect: Rect, ratio: number | null): { id: CropHandle; at: Point }[] {
  const { x, y, w, h } = rect;
  const corners: { id: CropHandle; at: Point }[] = [
    { id: "nw", at: { x, y } },
    { id: "ne", at: { x: x + w, y } },
    { id: "se", at: { x: x + w, y: y + h } },
    { id: "sw", at: { x, y: y + h } },
  ];
  if (ratio !== null) return corners;
  return [
    ...corners,
    { id: "n", at: { x: x + w / 2, y } },
    { id: "e", at: { x: x + w, y: y + h / 2 } },
    { id: "s", at: { x: x + w / 2, y: y + h } },
    { id: "w", at: { x, y: y + h / 2 } },
  ];
}

/** The crop grip under ``p`` (within ``radius``), nearest first. */
export function cropHandleAt(rect: Rect, p: Point, radius: number, ratio: number | null): CropHandle | null {
  let best: CropHandle | null = null;
  let bestDistance = radius;
  for (const handle of cropHandles(rect, ratio)) {
    const distance = Math.hypot(handle.at.x - p.x, handle.at.y - p.y);
    if (distance <= bestDistance) {
      best = handle.id;
      bestDistance = distance;
    }
  }
  return best;
}

/**
 * The crop frame ``original`` with grip ``handle`` dragged to ``p``, kept
 * inside ``area``. A corner pulls against the opposite corner (and keeps a
 * fixed ratio); an edge moves only itself.
 */
export function resizeCrop(original: Rect, handle: CropHandle, p: Point, area: Rect, ratio: number | null): Rect {
  const left = original.x;
  const top = original.y;
  const right = original.x + original.w;
  const bottom = original.y + original.h;
  const px = Math.max(area.x, Math.min(area.x + area.w, p.x));
  const py = Math.max(area.y, Math.min(area.y + area.h, p.y));
  if (handle === "n") {
    const y = Math.min(py, bottom - MIN_CROP);
    return { x: left, y, w: original.w, h: bottom - y };
  }
  if (handle === "s") return { x: left, y: top, w: original.w, h: Math.max(py, top + MIN_CROP) - top };
  if (handle === "w") {
    const x = Math.min(px, right - MIN_CROP);
    return { x, y: top, w: right - x, h: original.h };
  }
  if (handle === "e") return { x: left, y: top, w: Math.max(px, left + MIN_CROP) - left, h: original.h };
  const anchor = {
    x: handle === "nw" || handle === "sw" ? right : left,
    y: handle === "nw" || handle === "ne" ? bottom : top,
  };
  const dirX = handle === "ne" || handle === "se" ? 1 : -1;
  const dirY = handle === "sw" || handle === "se" ? 1 : -1;
  const roomX = dirX > 0 ? area.x + area.w - anchor.x : anchor.x - area.x;
  const roomY = dirY > 0 ? area.y + area.h - anchor.y : anchor.y - area.y;
  let w = Math.max(MIN_CROP, Math.min(roomX, (px - anchor.x) * dirX));
  let h = Math.max(MIN_CROP, Math.min(roomY, (py - anchor.y) * dirY));
  if (ratio !== null && ratio > 0) {
    w = Math.max(w, h * ratio);
    h = w / ratio;
    if (w > roomX) {
      w = roomX;
      h = w / ratio;
    }
    if (h > roomY) {
      h = roomY;
      w = h * ratio;
    }
  }
  return { x: dirX > 0 ? anchor.x : anchor.x - w, y: dirY > 0 ? anchor.y : anchor.y - h, w, h };
}

/** The crop frame moved by ``dx``/``dy``, stopping at the edges of ``area``. */
export function moveCrop(rect: Rect, dx: number, dy: number, area: Rect): Rect {
  return {
    ...rect,
    x: Math.max(area.x, Math.min(area.x + area.w - rect.w, rect.x + dx)),
    y: Math.max(area.y, Math.min(area.y + area.h - rect.h, rect.y + dy)),
  };
}

/** The largest rectangle of ``ratio`` inside ``rect``, centred in it. */
export function fitRatio(rect: Rect, ratio: number): Rect {
  const w = Math.min(rect.w, rect.h * ratio);
  const h = w / ratio;
  return { x: rect.x + (rect.w - w) / 2, y: rect.y + (rect.h - h) / 2, w, h };
}

/** Is ``rect`` all of ``area`` (so no crop is needed)? */
export function coversAll(rect: Rect, area: Rect): boolean {
  return (
    Math.abs(rect.x - area.x) < 0.5 &&
    Math.abs(rect.y - area.y) < 0.5 &&
    Math.abs(rect.w - area.w) < 0.5 &&
    Math.abs(rect.h - area.h) < 0.5
  );
}

/** The document with one crop (or none): an earlier crop is replaced, not stacked. */
export function withCrop(ops: readonly Op[], rect: Rect | null): Op[] {
  const rest = ops.filter((op) => op.kind !== "crop");
  return rect ? [...rest, withId({ kind: "crop", rect } as const)] : rest;
}

// -- more pictures ------------------------------------------------------------------

/** Where another picture goes: right of what is visible, under it, or over its middle. */
export type Placement = "beside" | "below" | "over";

export const PLACEMENTS: readonly Placement[] = ["beside", "below", "over"];

/**
 * Place a ``width`` x ``height`` picture against what is visible now
 * (``view``): beside it at the same height, below it at the same width, or
 * over its middle at under half its size. The picture keeps its shape.
 */
export function placeImage(view: Rect, width: number, height: number, placement: Placement): Rect {
  const aspect = Math.max(1, width) / Math.max(1, height);
  if (placement === "beside") return { x: view.x + view.w, y: view.y, w: view.h * aspect, h: view.h };
  if (placement === "below") return { x: view.x, y: view.y + view.h, w: view.w, h: view.w / aspect };
  const w = Math.min(view.w * 0.45, view.h * 0.45 * aspect, width);
  const h = w / aspect;
  return { x: view.x + (view.w - w) / 2, y: view.y + (view.h - h) / 2, w, h };
}

/**
 * The document with another picture added, and the new layer's id. With a
 * crop in place the crop grows to take the new picture in, so it never lands
 * out of sight.
 */
export function addImage(
  ops: readonly Op[],
  width: number,
  height: number,
  image: { src: string; width: number; height: number },
  placement: Placement,
): { ops: Op[]; id: number } {
  const view = viewport(ops, width, height);
  const rect = placeImage(view, image.width, image.height, placement);
  const layer = withId({ kind: "image", rect, src: image.src } as const);
  const next = [...ops, layer];
  const cropped = ops.some((op) => op.kind === "crop");
  return { ops: cropped ? withCrop(next, unionRect(view, rect)) : next, id: layer.id };
}

/** The number the next counter badge carries. */
export function nextCounter(ops: readonly Op[]): number {
  let highest = 0;
  for (const op of ops) if (op.kind === "counter") highest = Math.max(highest, op.n);
  return highest + 1;
}

/** Measures text the way the canvas will draw it; replaceable in tests. */
export type MeasureText = (text: string, size: number) => number;

const roughMeasure: MeasureText = (text, size) => text.length * size * 0.55;

function textLines(text: string): string[] {
  return text.split("\n");
}

/** The box a shape covers, for selection and hit testing. */
export function bounds(op: Draft, measure: MeasureText = roughMeasure): Rect {
  switch (op.kind) {
    case "arrow":
    case "line": {
      if (!op.via) return rectFrom(op.from, op.to);
      const path = segmentPath(op);
      const xs = path.map((p) => p.x);
      const ys = path.map((p) => p.y);
      return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
    }
    case "pen":
    case "highlight": {
      const xs = op.points.map((p) => p.x);
      const ys = op.points.map((p) => p.y);
      return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
    }
    case "text": {
      const lines = textLines(op.text);
      const width = Math.max(...lines.map((line) => measure(line, op.size)));
      const pad = op.style === "label" ? op.size * 0.4 : 0;
      return { x: op.at.x - pad, y: op.at.y - pad, w: width + pad * 2, h: lines.length * op.size * 1.25 + pad * 2 };
    }
    case "counter":
      return { x: op.at.x - op.size, y: op.at.y - op.size, w: op.size * 2, h: op.size * 2 };
    default:
      return op.rect;
  }
}

function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  const t = lengthSq === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function inside(p: Point, box: Rect, slop: number): boolean {
  return p.x >= box.x - slop && p.x <= box.x + box.w + slop && p.y >= box.y - slop && p.y <= box.y + box.h + slop;
}

/** Areas that cover others: picked only when nothing drawn on them is hit. */
const AREA_KINDS: ReadonlySet<Op["kind"]> = new Set(["spotlight", "redact", "image"]);

function hits(op: Op, p: Point, slop: number, measure?: MeasureText): boolean {
  switch (op.kind) {
    case "crop":
      return false;
    case "arrow":
    case "line": {
      const path = segmentPath(op);
      for (let j = 1; j < path.length; j += 1) {
        if (distanceToSegment(p, path[j - 1], path[j]) <= slop + op.width) return true;
      }
      return false;
    }
    case "pen":
    case "highlight": {
      const reach = slop + (op.kind === "highlight" ? op.width * 2 : op.width);
      if (op.points.length === 1) return Math.hypot(p.x - op.points[0].x, p.y - op.points[0].y) <= reach;
      for (let j = 1; j < op.points.length; j += 1) {
        if (distanceToSegment(p, op.points[j - 1], op.points[j]) <= reach) return true;
      }
      return false;
    }
    case "rect": {
      // An outline is picked at its edge, so what it frames stays clickable.
      const reach = slop + op.width;
      const r = op.rect;
      return inside(p, r, reach) && !inside(p, { x: r.x + reach, y: r.y + reach, w: r.w - reach * 2, h: r.h - reach * 2 }, 0);
    }
    case "ellipse": {
      const rx = Math.max(1, op.rect.w / 2);
      const ry = Math.max(1, op.rect.h / 2);
      const dx = (p.x - (op.rect.x + rx)) / rx;
      const dy = (p.y - (op.rect.y + ry)) / ry;
      return Math.abs(Math.hypot(dx, dy) - 1) * Math.min(rx, ry) <= slop + op.width;
    }
    default:
      return inside(p, bounds(op, measure), slop);
  }
}

/**
 * The top-most annotation under `p`, or null. Lines and strokes count near
 * their path, outlines near their edge, filled shapes, text and counters
 * anywhere on them. Spotlights and redactions come last — they cover
 * whatever sits in them — and crops are never picked.
 */
export function hitTest(
  ops: readonly Op[],
  p: Point,
  slop: number,
  measure?: MeasureText,
  { areas: withAreas = true }: { areas?: boolean } = {},
): Op | null {
  for (const areas of withAreas ? [false, true] : [false]) {
    for (let i = ops.length - 1; i >= 0; i -= 1) {
      const op = ops[i];
      if (AREA_KINDS.has(op.kind) !== areas) continue;
      if (hits(op, p, slop, measure)) return op;
    }
  }
  return null;
}

function shiftPoint(p: Point, dx: number, dy: number): Point {
  return { x: p.x + dx, y: p.y + dy };
}

/** The same annotation, moved. */
export function translate<T extends Draft>(op: T, dx: number, dy: number): T {
  switch (op.kind) {
    case "arrow":
    case "line":
      return {
        ...op,
        from: shiftPoint(op.from, dx, dy),
        to: shiftPoint(op.to, dx, dy),
        ...(op.via ? { via: shiftPoint(op.via, dx, dy) } : {}),
      };
    case "pen":
    case "highlight":
      return { ...op, points: op.points.map((p) => shiftPoint(p, dx, dy)) };
    case "text":
    case "counter":
      return { ...op, at: shiftPoint(op.at, dx, dy) };
    default:
      return { ...op, rect: { ...op.rect, x: op.rect.x + dx, y: op.rect.y + dy } };
  }
}

// -- handles: reshaping an annotation in place --------------------------------

/**
 * A grip on a selected annotation. Lines and arrows have one at each end
 * (``from``, ``to``) and one in the middle that bends them (``mid``); boxes, strokes and text one at each corner (text
 * scales its size with them); a counter one on its rim (``size``).
 */
export type HandleId = "from" | "to" | "mid" | "nw" | "ne" | "sw" | "se" | "size";

export interface Handle {
  id: HandleId;
  at: Point;
}

const OPPOSITE: Record<"nw" | "ne" | "sw" | "se", "nw" | "ne" | "sw" | "se"> = {
  nw: "se",
  ne: "sw",
  sw: "ne",
  se: "nw",
};

function corner(box: Rect, id: "nw" | "ne" | "sw" | "se"): Point {
  return {
    x: id === "nw" || id === "sw" ? box.x : box.x + box.w,
    y: id === "nw" || id === "ne" ? box.y : box.y + box.h,
  };
}

/** The grips a selected annotation shows. */
export function handles(op: Draft, measure?: MeasureText): Handle[] {
  switch (op.kind) {
    case "arrow":
    case "line":
      return [
        { id: "from", at: op.from },
        { id: "mid", at: segmentMiddle(op) },
        { id: "to", at: op.to },
      ];
    case "crop":
      return [];
    case "counter":
      return [{ id: "size", at: { x: op.at.x + op.size, y: op.at.y } }];
    default: {
      const box = bounds(op, measure);
      return (["nw", "ne", "sw", "se"] as const).map((id) => ({ id, at: corner(box, id) }));
    }
  }
}

/** The grip under `p` (within `radius`), nearest first; null when none. */
export function handleAt(op: Draft, p: Point, radius: number, measure?: MeasureText): HandleId | null {
  let best: HandleId | null = null;
  let bestDistance = radius;
  for (const handle of handles(op, measure)) {
    const distance = Math.hypot(handle.at.x - p.x, handle.at.y - p.y);
    if (distance <= bestDistance) {
      best = handle.id;
      bestDistance = distance;
    }
  }
  return best;
}

/**
 * ``original`` with grip ``handle`` dragged to ``p``. Always computed from
 * the shape as it was when the drag began, so a long drag never drifts.
 * An arrow's end follows the pointer; a box keeps the opposite corner
 * fixed; a stroke scales from it; text grows or shrinks from it, font size
 * and all; a counter's
 * badge grows with its rim.
 */
export function reshape<T extends Draft>(original: T, handle: HandleId, p: Point, measure?: MeasureText): T {
  const op = original as Draft;
  switch (op.kind) {
    case "arrow":
    case "line":
      if (handle === "from") return { ...op, from: p } as T;
      if (handle === "to") return { ...op, to: p } as T;
      if (handle === "mid") {
        // The curve's middle lands on the pointer; near the straight line it straightens.
        const middle = { x: (op.from.x + op.to.x) / 2, y: (op.from.y + op.to.y) / 2 };
        if (Math.hypot(p.x - middle.x, p.y - middle.y) < STRAIGHT_SNAP) {
          const { via: _via, ...straight } = op;
          return straight as T;
        }
        return { ...op, via: { x: 2 * p.x - middle.x, y: 2 * p.y - middle.y } } as T;
      }
      return original;
    case "crop":
      return original;
    case "image": {
      // A picture keeps its shape: the larger pull wins, the opposite corner stays.
      if (handle === "from" || handle === "to" || handle === "mid" || handle === "size") return original;
      const anchor = corner(op.rect, OPPOSITE[handle]);
      const aspect = op.rect.w / Math.max(1e-6, op.rect.h);
      const w = Math.max(8, Math.abs(p.x - anchor.x), Math.abs(p.y - anchor.y) * aspect);
      const h = w / aspect;
      const x = handle === "ne" || handle === "se" ? anchor.x : anchor.x - w;
      const y = handle === "sw" || handle === "se" ? anchor.y : anchor.y - h;
      return { ...op, rect: { x, y, w, h } } as T;
    }
    case "counter":
      return { ...op, size: Math.max(8, Math.hypot(p.x - op.at.x, p.y - op.at.y)) } as T;
    case "text": {
      // Any corner scales the text; the opposite corner stays where it was.
      if (handle === "from" || handle === "to" || handle === "mid" || handle === "size") return original;
      const box = bounds(op, measure);
      const anchor = corner(box, OPPOSITE[handle]);
      const factor = Math.max(0.1, Math.abs(p.y - anchor.y) / Math.max(1, box.h));
      const sized = { ...op, size: Math.max(8, Math.round(op.size * factor)) };
      const grown = bounds({ ...sized, at: { x: 0, y: 0 } }, measure);
      const pad = sized.style === "label" ? sized.size * 0.4 : 0;
      const left = handle === "ne" || handle === "se" ? anchor.x : anchor.x - grown.w;
      const top = handle === "sw" || handle === "se" ? anchor.y : anchor.y - grown.h;
      return { ...sized, at: { x: left + pad, y: top + pad } } as T;
    }
    case "pen":
    case "highlight": {
      if (handle === "from" || handle === "to" || handle === "mid" || handle === "size") return original;
      const box = bounds(op);
      const anchor = corner(box, OPPOSITE[handle]);
      const grip = corner(box, handle);
      const sx = Math.abs(grip.x - anchor.x) < 1e-6 ? 1 : (p.x - anchor.x) / (grip.x - anchor.x);
      const sy = Math.abs(grip.y - anchor.y) < 1e-6 ? 1 : (p.y - anchor.y) / (grip.y - anchor.y);
      return {
        ...op,
        points: op.points.map((q) => ({ x: anchor.x + (q.x - anchor.x) * sx, y: anchor.y + (q.y - anchor.y) * sy })),
      } as T;
    }
    default: {
      if (handle === "from" || handle === "to" || handle === "mid" || handle === "size") return original;
      return { ...op, rect: rectFrom(corner(op.rect, OPPOSITE[handle]), p) } as T;
    }
  }
}

/** The pointer cursor that fits a grip. */
export function handleCursor(handle: HandleId | CropHandle): string {
  if (handle === "nw" || handle === "se") return "nwse-resize";
  if (handle === "ne" || handle === "sw") return "nesw-resize";
  if (handle === "size" || handle === "e" || handle === "w") return "ew-resize";
  if (handle === "n" || handle === "s") return "ns-resize";
  return "grab";
}

/** Is this freshly drawn shape big enough to keep (not a stray click)? */
export function isMeaningful(op: Draft): boolean {
  switch (op.kind) {
    case "arrow":
    case "line":
      return Math.hypot(op.to.x - op.from.x, op.to.y - op.from.y) >= 4;
    case "pen":
    case "highlight":
      return op.points.length > 0;
    case "text":
      return op.text.trim().length > 0;
    case "counter":
      return true;
    default:
      return op.rect.w >= 4 && op.rect.h >= 4;
  }
}

// -- history ------------------------------------------------------------------

export interface History {
  past: Op[][];
  present: Op[];
  future: Op[][];
}

const HISTORY_LIMIT = 100;

export function emptyHistory(): History {
  return { past: [], present: [], future: [] };
}

/** A new document state; the old one becomes undoable, redo is dropped. */
export function commit(history: History, next: Op[]): History {
  return { past: [...history.past, history.present].slice(-HISTORY_LIMIT), present: next, future: [] };
}

export function undo(history: History): History {
  if (history.past.length === 0) return history;
  return {
    past: history.past.slice(0, -1),
    present: history.past[history.past.length - 1],
    future: [history.present, ...history.future],
  };
}

export function redo(history: History): History {
  if (history.future.length === 0) return history;
  return { past: [...history.past, history.present], present: history.future[0], future: history.future.slice(1) };
}

// -- background ---------------------------------------------------------------

export interface BackgroundPreset {
  id: string;
  /** Two or three colour stops, drawn diagonally; one stop is a solid fill. */
  stops: string[];
}

/** Our own palette — soft gradients and quiet solids, no third-party art. */
export const BACKGROUND_PRESETS: readonly BackgroundPreset[] = [
  { id: "dawn", stops: ["#ff9a8b", "#ff6a88", "#ff99ac"] },
  { id: "lagoon", stops: ["#43cea2", "#185a9d"] },
  { id: "dusk", stops: ["#7f7fd5", "#86a8e7", "#91eae4"] },
  { id: "ember", stops: ["#f7971e", "#ffd200"] },
  { id: "orchid", stops: ["#c471f5", "#fa71cd"] },
  { id: "slate", stops: ["#434343", "#1c1c1e"] },
  { id: "paper", stops: ["#f5f5f7"] },
  { id: "ink", stops: ["#111113"] },
];

export interface Background {
  enabled: boolean;
  preset: string;
  /** Space around the picture, as a share of its longer side (0 – 0.25). */
  padding: number;
  /** Corner radius of the picture, in its own pixels before scaling. */
  radius: number;
  shadow: boolean;
}

export const DEFAULT_BACKGROUND: Background = {
  enabled: false,
  preset: "dusk",
  padding: 0.08,
  radius: 12,
  shadow: true,
};

export function presetById(id: string): BackgroundPreset {
  return BACKGROUND_PRESETS.find((preset) => preset.id === id) ?? BACKGROUND_PRESETS[0];
}

export interface Layout {
  width: number;
  height: number;
  /** Where the picture sits inside the framed result. */
  image: Rect;
  radius: number;
}

/** The size of the result and where the picture sits in it. */
export function frameLayout(width: number, height: number, background: Background): Layout {
  if (!background.enabled) {
    return { width, height, image: { x: 0, y: 0, w: width, h: height }, radius: 0 };
  }
  const pad = Math.round(Math.max(width, height) * Math.max(0, Math.min(0.25, background.padding)));
  const unit = Math.max(1, Math.max(width, height) / 1400);
  return {
    width: width + pad * 2,
    height: height + pad * 2,
    image: { x: pad, y: pad, w: width, h: height },
    radius: Math.round(Math.max(0, background.radius) * unit),
  };
}

/** The same gradient as CSS, for the on-screen preview of the frame. */
export function presetCss(preset: BackgroundPreset): string {
  if (preset.stops.length === 1) return preset.stops[0];
  return `linear-gradient(135deg, ${preset.stops.join(", ")})`;
}

// -- painting -----------------------------------------------------------------

/** Black or white, whichever reads on `hex`. */
export function contrastOn(hex: string): string {
  const value = hex.replace("#", "");
  const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luminance > 0.6 ? "#111111" : "#ffffff";
}

/**
 * The outline of a tapered arrow, tail to tip and back, in picture space.
 *
 * Measured along the arrow: a fine tail point, a shaft that widens to the
 * neck, and a head whose barbs sweep back past the neck — the concave base
 * that joins the shaft and head into one continuous shape. Everything
 * scales with the stroke width; a short arrow keeps its head in proportion.
 * Empty for an arrow too short to draw.
 */
export function taperedArrowOutline(from: Point, to: Point, width: number, via?: Point): Point[] {
  if (via) return curvedArrowOutline(from, to, width, via);
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy);
  if (length < 2) return [];
  const ux = dx / length;
  const uy = dy / length;
  const nx = -uy;
  const ny = ux;
  const head = Math.min(Math.max(12, width * 4.6 + 6), length * 0.62);
  const barb = head * 0.56;
  const neckAt = length - head * 0.7;
  const tail = Math.max(0.6, width * 0.14);
  const neck = Math.max(1.4, width * 0.78);
  const at = (along: number, across: number): Point => ({
    x: from.x + ux * along + nx * across,
    y: from.y + uy * along + ny * across,
  });
  return [
    at(0, tail),
    at(neckAt, neck),
    at(length - head, barb),
    at(length, 0),
    at(length - head, -barb),
    at(neckAt, -neck),
    at(0, -tail),
  ];
}

/** The tapered arrow along a curve: the same profile, following the bend. */
function curvedArrowOutline(from: Point, to: Point, width: number, via: Point): Point[] {
  const path = segmentPath({ from, to, via }, 48);
  const lengths = [0];
  for (let i = 1; i < path.length; i += 1) {
    lengths.push(lengths[i - 1] + Math.hypot(path[i].x - path[i - 1].x, path[i].y - path[i - 1].y));
  }
  const length = lengths[lengths.length - 1];
  if (length < 2) return [];
  const head = Math.min(Math.max(12, width * 4.6 + 6), length * 0.62);
  const barb = head * 0.56;
  const neckAt = length - head * 0.7;
  const tail = Math.max(0.6, width * 0.14);
  const neck = Math.max(1.4, width * 0.78);
  const left: Point[] = [];
  const right: Point[] = [];
  for (let i = 0; i < path.length && lengths[i] <= neckAt; i += 1) {
    const a = path[Math.max(0, i - 1)];
    const b = path[Math.min(path.length - 1, i + 1)];
    const span = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const nx = -(b.y - a.y) / span;
    const ny = (b.x - a.x) / span;
    const half = tail + (neck - tail) * (lengths[i] / Math.max(1, neckAt));
    left.push({ x: path[i].x + nx * half, y: path[i].y + ny * half });
    right.push({ x: path[i].x - nx * half, y: path[i].y - ny * half });
  }
  // The head points along the curve's last stretch.
  const span = Math.hypot(to.x - via.x, to.y - via.y) || 1;
  const ux = (to.x - via.x) / span;
  const uy = (to.y - via.y) / span;
  const base = { x: to.x - ux * head, y: to.y - uy * head };
  return [
    ...left,
    { x: base.x - uy * barb, y: base.y + ux * barb },
    to,
    { x: base.x + uy * barb, y: base.y - ux * barb },
    ...right.reverse(),
  ];
}

function taperedArrow(ctx: CanvasRenderingContext2D, from: Point, to: Point, width: number, via?: Point) {
  const outline = taperedArrowOutline(from, to, width, via);
  if (outline.length === 0) return;
  ctx.beginPath();
  ctx.moveTo(outline[0].x, outline[0].y);
  for (const point of outline.slice(1)) ctx.lineTo(point.x, point.y);
  ctx.closePath();
  // A soft lift off the picture, and a hairline of the same colour to round
  // the corners — the arrow should look drawn, not cut out.
  ctx.shadowColor = "rgba(0,0,0,0.32)";
  ctx.shadowBlur = Math.max(3, width * 1.1);
  ctx.shadowOffsetY = Math.max(1, width * 0.3);
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.lineWidth = Math.max(1, width * 0.22);
  ctx.lineJoin = "round";
  ctx.stroke();
}

/** A classic arrow along a curve: the curve, and a head at each end ``heads`` asks for. */
function curvedArrow(ctx: CanvasRenderingContext2D, from: Point, to: Point, width: number, via: Point, double: boolean) {
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.quadraticCurveTo(via.x, via.y, to.x, to.y);
  ctx.stroke();
  const head = Math.max(10, width * 4);
  const tips: [Point, Point][] = double ? [[to, via], [from, via]] : [[to, via]];
  for (const [tip, toward] of tips) {
    const angle = Math.atan2(tip.y - toward.y, tip.x - toward.x);
    ctx.beginPath();
    ctx.moveTo(tip.x, tip.y);
    ctx.lineTo(tip.x - Math.cos(angle - 0.45) * head, tip.y - Math.sin(angle - 0.45) * head);
    ctx.lineTo(tip.x - Math.cos(angle + 0.45) * head, tip.y - Math.sin(angle + 0.45) * head);
    ctx.closePath();
    ctx.fill();
  }
}

function arrow(ctx: CanvasRenderingContext2D, from: Point, to: Point, width: number) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const head = Math.max(10, width * 4);
  const back = { x: to.x - Math.cos(angle) * head * 0.8, y: to.y - Math.sin(angle) * head * 0.8 };
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(back.x, back.y);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(to.x, to.y);
  ctx.lineTo(to.x - Math.cos(angle - 0.45) * head, to.y - Math.sin(angle - 0.45) * head);
  ctx.lineTo(to.x - Math.cos(angle + 0.45) * head, to.y - Math.sin(angle + 0.45) * head);
  ctx.closePath();
  ctx.fill();
}

/** A hand-drawn stroke smoothed through the midpoints of its samples. */
function smoothStroke(ctx: CanvasRenderingContext2D, points: Point[]) {
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  if (points.length === 1) {
    ctx.lineTo(points[0].x + 0.1, points[0].y);
  } else {
    for (let i = 1; i < points.length - 1; i += 1) {
      const mid = { x: (points[i].x + points[i + 1].x) / 2, y: (points[i].y + points[i + 1].y) / 2 };
      ctx.quadraticCurveTo(points[i].x, points[i].y, mid.x, mid.y);
    }
    const last = points[points.length - 1];
    ctx.lineTo(last.x, last.y);
  }
  ctx.stroke();
}

function makeCanvas(width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width));
  canvas.height = Math.max(1, Math.round(height));
  return canvas;
}

/**
 * The pictures of a document, flattened: ``source`` drawn at ``origin``
 * covers ``area``. Redactions read their pixels from it, so they hide what
 * any of the pictures shows.
 */
export interface Scene {
  source: CanvasImageSource;
  origin: Point;
  area: Rect;
}

/** Flatten the first picture and every placed picture into one scene. */
export function buildScene(
  base: CanvasImageSource,
  width: number,
  height: number,
  ops: readonly Draft[],
  sources?: ReadonlyMap<string, CanvasImageSource>,
): Scene {
  const area = extent(ops, width, height);
  const layers = ops.filter((op): op is Extract<Draft, { kind: "image" }> => op.kind === "image");
  if (layers.length === 0) return { source: base, origin: { x: 0, y: 0 }, area };
  const canvas = makeCanvas(area.w, area.h);
  const ctx = canvas.getContext("2d");
  if (!ctx) return { source: base, origin: { x: 0, y: 0 }, area };
  ctx.drawImage(base, -area.x, -area.y);
  for (const layer of layers) {
    const picture = sources?.get(layer.src);
    if (picture) ctx.drawImage(picture, layer.rect.x - area.x, layer.rect.y - area.y, layer.rect.w, layer.rect.h);
  }
  return { source: canvas, origin: { x: area.x, y: area.y }, area };
}

/**
 * Hide a region. Pixelate keeps hard blocks; blur shrinks and grows the
 * region twice with smoothing, which every engine supports (canvas `filter`
 * is missing in some WebKit builds the macOS and Linux shells use).
 */
function redact(ctx: CanvasRenderingContext2D, scene: Scene, rect: Rect, mode: RedactMode, block: number) {
  const w = Math.max(1, Math.round(rect.w / block));
  const h = Math.max(1, Math.round(rect.h / block));
  const small = makeCanvas(w, h);
  const sctx = small.getContext("2d");
  if (!sctx) return;
  sctx.imageSmoothingEnabled = true;
  sctx.drawImage(scene.source, rect.x - scene.origin.x, rect.y - scene.origin.y, rect.w, rect.h, 0, 0, w, h);
  ctx.save();
  ctx.beginPath();
  ctx.rect(rect.x, rect.y, rect.w, rect.h);
  ctx.clip();
  if (mode === "pixelate") {
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(small, 0, 0, w, h, rect.x, rect.y, rect.w, rect.h);
  } else {
    const tiny = makeCanvas(Math.max(1, Math.round(w / 2)), Math.max(1, Math.round(h / 2)));
    const tctx = tiny.getContext("2d");
    ctx.imageSmoothingEnabled = true;
    if (tctx) {
      tctx.imageSmoothingEnabled = true;
      tctx.drawImage(small, 0, 0, tiny.width, tiny.height);
      ctx.drawImage(tiny, 0, 0, tiny.width, tiny.height, rect.x, rect.y, rect.w, rect.h);
    } else {
      ctx.drawImage(small, 0, 0, w, h, rect.x, rect.y, rect.w, rect.h);
    }
  }
  ctx.restore();
}

function roundedRect(ctx: CanvasRenderingContext2D, rect: Rect, radius: number) {
  ctx.beginPath();
  roundedRectPath(ctx, rect, radius);
}

export function textFont(size: number): string {
  return `600 ${size}px Inter, "Segoe UI", system-ui, -apple-system, sans-serif`;
}

/** A text measure backed by a real canvas, or the rough one without it. */
export function canvasMeasure(ctx: CanvasRenderingContext2D | null): MeasureText {
  if (!ctx) return roughMeasure;
  return (text, size) => {
    ctx.font = textFont(size);
    return ctx.measureText(text).width;
  };
}

function paintText(ctx: CanvasRenderingContext2D, op: Extract<Draft, { kind: "text" }>) {
  ctx.font = textFont(op.size);
  ctx.textBaseline = "top";
  const lines = textLines(op.text);
  const lineHeight = op.size * 1.25;
  if (op.style === "label") {
    const pad = op.size * 0.4;
    const width = Math.max(...lines.map((line) => ctx.measureText(line).width));
    ctx.fillStyle = op.color;
    roundedRect(ctx, { x: op.at.x - pad, y: op.at.y - pad, w: width + pad * 2, h: lines.length * lineHeight + pad * 2 }, pad);
    ctx.fill();
    ctx.fillStyle = contrastOn(op.color);
  } else if (op.style === "outline") {
    ctx.lineJoin = "round";
    ctx.lineWidth = Math.max(2, op.size / 6);
    ctx.strokeStyle = contrastOn(op.color);
    lines.forEach((line, i) => ctx.strokeText(line, op.at.x, op.at.y + i * lineHeight));
    ctx.fillStyle = op.color;
  } else {
    ctx.fillStyle = op.color;
    ctx.shadowColor = "rgba(0,0,0,0.45)";
    ctx.shadowBlur = Math.max(2, op.size / 8);
  }
  lines.forEach((line, i) => ctx.fillText(line, op.at.x, op.at.y + i * lineHeight));
}

function paintCounter(ctx: CanvasRenderingContext2D, op: Extract<Draft, { kind: "counter" }>) {
  ctx.beginPath();
  ctx.arc(op.at.x, op.at.y, op.size, 0, Math.PI * 2);
  ctx.fillStyle = op.color;
  ctx.shadowColor = "rgba(0,0,0,0.35)";
  ctx.shadowBlur = Math.max(2, op.size / 4);
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.lineWidth = Math.max(1.5, op.size / 9);
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.stroke();
  ctx.fillStyle = contrastOn(op.color);
  ctx.font = textFont(Math.round(op.size * (op.n > 9 ? 0.95 : 1.15)));
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(String(op.n), op.at.x, op.at.y + op.size * 0.06);
}

/** Paint one annotation (crops, spotlights and pictures excluded — see paintOps). */
export function paintShape(ctx: CanvasRenderingContext2D, scene: Scene, op: Draft) {
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  switch (op.kind) {
    case "arrow":
      ctx.strokeStyle = op.color;
      ctx.fillStyle = op.color;
      ctx.lineWidth = op.width;
      if (op.via && (op.style === "classic" || op.style === "double")) {
        curvedArrow(ctx, op.from, op.to, op.width, op.via, op.style === "double");
      } else if (op.style === "classic") {
        arrow(ctx, op.from, op.to, op.width);
      } else if (op.style === "double") {
        arrow(ctx, op.from, op.to, op.width);
        arrow(ctx, op.to, op.from, op.width);
      } else {
        // Tapered is the default, also for arrows drawn before styles existed.
        taperedArrow(ctx, op.from, op.to, op.width, op.via);
      }
      break;
    case "line":
      ctx.strokeStyle = op.color;
      ctx.lineWidth = op.width;
      ctx.beginPath();
      ctx.moveTo(op.from.x, op.from.y);
      if (op.via) ctx.quadraticCurveTo(op.via.x, op.via.y, op.to.x, op.to.y);
      else ctx.lineTo(op.to.x, op.to.y);
      ctx.stroke();
      break;
    case "rect":
      ctx.strokeStyle = op.color;
      ctx.lineWidth = op.width;
      roundedRect(ctx, op.rect, op.width);
      ctx.stroke();
      break;
    case "filled":
      ctx.fillStyle = op.color;
      roundedRect(ctx, op.rect, op.width);
      ctx.fill();
      break;
    case "ellipse":
      ctx.strokeStyle = op.color;
      ctx.lineWidth = op.width;
      ctx.beginPath();
      ctx.ellipse(
        op.rect.x + op.rect.w / 2,
        op.rect.y + op.rect.h / 2,
        Math.max(1, op.rect.w / 2),
        Math.max(1, op.rect.h / 2),
        0,
        0,
        Math.PI * 2,
      );
      ctx.stroke();
      break;
    case "pen":
    case "highlight":
      if (op.points.length === 0) break;
      ctx.strokeStyle = op.color;
      ctx.lineWidth = op.kind === "highlight" ? op.width * 4 : op.width;
      if (op.kind === "highlight") {
        // Multiply keeps the text under the marker readable, like ink.
        ctx.globalAlpha = 0.4;
        ctx.globalCompositeOperation = "multiply";
        ctx.lineCap = "butt";
      }
      smoothStroke(ctx, op.points);
      break;
    case "text":
      paintText(ctx, op);
      break;
    case "counter":
      paintCounter(ctx, op);
      break;
    case "redact":
      redact(ctx, scene, op.rect, op.mode, op.block);
      break;
    case "spotlight":
    case "crop":
    case "image":
      break;
  }
  ctx.restore();
}

/** Dim everything outside the spotlights, in one layer so they never stack. */
function paintSpotlights(ctx: CanvasRenderingContext2D, spots: Rect[], area: Rect) {
  if (spots.length === 0) return;
  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.beginPath();
  ctx.rect(area.x, area.y, area.w, area.h);
  for (const spot of spots) {
    roundedRectPath(ctx, spot, Math.min(spot.w, spot.h) * 0.06);
  }
  ctx.fill("evenodd");
  ctx.restore();
}

function roundedRectPath(ctx: CanvasRenderingContext2D, rect: Rect, radius: number) {
  const r = Math.max(0, Math.min(radius, rect.w / 2, rect.h / 2));
  ctx.moveTo(rect.x + r, rect.y);
  ctx.arcTo(rect.x + rect.w, rect.y, rect.x + rect.w, rect.y + rect.h, r);
  ctx.arcTo(rect.x + rect.w, rect.y + rect.h, rect.x, rect.y + rect.h, r);
  ctx.arcTo(rect.x, rect.y + rect.h, rect.x, rect.y, r);
  ctx.arcTo(rect.x, rect.y, rect.x + rect.w, rect.y, r);
  ctx.closePath();
}

/**
 * Paint the pictures and every annotation in document space. The caller sets
 * the transform: identity for an export, a scale + offset for the screen.
 */
export function paintOps(ctx: CanvasRenderingContext2D, scene: Scene, ops: readonly Draft[]) {
  ctx.drawImage(scene.source, scene.origin.x, scene.origin.y);
  const spots: Rect[] = [];
  for (const op of ops) {
    if (op.kind === "spotlight") spots.push(op.rect);
    else paintShape(ctx, scene, op);
  }
  paintSpotlights(ctx, spots, scene.area);
}

/** Render the finished picture: cropped, annotated and framed. */
export function renderResult(
  base: HTMLImageElement,
  ops: readonly Op[],
  background: Background,
  sources?: ReadonlyMap<string, CanvasImageSource>,
): HTMLCanvasElement {
  const iw = base.naturalWidth;
  const ih = base.naturalHeight;
  const view = viewport(ops, iw, ih);
  const picture = makeCanvas(view.w, view.h);
  const pctx = picture.getContext("2d");
  if (!pctx) throw new Error("No 2D canvas available.");
  pctx.translate(-view.x, -view.y);
  paintOps(pctx, buildScene(base, iw, ih, ops, sources), ops);
  if (!background.enabled) return picture;

  const layout = frameLayout(picture.width, picture.height, background);
  const framed = makeCanvas(layout.width, layout.height);
  const ctx = framed.getContext("2d");
  if (!ctx) throw new Error("No 2D canvas available.");
  const preset = presetById(background.preset);
  if (preset.stops.length === 1) {
    ctx.fillStyle = preset.stops[0];
  } else {
    const gradient = ctx.createLinearGradient(0, 0, layout.width, layout.height);
    preset.stops.forEach((stop, i) => gradient.addColorStop(i / (preset.stops.length - 1), stop));
    ctx.fillStyle = gradient;
  }
  ctx.fillRect(0, 0, layout.width, layout.height);
  if (background.shadow) {
    ctx.save();
    ctx.shadowColor = "rgba(0,0,0,0.35)";
    ctx.shadowBlur = Math.max(12, layout.image.x * 0.5);
    ctx.shadowOffsetY = Math.max(4, layout.image.x * 0.12);
    ctx.fillStyle = "#000";
    roundedRect(ctx, layout.image, layout.radius);
    ctx.fill();
    ctx.restore();
  }
  ctx.save();
  roundedRect(ctx, layout.image, layout.radius);
  ctx.clip();
  ctx.drawImage(picture, layout.image.x, layout.image.y);
  ctx.restore();
  return framed;
}

/** The finished picture as a PNG blob. */
export async function exportPng(
  base: HTMLImageElement,
  ops: readonly Op[],
  background: Background,
  sources?: ReadonlyMap<string, CanvasImageSource>,
): Promise<Blob> {
  const canvas = renderResult(base, ops, background, sources);
  return await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Export failed."))), "image/png"),
  );
}
