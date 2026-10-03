/**
 * The appshot editor's document: a base picture plus a list of annotations,
 * drawn in the picture's own pixel space, and an optional background frame
 * around the result. Pure data + one renderer, so the on-screen canvas and
 * the exported PNG are painted by the same code.
 *
 * The tool set and its one-letter keys follow CleanShot X's annotate tool
 * (V move, A arrow, L line, R rectangle, F filled rectangle, E ellipse,
 * D draw, M highlighter, T text, C counter, H spotlight, P redact, K crop,
 * B background), so anyone who knows it can work here without looking.
 *
 * A crop is an annotation too: the last one sets the visible part of the
 * picture, and undo brings the rest back.
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

type Shape =
  | { kind: "arrow" | "line"; from: Point; to: Point; color: string; width: number }
  | { kind: "rect" | "filled" | "ellipse"; rect: Rect; color: string; width: number }
  | { kind: "pen" | "highlight"; points: Point[]; color: string; width: number }
  | { kind: "text"; at: Point; text: string; color: string; size: number; style: TextStyle }
  | { kind: "counter"; at: Point; n: number; color: string; size: number }
  | { kind: "spotlight"; rect: Rect }
  | { kind: "redact"; rect: Rect; mode: RedactMode; block: number }
  | { kind: "crop"; rect: Rect };

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

/** The visible part of the picture: the last crop, or all of it. */
export function viewport(ops: readonly Op[], width: number, height: number): Rect {
  for (let i = ops.length - 1; i >= 0; i -= 1) {
    const op = ops[i];
    if (op.kind === "crop") return op.rect;
  }
  return { x: 0, y: 0, w: width, h: height };
}

/** Clamp a rect to the picture; `null` when nothing of it is left. */
export function clampRect(rect: Rect, width: number, height: number): Rect | null {
  const x = Math.max(0, Math.min(width, rect.x));
  const y = Math.max(0, Math.min(height, rect.y));
  const right = Math.max(0, Math.min(width, rect.x + rect.w));
  const bottom = Math.max(0, Math.min(height, rect.y + rect.h));
  if (right - x < 2 || bottom - y < 2) return null;
  return { x, y, w: right - x, h: bottom - y };
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
    case "line":
      return rectFrom(op.from, op.to);
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
const AREA_KINDS: ReadonlySet<Op["kind"]> = new Set(["spotlight", "redact"]);

function hits(op: Op, p: Point, slop: number, measure?: MeasureText): boolean {
  switch (op.kind) {
    case "crop":
      return false;
    case "arrow":
    case "line":
      return distanceToSegment(p, op.from, op.to) <= slop + op.width;
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
export function hitTest(ops: readonly Op[], p: Point, slop: number, measure?: MeasureText): Op | null {
  for (const areas of [false, true]) {
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
      return { ...op, from: shiftPoint(op.from, dx, dy), to: shiftPoint(op.to, dx, dy) };
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
 * Hide a region. Pixelate keeps hard blocks; blur shrinks and grows the
 * region twice with smoothing, which every engine supports (canvas `filter`
 * is missing in some WebKit builds the macOS and Linux shells use).
 */
function redact(ctx: CanvasRenderingContext2D, base: CanvasImageSource, rect: Rect, mode: RedactMode, block: number) {
  const w = Math.max(1, Math.round(rect.w / block));
  const h = Math.max(1, Math.round(rect.h / block));
  const small = makeCanvas(w, h);
  const sctx = small.getContext("2d");
  if (!sctx) return;
  sctx.imageSmoothingEnabled = true;
  sctx.drawImage(base, rect.x, rect.y, rect.w, rect.h, 0, 0, w, h);
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

/** Paint one annotation (crops and spotlights excluded — see paintOps). */
export function paintShape(ctx: CanvasRenderingContext2D, base: CanvasImageSource, op: Draft) {
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  switch (op.kind) {
    case "arrow":
      ctx.strokeStyle = op.color;
      ctx.fillStyle = op.color;
      ctx.lineWidth = op.width;
      arrow(ctx, op.from, op.to, op.width);
      break;
    case "line":
      ctx.strokeStyle = op.color;
      ctx.lineWidth = op.width;
      ctx.beginPath();
      ctx.moveTo(op.from.x, op.from.y);
      ctx.lineTo(op.to.x, op.to.y);
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
      redact(ctx, base, op.rect, op.mode, op.block);
      break;
    case "spotlight":
    case "crop":
      break;
  }
  ctx.restore();
}

/** Dim everything outside the spotlights, in one layer so they never stack. */
function paintSpotlights(ctx: CanvasRenderingContext2D, spots: Rect[], width: number, height: number) {
  if (spots.length === 0) return;
  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.beginPath();
  ctx.rect(0, 0, width, height);
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
 * Paint the picture and every annotation in picture space. The caller sets
 * the transform: identity for an export, a scale + offset for the screen.
 */
export function paintOps(
  ctx: CanvasRenderingContext2D,
  base: CanvasImageSource,
  ops: readonly Draft[],
  size: { width: number; height: number },
) {
  ctx.drawImage(base, 0, 0);
  const spots: Rect[] = [];
  for (const op of ops) {
    if (op.kind === "spotlight") spots.push(op.rect);
    else paintShape(ctx, base, op);
  }
  paintSpotlights(ctx, spots, size.width, size.height);
}

/** Render the finished picture: cropped, annotated and framed. */
export function renderResult(
  base: HTMLImageElement,
  ops: readonly Op[],
  background: Background,
): HTMLCanvasElement {
  const iw = base.naturalWidth;
  const ih = base.naturalHeight;
  const view = viewport(ops, iw, ih);
  const picture = makeCanvas(view.w, view.h);
  const pctx = picture.getContext("2d");
  if (!pctx) throw new Error("No 2D canvas available.");
  pctx.translate(-view.x, -view.y);
  paintOps(pctx, base, ops, { width: iw, height: ih });
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
export async function exportPng(base: HTMLImageElement, ops: readonly Op[], background: Background): Promise<Blob> {
  const canvas = renderResult(base, ops, background);
  return await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Export failed."))), "image/png"),
  );
}
