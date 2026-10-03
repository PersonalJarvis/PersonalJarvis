/**
 * The Jarvis X annotation editor's document model — pure, no DOM.
 *
 * Every shape lives in IMAGE pixel coordinates (the capture's native
 * resolution), so the on-screen zoom never changes what is exported. The
 * document is a list of shapes plus an optional crop; the editor keeps an
 * undo history of whole documents, which stays cheap because shapes are small
 * immutable records and unchanged ones are shared between snapshots.
 *
 * Kept free of React and canvas so the rules — hit testing, resizing, counter
 * numbering, crop and export math, undo — are unit-testable on their own.
 */

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

export type Tool =
  | "select"
  | "rect"
  | "highlight"
  | "ellipse"
  | "arrow"
  | "line"
  | "pen"
  | "marker"
  | "text"
  | "counter"
  | "blur"
  | "crop";

interface ShapeBase {
  id: string;
  color: string;
  /** Stroke width in image pixels. */
  width: number;
  shadow: boolean;
}

export interface BoxShape extends ShapeBase {
  kind: "rect" | "highlight" | "ellipse" | "blur";
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface SegmentShape extends ShapeBase {
  kind: "arrow" | "line";
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface StrokeShape extends ShapeBase {
  kind: "pen" | "marker";
  points: Point[];
}

export interface TextShape extends ShapeBase {
  kind: "text";
  x: number;
  y: number;
  text: string;
  fontSize: number;
}

export interface CounterShape extends ShapeBase {
  kind: "counter";
  x: number;
  y: number;
  n: number;
}

export type Shape = BoxShape | SegmentShape | StrokeShape | TextShape | CounterShape;
export type ShapeKind = Shape["kind"];

export interface EditorDoc {
  shapes: Shape[];
  /** The kept part of the image, in image pixels; null keeps all of it. */
  crop: Rect | null;
}

export const EMPTY_DOC: EditorDoc = { shapes: [], crop: null };

/** Tools that draw a shape (select and crop do not). */
export const DRAWING_TOOLS: readonly Tool[] = [
  "rect",
  "highlight",
  "ellipse",
  "arrow",
  "line",
  "pen",
  "marker",
  "text",
  "counter",
  "blur",
];

/** One key per tool, shown in the toolbar tooltips. */
export const TOOL_SHORTCUTS: Record<Tool, string> = {
  select: "V",
  rect: "R",
  highlight: "H",
  ellipse: "O",
  arrow: "A",
  line: "L",
  pen: "P",
  marker: "M",
  text: "T",
  counter: "N",
  blur: "B",
  crop: "C",
};

export function toolForKey(key: string): Tool | null {
  const upper = key.toUpperCase();
  const hit = (Object.keys(TOOL_SHORTCUTS) as Tool[]).find((tool) => TOOL_SHORTCUTS[tool] === upper);
  return hit ?? null;
}

/** The annotation palette. Content colours drawn INTO the image, not theme ink. */
export const PALETTE = [
  "#ff3b30",
  "#ff9500",
  "#ffcc00",
  "#34c759",
  "#0a84ff",
  "#af52de",
  "#ffffff",
  "#1c1c1e",
] as const;

export const DEFAULT_COLOR = PALETTE[0];
export const STROKE_WIDTHS = [2, 4, 6, 10] as const;
export const DEFAULT_STROKE = 4;
export const FONT_SIZES = [16, 24, 32, 48, 72] as const;
export const DEFAULT_FONT_SIZE = 32;
/** Opacity of the translucent marker tools. */
export const HIGHLIGHT_ALPHA = 0.35;
/** Smallest box a drag must span to become a shape, in image pixels. */
export const MIN_DRAG = 3;

let idCounter = 0;
export function newShapeId(): string {
  idCounter += 1;
  return `s${Date.now().toString(36)}${idCounter.toString(36)}`;
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

/** A rect with non-negative width and height from any two corners. */
export function rectFromPoints(a: Point, b: Point): Rect {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    w: Math.abs(b.x - a.x),
    h: Math.abs(b.y - a.y),
  };
}

export function normalizeRect(r: Rect): Rect {
  return rectFromPoints({ x: r.x, y: r.y }, { x: r.x + r.w, y: r.y + r.h });
}

/** Intersect a rect with the image so a crop can never reach outside it. */
export function clampRect(r: Rect, width: number, height: number): Rect {
  const n = normalizeRect(r);
  const x1 = Math.max(0, Math.min(width, n.x));
  const y1 = Math.max(0, Math.min(height, n.y));
  const x2 = Math.max(0, Math.min(width, n.x + n.w));
  const y2 = Math.max(0, Math.min(height, n.y + n.h));
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

/** Whole-pixel crop inside the image, or null when it is too small to keep. */
export function cropFromDrag(a: Point, b: Point, width: number, height: number): Rect | null {
  const r = clampRect(rectFromPoints(a, b), width, height);
  const x = Math.round(r.x);
  const y = Math.round(r.y);
  const w = Math.round(r.x + r.w) - x;
  const h = Math.round(r.y + r.h) - y;
  if (w < MIN_DRAG * 2 || h < MIN_DRAG * 2) return null;
  return { x, y, w, h };
}

/** The part of the image the document shows and exports. */
export function visibleArea(doc: EditorDoc, width: number, height: number): Rect {
  return doc.crop ?? { x: 0, y: 0, w: width, h: height };
}

/** Exported bitmap size — always native resolution, never the view zoom. */
export function exportSize(doc: EditorDoc, width: number, height: number): { width: number; height: number } {
  const area = visibleArea(doc, width, height);
  return { width: Math.max(1, Math.round(area.w)), height: Math.max(1, Math.round(area.h)) };
}

/** Scale that fits content into a box with a margin; never enlarges past 1. */
export function fitScale(
  boxW: number,
  boxH: number,
  contentW: number,
  contentH: number,
  margin = 24,
): number {
  if (contentW <= 0 || contentH <= 0) return 1;
  const w = Math.max(1, boxW - margin * 2);
  const h = Math.max(1, boxH - margin * 2);
  return Math.min(1, w / contentW, h / contentH);
}

export const MIN_ZOOM = 0.05;
export const MAX_ZOOM = 8;
const ZOOM_STEPS = [0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8];

export function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

/** Next zoom step in a direction, from wherever the zoom is now. */
export function stepZoom(current: number, direction: 1 | -1): number {
  if (direction > 0) return ZOOM_STEPS.find((z) => z > current + 1e-6) ?? MAX_ZOOM;
  const lower = [...ZOOM_STEPS].reverse().find((z) => z < current - 1e-6);
  return lower ?? Math.min(current, ZOOM_STEPS[0]);
}

/** View (CSS pixel, relative to the canvas) → image pixels. */
export function viewToImage(p: Point, scale: number, area: Rect): Point {
  return { x: p.x / scale + area.x, y: p.y / scale + area.y };
}

/** Image pixels → view (CSS pixel, relative to the canvas). */
export function imageToView(p: Point, scale: number, area: Rect): Point {
  return { x: (p.x - area.x) * scale, y: (p.y - area.y) * scale };
}

/** Rough text box: the editor measures real text; this keeps tests DOM-free. */
export function estimateTextSize(text: string, fontSize: number): { w: number; h: number } {
  const lines = (text || " ").split("\n");
  const longest = Math.max(...lines.map((line) => line.length), 1);
  return { w: longest * fontSize * 0.58, h: lines.length * fontSize * 1.25 };
}

export function counterRadius(shape: Pick<CounterShape, "width">): number {
  return 12 + shape.width * 2;
}

export function shapeBounds(
  shape: Shape,
  measure: (s: TextShape) => { w: number; h: number } = (s) => estimateTextSize(s.text, s.fontSize),
): Rect {
  switch (shape.kind) {
    case "rect":
    case "highlight":
    case "ellipse":
    case "blur":
      return normalizeRect(shape);
    case "arrow":
    case "line":
      return rectFromPoints({ x: shape.x1, y: shape.y1 }, { x: shape.x2, y: shape.y2 });
    case "pen":
    case "marker": {
      if (shape.points.length === 0) return { x: 0, y: 0, w: 0, h: 0 };
      const xs = shape.points.map((p) => p.x);
      const ys = shape.points.map((p) => p.y);
      const x = Math.min(...xs);
      const y = Math.min(...ys);
      return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
    }
    case "text": {
      const size = measure(shape);
      return { x: shape.x, y: shape.y, w: size.w, h: size.h };
    }
    case "counter": {
      const r = counterRadius(shape);
      return { x: shape.x - r, y: shape.y - r, w: r * 2, h: r * 2 };
    }
  }
}

function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function inside(p: Point, r: Rect, pad = 0): boolean {
  return p.x >= r.x - pad && p.x <= r.x + r.w + pad && p.y >= r.y - pad && p.y <= r.y + r.h + pad;
}

/** Does a point (image pixels) touch this shape? `tolerance` in image pixels. */
export function hitShape(
  shape: Shape,
  p: Point,
  tolerance: number,
  measure?: (s: TextShape) => { w: number; h: number },
): boolean {
  const reach = tolerance + shape.width / 2;
  switch (shape.kind) {
    case "highlight":
    case "blur":
    case "text":
    case "counter":
      // Filled things are grabbed anywhere inside.
      return inside(p, shapeBounds(shape, measure), tolerance);
    case "rect": {
      const r = normalizeRect(shape);
      if (!inside(p, r, reach)) return false;
      // Hollow: only the outline, so a click inside selects what is under it.
      return (
        Math.abs(p.x - r.x) <= reach ||
        Math.abs(p.x - (r.x + r.w)) <= reach ||
        Math.abs(p.y - r.y) <= reach ||
        Math.abs(p.y - (r.y + r.h)) <= reach
      );
    }
    case "ellipse": {
      const r = normalizeRect(shape);
      const rx = r.w / 2;
      const ry = r.h / 2;
      if (rx <= 0 || ry <= 0) return false;
      const cx = r.x + rx;
      const cy = r.y + ry;
      const d = Math.hypot((p.x - cx) / rx, (p.y - cy) / ry);
      // Distance from the outline, approximated in the ellipse's own scale.
      return Math.abs(d - 1) * Math.min(rx, ry) <= reach;
    }
    case "arrow":
    case "line":
      return distanceToSegment(p, { x: shape.x1, y: shape.y1 }, { x: shape.x2, y: shape.y2 }) <= reach;
    case "pen":
    case "marker": {
      const pts = shape.points;
      if (pts.length === 1) return Math.hypot(p.x - pts[0].x, p.y - pts[0].y) <= reach;
      for (let i = 1; i < pts.length; i += 1) {
        if (distanceToSegment(p, pts[i - 1], pts[i]) <= reach) return true;
      }
      return false;
    }
  }
}

/** Topmost shape under the point, or null. Later shapes paint on top. */
export function hitTest(
  shapes: readonly Shape[],
  p: Point,
  tolerance: number,
  measure?: (s: TextShape) => { w: number; h: number },
): string | null {
  for (let i = shapes.length - 1; i >= 0; i -= 1) {
    if (hitShape(shapes[i], p, tolerance, measure)) return shapes[i].id;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Handles and transforms
// ---------------------------------------------------------------------------

export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "start" | "end";

/** The resize handles a shape offers, with their image-pixel positions. */
export function shapeHandles(shape: Shape): { handle: Handle; x: number; y: number }[] {
  if (shape.kind === "arrow" || shape.kind === "line") {
    return [
      { handle: "start", x: shape.x1, y: shape.y1 },
      { handle: "end", x: shape.x2, y: shape.y2 },
    ];
  }
  if (shape.kind === "rect" || shape.kind === "highlight" || shape.kind === "ellipse" || shape.kind === "blur") {
    const r = normalizeRect(shape);
    const mx = r.x + r.w / 2;
    const my = r.y + r.h / 2;
    return [
      { handle: "nw", x: r.x, y: r.y },
      { handle: "n", x: mx, y: r.y },
      { handle: "ne", x: r.x + r.w, y: r.y },
      { handle: "e", x: r.x + r.w, y: my },
      { handle: "se", x: r.x + r.w, y: r.y + r.h },
      { handle: "s", x: mx, y: r.y + r.h },
      { handle: "sw", x: r.x, y: r.y + r.h },
      { handle: "w", x: r.x, y: my },
    ];
  }
  // Strokes, text and counters move as a whole; their size is a style.
  return [];
}

export function hitHandle(shape: Shape, p: Point, tolerance: number): Handle | null {
  const hit = shapeHandles(shape).find((h) => Math.hypot(p.x - h.x, p.y - h.y) <= tolerance);
  return hit ? hit.handle : null;
}

/** Move a shape by an offset. Returns a new shape. */
export function moveShape<S extends Shape>(shape: S, dx: number, dy: number): S {
  switch (shape.kind) {
    case "arrow":
    case "line":
      return { ...shape, x1: shape.x1 + dx, y1: shape.y1 + dy, x2: shape.x2 + dx, y2: shape.y2 + dy };
    case "pen":
    case "marker":
      return { ...shape, points: shape.points.map((p) => ({ x: p.x + dx, y: p.y + dy })) };
    default:
      return { ...shape, x: (shape as BoxShape).x + dx, y: (shape as BoxShape).y + dy };
  }
}

/**
 * Drag one handle of `original` to `p`. Box shapes may flip through zero —
 * the result is normalised, so the box stays a valid rect either way.
 */
export function resizeShape<S extends Shape>(original: S, handle: Handle, p: Point): S {
  if (original.kind === "arrow" || original.kind === "line") {
    if (handle === "start") return { ...original, x1: p.x, y1: p.y };
    if (handle === "end") return { ...original, x2: p.x, y2: p.y };
    return original;
  }
  if (
    original.kind !== "rect" &&
    original.kind !== "highlight" &&
    original.kind !== "ellipse" &&
    original.kind !== "blur"
  ) {
    return original;
  }
  const r = normalizeRect(original);
  let x1 = r.x;
  let y1 = r.y;
  let x2 = r.x + r.w;
  let y2 = r.y + r.h;
  if (handle.includes("w")) x1 = p.x;
  if (handle.includes("e")) x2 = p.x;
  if (handle.includes("n")) y1 = p.y;
  if (handle.includes("s")) y2 = p.y;
  return { ...original, ...rectFromPoints({ x: x1, y: y1 }, { x: x2, y: y2 }) };
}

// ---------------------------------------------------------------------------
// Document edits
// ---------------------------------------------------------------------------

export function addShape(doc: EditorDoc, shape: Shape): EditorDoc {
  return { ...doc, shapes: [...doc.shapes, shape] };
}

export function updateShape(doc: EditorDoc, id: string, patch: (shape: Shape) => Shape): EditorDoc {
  let changed = false;
  const shapes = doc.shapes.map((shape) => {
    if (shape.id !== id) return shape;
    const next = patch(shape);
    if (next !== shape) changed = true;
    return next;
  });
  return changed ? { ...doc, shapes } : doc;
}

export function deleteShape(doc: EditorDoc, id: string): EditorDoc {
  const shapes = doc.shapes.filter((shape) => shape.id !== id);
  return shapes.length === doc.shapes.length ? doc : { ...doc, shapes };
}

export function setCrop(doc: EditorDoc, crop: Rect | null): EditorDoc {
  return { ...doc, crop };
}

/** The number the next counter badge gets: one past the highest on the page. */
export function nextCounterNumber(shapes: readonly Shape[]): number {
  let max = 0;
  for (const shape of shapes) if (shape.kind === "counter" && shape.n > max) max = shape.n;
  return max + 1;
}

/** Apply a style change to a shape, touching only what that kind uses. */
export function restyleShape(
  shape: Shape,
  style: Partial<{ color: string; width: number; shadow: boolean; fontSize: number }>,
): Shape {
  const next: Shape = { ...shape };
  if (style.color !== undefined && shape.kind !== "blur") next.color = style.color;
  if (style.width !== undefined) next.width = style.width;
  if (style.shadow !== undefined) next.shadow = style.shadow;
  if (style.fontSize !== undefined && next.kind === "text") next.fontSize = style.fontSize;
  return next;
}

/** Is a freshly drawn shape big enough to keep (a click is not a rectangle)? */
export function isMeaningful(shape: Shape): boolean {
  switch (shape.kind) {
    case "rect":
    case "highlight":
    case "ellipse":
    case "blur":
      return Math.abs(shape.w) >= MIN_DRAG && Math.abs(shape.h) >= MIN_DRAG;
    case "arrow":
    case "line":
      return Math.hypot(shape.x2 - shape.x1, shape.y2 - shape.y1) >= MIN_DRAG * 2;
    case "pen":
    case "marker":
      return shape.points.length >= 2;
    case "text":
      return shape.text.trim().length > 0;
    case "counter":
      return true;
  }
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

export interface History {
  past: EditorDoc[];
  present: EditorDoc;
  future: EditorDoc[];
}

export const HISTORY_LIMIT = 200;

export function createHistory(doc: EditorDoc = EMPTY_DOC): History {
  return { past: [], present: doc, future: [] };
}

/** A new undoable state. A no-op edit (same object) records nothing. */
export function commit(history: History, next: EditorDoc): History {
  if (next === history.present) return history;
  const past = [...history.past, history.present];
  if (past.length > HISTORY_LIMIT) past.splice(0, past.length - HISTORY_LIMIT);
  return { past, present: next, future: [] };
}

/** Replace the present without a history entry — the live frames of a drag. */
export function replacePresent(history: History, next: EditorDoc): History {
  return next === history.present ? history : { ...history, present: next };
}

/**
 * Close a gesture whose live frames went through `replacePresent`: ONE undo
 * step back to `before`, however many frames the drag painted.
 */
export function finishGesture(history: History, before: EditorDoc): History {
  if (history.present === before) return history;
  const past = [...history.past, before];
  if (past.length > HISTORY_LIMIT) past.splice(0, past.length - HISTORY_LIMIT);
  return { past, present: history.present, future: [] };
}

export function undo(history: History): History {
  if (history.past.length === 0) return history;
  const previous = history.past[history.past.length - 1];
  return {
    past: history.past.slice(0, -1),
    present: previous,
    future: [history.present, ...history.future],
  };
}

export function redo(history: History): History {
  if (history.future.length === 0) return history;
  const [next, ...rest] = history.future;
  return { past: [...history.past, history.present], present: next, future: rest };
}

export const canUndo = (history: History) => history.past.length > 0;
export const canRedo = (history: History) => history.future.length > 0;

/** Anything to save — a shape or a crop on the page. */
export function isDirty(doc: EditorDoc): boolean {
  return doc.shapes.length > 0 || doc.crop !== null;
}

// ---------------------------------------------------------------------------
// Drawing helpers shared by the view and the export
// ---------------------------------------------------------------------------

/** The two barb points of an arrow head at (x2, y2). */
export function arrowHead(shape: Pick<SegmentShape, "x1" | "y1" | "x2" | "y2" | "width">): {
  left: Point;
  right: Point;
  length: number;
} {
  const angle = Math.atan2(shape.y2 - shape.y1, shape.x2 - shape.x1);
  const length = Math.max(12, shape.width * 4);
  const spread = Math.PI / 7;
  return {
    left: { x: shape.x2 - length * Math.cos(angle - spread), y: shape.y2 - length * Math.sin(angle - spread) },
    right: { x: shape.x2 - length * Math.cos(angle + spread), y: shape.y2 - length * Math.sin(angle + spread) },
    length,
  };
}

/** Pixelation block size for a blur region, from the stroke width setting. */
export function pixelBlock(shape: Pick<BoxShape, "width">): number {
  return Math.max(6, shape.width * 3);
}
