/**
 * Canvas painting for the Jarvis X editor.
 *
 * One painter for both the screen and the export: the caller sets a transform
 * that maps image pixels onto its canvas (zoomed and high-DPI on screen, 1:1
 * for the export), then `paintDocument` draws the base image, every blur
 * region, and the shapes on top. Because the export goes through the same
 * code, what the user sees is what gets saved.
 */
import {
  HIGHLIGHT_ALPHA,
  arrowHead,
  counterRadius,
  exportSize,
  pixelBlock,
  visibleArea,
  type BoxShape,
  type EditorDoc,
  type Shape,
  type TextShape,
} from "@/lib/jarvisxEditorModel";

export const TEXT_FONT_FAMILY = '"Inter Variable", Inter, system-ui, sans-serif';
const TEXT_LINE_HEIGHT = 1.25;

export function textFont(fontSize: number): string {
  return `600 ${fontSize}px ${TEXT_FONT_FAMILY}`;
}

let measureCtx: CanvasRenderingContext2D | null = null;

/** Real text box for a text shape, measured with the font it is drawn in. */
export function measureTextShape(shape: Pick<TextShape, "text" | "fontSize">): { w: number; h: number } {
  const lines = (shape.text || " ").split("\n");
  if (!measureCtx && typeof document !== "undefined") {
    measureCtx = document.createElement("canvas").getContext("2d");
  }
  if (!measureCtx) {
    const longest = Math.max(...lines.map((l) => l.length), 1);
    return { w: longest * shape.fontSize * 0.58, h: lines.length * shape.fontSize * TEXT_LINE_HEIGHT };
  }
  measureCtx.font = textFont(shape.fontSize);
  const w = Math.max(...lines.map((line) => measureCtx!.measureText(line || " ").width), shape.fontSize * 0.5);
  return { w, h: lines.length * shape.fontSize * TEXT_LINE_HEIGHT };
}

/** Black or white, whichever reads on top of `hex`. */
export function inkOn(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return "#ffffff";
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.62 ? "#1c1c1e" : "#ffffff";
}

function withShadow(ctx: CanvasRenderingContext2D, shape: Shape, draw: () => void) {
  ctx.save();
  if (shape.shadow) {
    ctx.shadowColor = "rgba(0, 0, 0, 0.45)";
    ctx.shadowBlur = Math.max(6, shape.width * 2.5);
    ctx.shadowOffsetY = Math.max(2, shape.width * 0.6);
  }
  draw();
  ctx.restore();
}

function paintBlur(ctx: CanvasRenderingContext2D, image: CanvasImageSource, shape: BoxShape) {
  const w = Math.round(Math.abs(shape.w));
  const h = Math.round(Math.abs(shape.h));
  if (w < 1 || h < 1 || typeof document === "undefined") return;
  const x = Math.round(Math.min(shape.x, shape.x + shape.w));
  const y = Math.round(Math.min(shape.y, shape.y + shape.h));
  const block = pixelBlock(shape);
  const small = document.createElement("canvas");
  small.width = Math.max(1, Math.ceil(w / block));
  small.height = Math.max(1, Math.ceil(h / block));
  const sctx = small.getContext("2d");
  if (!sctx) return;
  sctx.imageSmoothingEnabled = true;
  sctx.drawImage(image, x, y, w, h, 0, 0, small.width, small.height);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(small, 0, 0, small.width, small.height, x, y, w, h);
  ctx.restore();
}

function strokePath(ctx: CanvasRenderingContext2D, points: { x: number; y: number }[]) {
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  if (points.length === 1) {
    ctx.lineTo(points[0].x + 0.01, points[0].y);
  } else {
    // Midpoint quadratic smoothing: a hand-drawn line without the jaggies.
    for (let i = 1; i < points.length - 1; i += 1) {
      const mx = (points[i].x + points[i + 1].x) / 2;
      const my = (points[i].y + points[i + 1].y) / 2;
      ctx.quadraticCurveTo(points[i].x, points[i].y, mx, my);
    }
    const last = points[points.length - 1];
    ctx.lineTo(last.x, last.y);
  }
  ctx.stroke();
}

