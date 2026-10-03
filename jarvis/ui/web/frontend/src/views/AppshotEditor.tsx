import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Check,
  Circle,
  Copy,
  Crop,
  Download,
  Grid3x3,
  Highlighter,
  Loader2,
  MoveUpRight,
  Pencil,
  Redo2,
  Square,
  Type,
  Undo2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useT } from "@/i18n";
import { latestAppshotImageUrl } from "@/lib/appshotApi";
import {
  COLORS,
  clampRect,
  exportPng,
  paintOps,
  rectFrom,
  strokeWidths,
  textSize,
  viewport,
  type Op,
  type Point,
  type Tool,
} from "@/lib/appshotEditorModel";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

/**
 * The appshot editor — annotate the last appshot with arrow, rectangle,
 * ellipse, pen, highlighter, text, pixelate and
 * crop, with undo/redo. The result can be copied, saved, or put back in place
 * of the appshot so the next message carries the edited picture.
 *
 * Everything happens in this window's memory; only "Save" writes a file (a
 * normal download the user asked for).
 *
 * Rendered in place (a fixed full-window layer), not portalled: inside the
 * Settings dialog it then counts as that dialog's nested modal, so its clicks
 * and Escape never read as "outside" and its text box can take focus.
 */

const TOOLS: { tool: Tool; icon: typeof Square; key: string }[] = [
  { tool: "arrow", icon: MoveUpRight, key: "a" },
  { tool: "rect", icon: Square, key: "r" },
  { tool: "ellipse", icon: Circle, key: "e" },
  { tool: "pen", icon: Pencil, key: "p" },
  { tool: "highlight", icon: Highlighter, key: "h" },
  { tool: "text", icon: Type, key: "t" },
  { tool: "pixelate", icon: Grid3x3, key: "b" },
  { tool: "crop", icon: Crop, key: "c" },
];

interface Draft {
  op: Op;
  start: Point;
}

function stamp(): string {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-");
}

