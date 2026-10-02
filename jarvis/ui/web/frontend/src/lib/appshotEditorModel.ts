/**
 * The appshot editor's document: a base picture plus a list of operations,
 * drawn in the picture's own pixel space. Pure data + one renderer, so the
 * on-screen canvas and the exported PNG are painted by the same code.
 *
 * A crop is an operation too: the last one sets the visible part of the
 * picture, and undo brings the rest back.
 */

export type Tool = "arrow" | "rect" | "ellipse" | "pen" | "highlight" | "text" | "pixelate" | "crop";

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

export type Op =
  | { kind: "arrow"; from: Point; to: Point; color: string; width: number }
  | { kind: "rect" | "ellipse"; rect: Rect; color: string; width: number }
  | { kind: "pen" | "highlight"; points: Point[]; color: string; width: number }
  | { kind: "text"; at: Point; text: string; color: string; size: number }
  | { kind: "pixelate"; rect: Rect; block: number }
  | { kind: "crop"; rect: Rect };

export const COLORS = ["#ef4444", "#f97316", "#facc15", "#22c55e", "#3b82f6", "#ffffff", "#111111"];

/** Stroke widths (S / M / L) for a picture of this size, in its own pixels. */
export function strokeWidths(width: number, height: number): [number, number, number] {
  const unit = Math.max(1, Math.max(width, height) / 1400);
  return [Math.round(3 * unit), Math.round(6 * unit), Math.round(11 * unit)];
}

export function textSize(stroke: number): number {
  return Math.max(14, Math.round(stroke * 5));
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

/** The visible part of the picture: the last crop, or all of it. */
export function viewport(ops: Op[], width: number, height: number): Rect {
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

function pixelate(ctx: CanvasRenderingContext2D, base: CanvasImageSource, rect: Rect, block: number) {
  const w = Math.max(1, Math.round(rect.w / block));
  const h = Math.max(1, Math.round(rect.h / block));
  const small = document.createElement("canvas");
  small.width = w;
  small.height = h;
  const sctx = small.getContext("2d");
  if (!sctx) return;
  sctx.imageSmoothingEnabled = true;
  sctx.drawImage(base, rect.x, rect.y, rect.w, rect.h, 0, 0, w, h);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(small, 0, 0, w, h, rect.x, rect.y, rect.w, rect.h);
  ctx.restore();
}

/**
 * Paint the picture and every operation (crops excluded) in picture space.
 * The caller sets the transform: identity for an export, a scale + offset
 * for the screen.
 */
export function paintOps(ctx: CanvasRenderingContext2D, base: CanvasImageSource, ops: Op[]) {
  ctx.drawImage(base, 0, 0);
  for (const op of ops) {
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
      case "rect":
        ctx.strokeStyle = op.color;
        ctx.lineWidth = op.width;
        ctx.strokeRect(op.rect.x, op.rect.y, op.rect.w, op.rect.h);
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
          ctx.globalAlpha = 0.35;
          ctx.lineCap = "square";
        }
        ctx.beginPath();
        ctx.moveTo(op.points[0].x, op.points[0].y);
        for (const p of op.points.slice(1)) ctx.lineTo(p.x, p.y);
        if (op.points.length === 1) ctx.lineTo(op.points[0].x + 0.1, op.points[0].y);
        ctx.stroke();
        break;
      case "text": {
        ctx.font = `600 ${op.size}px Inter, "Segoe UI", system-ui, sans-serif`;
        ctx.textBaseline = "top";
        ctx.fillStyle = op.color;
        ctx.shadowColor = "rgba(0,0,0,0.45)";
        ctx.shadowBlur = Math.max(2, op.size / 8);
        op.text.split("\n").forEach((line, i) => {
          ctx.fillText(line, op.at.x, op.at.y + i * op.size * 1.25);
        });
        break;
      }
      case "pixelate":
        pixelate(ctx, base, op.rect, op.block);
        break;
      case "crop":
        break;
    }
    ctx.restore();
  }
}

/** Render the finished picture (cropped, with every edit) to a PNG blob. */
export async function exportPng(
  base: HTMLImageElement,
  ops: Op[],
): Promise<Blob> {
  const view = viewport(ops, base.naturalWidth, base.naturalHeight);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(view.w);
  canvas.height = Math.round(view.h);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("No 2D canvas available.");
  ctx.translate(-view.x, -view.y);
  paintOps(ctx, base, ops);
  return await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Export failed."))), "image/png"),
  );
}