export function paintShape(ctx: CanvasRenderingContext2D, shape: Shape) {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  switch (shape.kind) {
    case "blur":
      return;
    case "rect":
      withShadow(ctx, shape, () => {
        ctx.strokeStyle = shape.color;
        ctx.lineWidth = shape.width;
        ctx.lineJoin = "miter";
        ctx.strokeRect(shape.x, shape.y, shape.w, shape.h);
      });
      return;
    case "highlight":
      ctx.save();
      ctx.globalAlpha = HIGHLIGHT_ALPHA;
      ctx.fillStyle = shape.color;
      ctx.fillRect(shape.x, shape.y, shape.w, shape.h);
      ctx.restore();
      return;
    case "ellipse":
      withShadow(ctx, shape, () => {
        ctx.strokeStyle = shape.color;
        ctx.lineWidth = shape.width;
        ctx.beginPath();
        ctx.ellipse(
          shape.x + shape.w / 2,
          shape.y + shape.h / 2,
          Math.abs(shape.w / 2),
          Math.abs(shape.h / 2),
          0,
          0,
          Math.PI * 2,
        );
        ctx.stroke();
      });
      return;
    case "line":
      withShadow(ctx, shape, () => {
        ctx.strokeStyle = shape.color;
        ctx.lineWidth = shape.width;
        ctx.beginPath();
        ctx.moveTo(shape.x1, shape.y1);
        ctx.lineTo(shape.x2, shape.y2);
        ctx.stroke();
      });
      return;
    case "arrow":
      withShadow(ctx, shape, () => {
        const head = arrowHead(shape);
        const angle = Math.atan2(shape.y2 - shape.y1, shape.x2 - shape.x1);
        // Stop the shaft inside the head so the tip stays sharp.
        const inset = head.length * 0.6;
        ctx.strokeStyle = shape.color;
        ctx.fillStyle = shape.color;
        ctx.lineWidth = shape.width;
        ctx.beginPath();
        ctx.moveTo(shape.x1, shape.y1);
        ctx.lineTo(shape.x2 - inset * Math.cos(angle), shape.y2 - inset * Math.sin(angle));
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(shape.x2, shape.y2);
        ctx.lineTo(head.left.x, head.left.y);
        ctx.lineTo(head.right.x, head.right.y);
        ctx.closePath();
        ctx.fill();
      });
      return;
    case "pen":
      if (shape.points.length === 0) return;
      withShadow(ctx, shape, () => {
        ctx.strokeStyle = shape.color;
        ctx.lineWidth = shape.width;
        strokePath(ctx, shape.points);
      });
      return;
    case "marker":
      if (shape.points.length === 0) return;
      ctx.save();
      ctx.globalAlpha = HIGHLIGHT_ALPHA;
      ctx.strokeStyle = shape.color;
      ctx.lineWidth = shape.width * 4;
      ctx.lineCap = "butt";
      strokePath(ctx, shape.points);
      ctx.restore();
      return;
    case "text":
      withShadow(ctx, shape, () => {
        ctx.font = textFont(shape.fontSize);
        ctx.fillStyle = shape.color;
        ctx.textBaseline = "top";
        shape.text.split("\n").forEach((line, i) => {
          ctx.fillText(line, shape.x, shape.y + i * shape.fontSize * TEXT_LINE_HEIGHT);
        });
      });
      return;
    case "counter": {
      const r = counterRadius(shape);
      withShadow(ctx, shape, () => {
        ctx.fillStyle = shape.color;
        ctx.beginPath();
        ctx.arc(shape.x, shape.y, r, 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.save();
      ctx.fillStyle = inkOn(shape.color);
      ctx.font = `700 ${Math.round(r * 1.1)}px ${TEXT_FONT_FAMILY}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(shape.n), shape.x, shape.y + r * 0.05);
      ctx.restore();
      return;
    }
  }
}

/**
 * Paint base image + blur + shapes. The context's transform must already map
 * image pixels to its own space; `skipId` leaves out the shape being edited
 * in place (the text field shows it instead).
 */
export function paintDocument(
  ctx: CanvasRenderingContext2D,
  image: CanvasImageSource,
  doc: EditorDoc,
  skipId?: string | null,
) {
  ctx.drawImage(image, 0, 0);
  for (const shape of doc.shapes) {
    if (shape.kind === "blur" && shape.id !== skipId) paintBlur(ctx, image, shape);
  }
  for (const shape of doc.shapes) {
    if (shape.kind !== "blur" && shape.id !== skipId) paintShape(ctx, shape);
  }
}

/** Flatten the document into a PNG at the capture's native resolution. */
export function exportPng(image: HTMLImageElement, doc: EditorDoc): Promise<Blob> {
  const natW = image.naturalWidth;
  const natH = image.naturalHeight;
  const size = exportSize(doc, natW, natH);
  const area = visibleArea(doc, natW, natH);
  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.reject(new Error("Canvas is unavailable."));
  ctx.setTransform(1, 0, 0, 1, -Math.round(area.x), -Math.round(area.y));
  paintDocument(ctx, image, doc);
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Export failed."))), "image/png");
  });
}