export function AppshotEditor({ appshotId, onClose }: { appshotId: string; onClose: () => void }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [ops, setOps] = useState<Op[]>([]);
  const [redo, setRedo] = useState<Op[]>([]);
  const [tool, setTool] = useState<Tool>("arrow");
  const [color, setColor] = useState(COLORS[0]);
  const [widthIndex, setWidthIndex] = useState(1);
  const [draft, setDraftState] = useState<Draft | null>(null);
  // Pointer events can outrun React's re-render (a quick flick sends its
  // moves and the release before the press has rendered), so the handlers
  // read and write the draft through a ref; the state only drives painting.
  const draftRef = useRef<Draft | null>(null);
  const setDraft = useCallback((next: Draft | null) => {
    draftRef.current = next;
    setDraftState(next);
  }, []);
  const [typing, setTyping] = useState<{ at: Point; text: string } | null>(null);
  const [busy, setBusy] = useState<"" | "copy" | "save" | "apply">("");
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [stage, setStage] = useState({ w: 0, h: 0 });

  // -- load ----------------------------------------------------------------
  useEffect(() => {
    let alive = true;
    const img = new Image();
    img.onload = () => alive && setImage(img);
    img.onerror = () => alive && setFailed(true);
    img.src = latestAppshotImageUrl(appshotId);
    return () => {
      alive = false;
    };
  }, [appshotId]);

  useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () => setStage({ w: el.clientWidth, h: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const iw = image?.naturalWidth ?? 0;
  const ih = image?.naturalHeight ?? 0;
  const widths = strokeWidths(iw || 1, ih || 1);
  const width = widths[widthIndex];
  const view = viewport(ops, iw, ih);
  // While cropping, show the whole picture so a crop can also grow back.
  const shown = tool === "crop" ? { x: 0, y: 0, w: iw, h: ih } : view;
  const scale =
    shown.w > 0 && stage.w > 0 ? Math.min((stage.w - 48) / shown.w, (stage.h - 48) / shown.h, 1) : 1;
  const cw = Math.max(1, Math.round(shown.w * scale));
  const ch = Math.max(1, Math.round(shown.h * scale));

  // -- paint ---------------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !image) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cw * dpr);
    canvas.height = Math.round(ch * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr * scale, 0, 0, dpr * scale, -shown.x * dpr * scale, -shown.y * dpr * scale);
    ctx.clearRect(shown.x, shown.y, shown.w, shown.h);
    const drawn = draft && draft.op.kind !== "crop" ? [...ops, draft.op] : ops;
    paintOps(ctx, image, drawn.filter((op) => op.kind !== "crop"));
    if (tool === "crop") {
      const crop = draft?.op.kind === "crop" ? draft.op.rect : view;
      ctx.save();
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.beginPath();
      ctx.rect(0, 0, iw, ih);
      ctx.rect(crop.x, crop.y, crop.w, crop.h);
      ctx.fill("evenodd");
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5 / scale;
      ctx.setLineDash([6 / scale, 4 / scale]);
      ctx.strokeRect(crop.x, crop.y, crop.w, crop.h);
      ctx.restore();
    }
  }, [image, ops, draft, tool, cw, ch, scale, shown.x, shown.y, shown.w, shown.h, iw, ih, view]);

  // -- editing -------------------------------------------------------------
  const push = useCallback((op: Op) => {
    setOps((prev) => [...prev, op]);
    setRedo([]);
  }, []);

  const undo = useCallback(() => {
    if (ops.length === 0) return;
    setRedo([...redo, ops[ops.length - 1]]);
    setOps(ops.slice(0, -1));
  }, [ops, redo]);

  const redoOne = useCallback(() => {
    if (redo.length === 0) return;
    setOps([...ops, redo[redo.length - 1]]);
    setRedo(redo.slice(0, -1));
  }, [ops, redo]);

  const toImage = (event: React.PointerEvent): Point => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return {
      x: shown.x + (event.clientX - rect.left) / scale,
      y: shown.y + (event.clientY - rect.top) / scale,
    };
  };

  const commitText = useCallback(() => {
    setTyping((current) => {
      if (current && current.text.trim()) {
        push({ kind: "text", at: current.at, text: current.text, color, size: textSize(width) });
      }
      return null;
    });
  }, [color, push, width]);

  const onPointerDown = (event: React.PointerEvent) => {
    if (!image || event.button !== 0) return;
    const p = toImage(event);
    if (tool === "text") {
      // Keep the press from moving focus to the dialog behind the canvas: it
      // would blur (and so close) the text box this click is about to open.
      event.preventDefault();
      if (typing) commitText();
      setTyping({ at: p, text: "" });
      return;
    }
    (event.target as Element).setPointerCapture(event.pointerId);
    const zero = { x: p.x, y: p.y, w: 0, h: 0 };
    let op: Op;
    switch (tool) {
      case "arrow":
        op = { kind: "arrow", from: p, to: p, color, width };
        break;
      case "rect":
      case "ellipse":
        op = { kind: tool, rect: zero, color, width };
        break;
      case "pen":
      case "highlight":
        op = { kind: tool, points: [p], color, width };
        break;
      case "pixelate":
        op = { kind: "pixelate", rect: zero, block: Math.max(6, width * 2) };
        break;
      default:
        op = { kind: "crop", rect: zero };
    }
    setDraft({ op, start: p });
  };

  const onPointerMove = (event: React.PointerEvent) => {
    const draft = draftRef.current;
    if (!draft) return;
    const p = toImage(event);
    const op = draft.op;
    let next: Op = op;
    if (op.kind === "arrow") next = { ...op, to: p };
    else if (op.kind === "pen" || op.kind === "highlight") next = { ...op, points: [...op.points, p] };
    else if (op.kind === "rect" || op.kind === "ellipse" || op.kind === "pixelate" || op.kind === "crop") {
      next = { ...op, rect: rectFrom(draft.start, p) };
    }
    setDraft({ ...draft, op: next });
  };

  const onPointerUp = () => {
    const draft = draftRef.current;
    if (!draft) return;
    const op = draft.op;
    setDraft(null);
    if (op.kind === "arrow" && Math.hypot(op.to.x - op.from.x, op.to.y - op.from.y) < 4) return;
    if ("rect" in op) {
      const rect = clampRect(op.rect, iw, ih);
      if (!rect || rect.w < 4 || rect.h < 4) return;
      push({ ...op, rect } as Op);
      if (op.kind === "crop") setTool("arrow");
      return;
    }
    push(op);
  };

  // -- results -------------------------------------------------------------
  const render = useCallback(async () => {
    if (!image) throw new Error(t("appshots.editor.gone"));
    return await exportPng(image, ops);
  }, [image, ops, t]);

  const copy = useCallback(async () => {
    setBusy("copy");
    try {
      const blob = await render();
      // A browser that waits for focus or a permission never settles this
      // promise; the button must not spin forever.
      await Promise.race([
        navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]),
        new Promise((_, reject) =>
          window.setTimeout(() => reject(new Error(t("appshots.editor.copy_timeout"))), 5000),
        ),
      ]);
      pushToast("success", t("appshots.editor.copied"));
    } catch (error) {
      pushToast("error", t("appshots.editor.copy_failed").replace("{0}", (error as Error).message));
    } finally {
      setBusy("");
    }
  }, [pushToast, render, t]);

  const save = useCallback(async () => {
    setBusy("save");
    try {
      const blob = await render();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `appshot-${stamp()}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setBusy("");
    }
  }, [pushToast, render]);

  const apply = useCallback(async () => {
    setBusy("apply");
    try {
      const blob = await render();
      const response = await fetch(`/api/appshot/latest/image?id=${encodeURIComponent(appshotId)}`, {
        method: "PUT",
        headers: { "content-type": "image/png" },
        body: blob,
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(body?.detail || `HTTP ${response.status}`);
      }
      pushToast("success", t("appshots.editor.applied"));
      onClose();
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setBusy("");
    }
  }, [appshotId, onClose, pushToast, render, t]);

  // -- keyboard ------------------------------------------------------------
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (typing) return; // the text box owns the keys while it is open
      const mod = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (event.key === "Escape") {
        event.preventDefault();
        if (draftRef.current) setDraft(null);
        else onClose();
      } else if (mod && key === "z" && !event.shiftKey) {
        event.preventDefault();
        undo();
      } else if (mod && (key === "y" || (key === "z" && event.shiftKey))) {
        event.preventDefault();
        redoOne();
      } else if (mod && key === "c") {
        event.preventDefault();
        void copy();
      } else if (mod && key === "s") {
        event.preventDefault();
        void save();
      } else if (!mod && !event.altKey) {
        const hit = TOOLS.find((entry) => entry.key === key);
        if (hit) setTool(hit.tool);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [copy, onClose, redoOne, save, setDraft, typing, undo]);

  const toolLabel = (name: Tool) => t(`appshots.editor.tool_${name}`);
  const textScreen = typing
    ? { left: (typing.at.x - shown.x) * scale, top: (typing.at.y - shown.y) * scale }
    : null;

  return (
    <div
      className="fixed inset-0 z-[80] flex flex-col bg-background/95 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={t("appshots.editor.title")}
      data-testid="appshot-editor"
    >
      <div className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-2.5">
        <p className="mr-2 text-base font-medium text-foreground">{t("appshots.editor.title")}</p>

        <div className="flex items-center gap-0.5 rounded-lg bg-secondary/60 p-0.5" role="toolbar">
          {TOOLS.map(({ tool: name, icon: Icon, key }) => (
            <QuickTooltip key={name} content={`${toolLabel(name)} (${key.toUpperCase()})`} side="bottom">
              <button
                type="button"
                aria-label={toolLabel(name)}
                aria-pressed={tool === name}
                onClick={() => setTool(name)}
                data-testid={`appshot-editor-tool-${name}`}
                className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors",
                  "hover:bg-popover hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
                  tool === name && "bg-popover text-foreground shadow-sm",
                )}
              >
                <Icon className="h-4 w-4" aria-hidden />
              </button>
            </QuickTooltip>
          ))}
        </div>

        <div className="flex items-center gap-1.5" aria-label={t("appshots.editor.color")}>
          {COLORS.map((swatch) => (
            <button
              key={swatch}
              type="button"
              aria-label={swatch}
              aria-pressed={color === swatch}
              onClick={() => setColor(swatch)}
              className={cn(
                "h-5 w-5 rounded-full border border-border transition-transform focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
                color === swatch && "scale-110 ring-2 ring-foreground/70 ring-offset-2 ring-offset-background",
              )}
              style={{ backgroundColor: swatch }}
            />
          ))}
        </div>

        <div className="flex items-center gap-0.5 rounded-lg bg-secondary/60 p-0.5" aria-label={t("appshots.editor.size")}>
          {[0, 1, 2].map((index) => (
            <button
              key={index}
              type="button"
              aria-label={`${t("appshots.editor.size")} ${index + 1}`}
              aria-pressed={widthIndex === index}
              onClick={() => setWidthIndex(index)}
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-popover",
                widthIndex === index && "bg-popover shadow-sm",
              )}
            >
              <span className="rounded-full bg-foreground" style={{ width: 4 + index * 4, height: 4 + index * 4 }} />
            </button>
          ))}
        </div>

        <div className="flex items-center gap-0.5">
          <QuickTooltip content={`${t("appshots.editor.undo")} (Ctrl+Z)`} side="bottom">
            <Button type="button" variant="ghost" size="sm" onClick={undo} disabled={ops.length === 0} aria-label={t("appshots.editor.undo")}>
              <Undo2 aria-hidden />
            </Button>
          </QuickTooltip>
          <QuickTooltip content={`${t("appshots.editor.redo")} (Ctrl+Y)`} side="bottom">
            <Button type="button" variant="ghost" size="sm" onClick={redoOne} disabled={redo.length === 0} aria-label={t("appshots.editor.redo")}>
              <Redo2 aria-hidden />
            </Button>
          </QuickTooltip>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={() => void copy()} disabled={!image || busy !== ""} data-testid="appshot-editor-copy">
            {busy === "copy" ? <Loader2 className="animate-spin" aria-hidden /> : <Copy aria-hidden />}
            {t("appshots.editor.copy")}
          </Button>
          <Button type="button" variant="secondary" size="sm" onClick={() => void save()} disabled={!image || busy !== ""} data-testid="appshot-editor-save">
            {busy === "save" ? <Loader2 className="animate-spin" aria-hidden /> : <Download aria-hidden />}
            {t("appshots.editor.save")}
          </Button>
          <Button type="button" size="sm" onClick={() => void apply()} disabled={!image || busy !== ""} data-testid="appshot-editor-apply">
            {busy === "apply" ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
            {t("appshots.editor.apply")}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label={t("appshots.editor.close")} data-testid="appshot-editor-close">
            <X aria-hidden />
          </Button>
        </div>
      </div>

      <div ref={stageRef} className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden">
        {failed ? (
          <p className="text-base text-muted-foreground">{t("appshots.editor.gone")}</p>
        ) : !image ? (
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
        ) : (
          <div className="relative shadow-2xl ring-1 ring-border" style={{ width: cw, height: ch }}>
            <canvas
              ref={canvasRef}
              style={{ width: cw, height: ch, cursor: tool === "text" ? "text" : "crosshair", touchAction: "none" }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              data-testid="appshot-editor-canvas"
            />
            {typing && textScreen && (
              <textarea
                autoFocus
                value={typing.text}
                placeholder={t("appshots.editor.text_placeholder")}
                onChange={(event) => setTyping({ ...typing, text: event.target.value })}
                onBlur={commitText}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    commitText();
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    setTyping(null);
                  }
                }}
                rows={1}
                className="absolute resize-none border border-dashed border-white/70 bg-black/20 p-0 font-semibold leading-tight outline-none placeholder:text-white/50"
                style={{
                  left: textScreen.left,
                  top: textScreen.top,
                  color,
                  fontSize: textSize(width) * scale,
                  minWidth: 120,
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
