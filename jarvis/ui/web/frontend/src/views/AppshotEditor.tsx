import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  Circle,
  Copy,
  Crop,
  Download,
  Focus,
  GripVertical,
  Grid3x3,
  Highlighter,
  Image as ImageIcon,
  Loader2,
  Minus,
  MousePointer2,
  MoveUpRight,
  Pencil,
  Redo2,
  Square,
  Trash2,
  Type,
  Undo2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useCapabilities } from "@/hooks/useCapabilities";
import { fill, useLocaleChunk, useT } from "@/i18n";
import { copyAppshotPng } from "@/lib/appshotClipboard";
import { latestAppshotImageUrl } from "@/lib/appshotApi";
import {
  BACKGROUND_PRESETS,
  COLORS,
  CROP_RATIOS,
  DEFAULT_BACKGROUND,
  STROKE_LEVELS,
  TOOL_KEYS,
  bounds,
  canvasMeasure,
  clampRect,
  commit,
  constrainEnd,
  counterSize,
  emptyHistory,
  exportPng,
  frameLayout,
  hitTest,
  isMeaningful,
  nextCounter,
  paintOps,
  presetById,
  presetCss,
  rectFrom,
  redo as redoHistory,
  strokeWidths,
  textSize,
  toolForKey,
  translate,
  undo as undoHistory,
  viewport,
  withId,
  type Background,
  type Draft,
  type History,
  type Op,
  type Point,
  type RedactMode,
  type TextStyle,
  type Tool,
} from "@/lib/appshotEditorModel";
import { saveOrDownload } from "@/lib/clipboard";
import { canNativeDrag, startNativeFileDrag } from "@/lib/nativeDrag";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

/**
 * The appshot editor — annotate a capture the way CleanShot X's annotate tool
 * does: select/move, arrow, line, rectangle, filled rectangle, ellipse, draw,
 * highlighter, text (three styles), counter, spotlight, pixelate/blur, crop
 * (free or fixed ratio) and a background frame, with undo/redo and CleanShot's
 * one-letter keys. The result can be copied, saved, or put back in place of
 * the appshot so the next message carries the edited picture.
 *
 * Copy goes through the OS clipboard on the desktop (`/api/appshot/clipboard`)
 * because the WebView cannot be trusted with images; Save writes into
 * Downloads through the backend because the desktop WebView drops browser
 * downloads. In a plain browser both fall back to the browser's own paths.
 *
 * The layout follows CleanShot X's editor window: a floating rounded window
 * over a dimmed app, the tools as pills along the top with colour and size
 * beside them, "Save" and "Done" at the top right, and a bottom bar with the
 * zoom, a "Drag me" handle (a real file drag into any app, where the desktop
 * shell has the native drag bridge) and the copy action.
 */

/** Toolbar order, as in CleanShot X: selection, shapes, text, effects, ink. */
const TOOL_ORDER: readonly Tool[] = [
  "move",
  "rect",
  "filled",
  "ellipse",
  "line",
  "arrow",
  "text",
  "redact",
  "spotlight",
  "counter",
  "pen",
  "highlight",
];

const KEY_OF: Record<Tool, string> = Object.fromEntries(TOOL_KEYS.map(({ tool, key }) => [tool, key])) as Record<Tool, string>;

/** Zoom steps after "fit". */
const ZOOMS = [0.5, 1, 2] as const;

const TOOL_ICONS: Record<Tool, typeof Square | null> = {
  move: MousePointer2,
  arrow: MoveUpRight,
  line: Minus,
  rect: Square,
  filled: null,
  ellipse: Circle,
  pen: Pencil,
  highlight: Highlighter,
  text: Type,
  counter: null,
  spotlight: Focus,
  redact: Grid3x3,
  crop: Crop,
  background: ImageIcon,
};


const COLOR_KEY = "jarvis.appshotEditor.color";
const BACKGROUND_KEY = "jarvis.appshotEditor.background";

function readStored<T>(key: string, fallback: T, valid: (value: unknown) => value is T): T {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    const value: unknown = JSON.parse(raw);
    return valid(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage can be off (private mode, a locked profile); the choice simply
    // is not remembered.
  }
}

const isColor = (value: unknown): value is string => typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
const isBackground = (value: unknown): value is Background =>
  typeof value === "object" && value !== null && "preset" in value && "padding" in value;

function stamp(): string {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-");
}

type Gesture =
  | { kind: "draw"; shape: Draft; start: Point }
  | { kind: "move"; id: number; start: Point; dx: number; dy: number };

type LoadState = "loading" | "ready" | "failed";

interface Typing {
  at: Point;
  text: string;
  /** The text annotation being changed, or null for a new one. */
  replaceId: number | null;
}

export interface AppshotEditorProps {
  appshotId: string;
  onClose: () => void;
  /** The edited picture replaced the held appshot (and went to the assistant). */
  onApplied?: () => void;
  /**
   * ``overlay``: a floating window over the app (the page editor).
   * ``window``: the editor IS its own desktop window, edge to edge, and its
   * top bar moves the window.
   */
  variant?: "overlay" | "window";
}

export function AppshotEditor({ appshotId, onClose, onApplied, variant = "overlay" }: AppshotEditorProps) {
  const t = useT();
  const ready = useLocaleChunk("appshot_editor");
  const caps = useCapabilities();
  const native = caps.data?.native_file_actions ?? false;
  const pushToast = useEventStore((s) => s.pushToast);
  const assistantName = useEventStore((s) => s.assistantName);

  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [load, setLoad] = useState<LoadState>("loading");
  const [history, setHistory] = useState<History>(emptyHistory);
  const ops = history.present;
  const [tool, setTool] = useState<Tool>("arrow");
  const [color, setColorState] = useState(() => readStored(COLOR_KEY, COLORS[0], isColor));
  const [widthIndex, setWidthIndex] = useState(2);
  const [zoom, setZoom] = useState<"fit" | number>("fit");
  const [menu, setMenu] = useState<"" | "style" | "zoom">("");
  const [dragging, setDragging] = useState(false);
  const holdingRef = useRef(false);
  const [textStyle, setTextStyle] = useState<TextStyle>("plain");
  const [redactMode, setRedactMode] = useState<RedactMode>("pixelate");
  const [cropRatio, setCropRatio] = useState<string>("free");
  const [background, setBackgroundState] = useState<Background>(() =>
    ({ ...readStored(BACKGROUND_KEY, DEFAULT_BACKGROUND, isBackground), enabled: false }),
  );
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // The text box: state for painting, a ref so a commit reads the latest
  // text without side effects inside a state updater.
  const [typing, setTypingState] = useState<Typing | null>(null);
  const typingRef = useRef<Typing | null>(null);
  const setTyping = useCallback((next: Typing | null) => {
    typingRef.current = next;
    setTypingState(next);
  }, []);
  const [busy, setBusy] = useState<"" | "copy" | "save" | "apply">("");
  const [dirty, setDirty] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  // Pointer events can outrun React's re-render (a quick flick sends its
  // moves and the release before the press has rendered), so the handlers
  // read and write the gesture through a ref; the state only drives painting.
  const gestureRef = useRef<Gesture | null>(null);
  const [gesture, setGestureState] = useState<Gesture | null>(null);
  const setGesture = useCallback((next: Gesture | null) => {
    gestureRef.current = next;
    setGestureState(next);
  }, []);

  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [stage, setStage] = useState({ w: 0, h: 0 });
  const measure = useMemo(() => {
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = document.createElement("canvas").getContext("2d");
    } catch {
      ctx = null;
    }
    return canvasMeasure(ctx);
  }, []);

  const setColor = (next: string) => {
    setColorState(next);
    writeStored(COLOR_KEY, next);
  };
  const setBackground = useCallback((patch: Partial<Background>) => {
    setBackgroundState((current) => ({ ...current, ...patch }));
  }, []);
  useEffect(() => writeStored(BACKGROUND_KEY, background), [background]);

  // -- load ----------------------------------------------------------------
  useEffect(() => {
    let alive = true;
    const img = new Image();
    img.onload = () => {
      if (!alive) return;
      setImage(img);
      setLoad("ready");
    };
    img.onerror = () => alive && setLoad("failed");
    img.src = latestAppshotImageUrl(appshotId);
    return () => {
      alive = false;
    };
  }, [appshotId]);

  useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measureStage = () => setStage({ w: el.clientWidth, h: el.clientHeight });
    measureStage();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measureStage);
      return () => window.removeEventListener("resize", measureStage);
    }
    const observer = new ResizeObserver(measureStage);
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
  const framing = tool === "crop" ? { ...background, enabled: false } : background;
  const layout = frameLayout(shown.w || 1, shown.h || 1, framing);
  const fitScale =
    shown.w > 0 && stage.w > 0
      ? Math.min((stage.w - 48) / layout.width, (stage.h - 48) / layout.height, 1)
      : 1;
  const scale = zoom === "fit" ? fitScale : zoom;
  const cw = Math.max(1, Math.round(shown.w * scale));
  const ch = Math.max(1, Math.round(shown.h * scale));
  const ratio = CROP_RATIOS.find((entry) => entry.id === cropRatio)?.ratio ?? null;

  // What is on screen: the document, a move in progress, and the shape being drawn.
  const drawn = useMemo<Draft[]>(() => {
    const live: Draft[] =
      gesture?.kind === "move"
        ? ops.map((op) => (op.id === gesture.id ? translate(op, gesture.dx, gesture.dy) : op))
        : [...ops];
    if (gesture?.kind === "draw" && gesture.shape.kind !== "crop") live.push(gesture.shape);
    return live;
  }, [gesture, ops]);

  const selected = selectedId === null ? null : ops.find((op) => op.id === selectedId) ?? null;

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
    paintOps(ctx, image, drawn, { width: iw, height: ih });
    if (tool === "crop") {
      const crop = gesture?.kind === "draw" && gesture.shape.kind === "crop" ? gesture.shape.rect : view;
      ctx.save();
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.beginPath();
      ctx.rect(0, 0, iw, ih);
      ctx.rect(crop.x, crop.y, crop.w, crop.h);
      ctx.fill("evenodd");
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5 / scale;
      ctx.strokeRect(crop.x, crop.y, crop.w, crop.h);
      ctx.restore();
    }
    const outlined =
      selected && gesture?.kind === "move" && gesture.id === selected.id
        ? translate(selected, gesture.dx, gesture.dy)
        : selected;
    if (outlined) {
      const box = bounds(outlined, measure);
      const pad = 6 / scale;
      ctx.save();
      ctx.lineWidth = 1.5 / scale;
      ctx.setLineDash([5 / scale, 4 / scale]);
      ctx.strokeStyle = "rgba(0,0,0,0.6)";
      ctx.strokeRect(box.x - pad, box.y - pad, box.w + pad * 2, box.h + pad * 2);
      ctx.lineDashOffset = 4.5 / scale;
      ctx.strokeStyle = "#ffffff";
      ctx.strokeRect(box.x - pad, box.y - pad, box.w + pad * 2, box.h + pad * 2);
      ctx.restore();
    }
  }, [image, drawn, gesture, tool, cw, ch, scale, shown.x, shown.y, shown.w, shown.h, iw, ih, view, selected, measure]);

  // -- editing -------------------------------------------------------------
  const apply = useCallback((next: Op[]) => {
    setHistory((current) => commit(current, next));
    setDirty(true);
  }, []);

  const push = useCallback((shape: Draft) => apply([...history.present, withId(shape)]), [apply, history.present]);

  const undo = useCallback(() => {
    setHistory(undoHistory);
    setSelectedId(null);
  }, []);
  const redo = useCallback(() => {
    setHistory(redoHistory);
    setSelectedId(null);
  }, []);

  const removeSelected = useCallback(() => {
    if (selectedId === null) return;
    apply(ops.filter((op) => op.id !== selectedId));
    setSelectedId(null);
  }, [apply, ops, selectedId]);

  const nudgeSelected = useCallback(
    (dx: number, dy: number) => {
      if (selectedId === null) return;
      apply(ops.map((op) => (op.id === selectedId ? translate(op, dx, dy) : op)));
    },
    [apply, ops, selectedId],
  );

  const chooseTool = useCallback(
    (next: Tool) => {
      setTool(next);
      if (next !== "move") setSelectedId(null);
      if (next === "background") setBackground({ enabled: true });
    },
    [setBackground],
  );

  const toImage = (event: { clientX: number; clientY: number }): Point => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return {
      x: shown.x + (event.clientX - rect.left) / scale,
      y: shown.y + (event.clientY - rect.top) / scale,
    };
  };

  const commitText = useCallback(() => {
    const current = typingRef.current;
    setTyping(null);
    if (!current) return;
    const text = current.text.replace(/\s+$/, "");
    if (current.replaceId !== null) {
      const id = current.replaceId;
      apply(
        text
          ? ops.map((op) => (op.id === id && op.kind === "text" ? { ...op, text } : op))
          : ops.filter((op) => op.id !== id),
      );
    } else if (text.trim()) {
      push({ kind: "text", at: current.at, text, color, size: textSize(width), style: textStyle });
    }
  }, [apply, color, ops, push, setTyping, textStyle, width]);

  const editTextAt = (p: Point): boolean => {
    const hit = hitTest(ops, p, 6 / scale, measure);
    if (!hit || hit.kind !== "text") return false;
    setTyping({ at: hit.at, text: hit.text, replaceId: hit.id });
    return true;
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!image || event.button !== 0) return;
    const p = toImage(event);
    if (tool === "background") return;
    if (tool === "text") {
      // Keep the press from moving focus away: it would blur (and so close)
      // the text box this click is about to open.
      event.preventDefault();
      if (typing) commitText();
      if (!editTextAt(p)) setTyping({ at: p, text: "", replaceId: null });
      return;
    }
    if (tool === "counter") {
      push({ kind: "counter", at: p, n: nextCounter(ops), color, size: counterSize(width) });
      return;
    }
    event.currentTarget.setPointerCapture?.(event.pointerId);
    if (tool === "move") {
      const hit = hitTest(ops, p, 6 / scale, measure);
      setSelectedId(hit?.id ?? null);
      if (hit) setGesture({ kind: "move", id: hit.id, start: p, dx: 0, dy: 0 });
      return;
    }
    const zero = { x: p.x, y: p.y, w: 0, h: 0 };
    let shape: Draft;
    switch (tool) {
      case "arrow":
      case "line":
        shape = { kind: tool, from: p, to: p, color, width };
        break;
      case "rect":
      case "filled":
      case "ellipse":
        shape = { kind: tool, rect: zero, color, width };
        break;
      case "pen":
      case "highlight":
        shape = { kind: tool, points: [p], color, width };
        break;
      case "spotlight":
        shape = { kind: "spotlight", rect: zero };
        break;
      case "redact":
        shape = { kind: "redact", rect: zero, mode: redactMode, block: Math.max(8, width * 3) };
        break;
      default:
        shape = { kind: "crop", rect: zero };
    }
    setGesture({ kind: "draw", shape, start: p });
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const current = gestureRef.current;
    if (!current) return;
    const p = toImage(event);
    if (current.kind === "move") {
      setGesture({ ...current, dx: p.x - current.start.x, dy: p.y - current.start.y });
      return;
    }
    const shape = current.shape;
    const end = constrainEnd(tool, current.start, p, event.shiftKey, ratio);
    let next: Draft = shape;
    if (shape.kind === "arrow" || shape.kind === "line") next = { ...shape, to: end };
    else if (shape.kind === "pen" || shape.kind === "highlight") next = { ...shape, points: [...shape.points, p] };
    else if ("rect" in shape) next = { ...shape, rect: rectFrom(current.start, end) } as Draft;
    setGesture({ ...current, shape: next });
  };

  const onPointerUp = () => {
    const current = gestureRef.current;
    if (!current) return;
    setGesture(null);
    if (current.kind === "move") {
      if (current.dx !== 0 || current.dy !== 0) {
        apply(ops.map((op) => (op.id === current.id ? translate(op, current.dx, current.dy) : op)));
      }
      return;
    }
    let shape = current.shape;
    if (shape.kind === "crop" || shape.kind === "redact" || shape.kind === "spotlight") {
      const rect = clampRect(shape.rect, iw, ih);
      if (!rect) return;
      shape = { ...shape, rect } as Draft;
    }
    if (!isMeaningful(shape)) return;
    push(shape);
    if (shape.kind === "crop") setTool("move");
  };

  const onDoubleClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!image || typing) return;
    editTextAt(toImage(event));
  };

  // -- results -------------------------------------------------------------
  const render = useCallback(async () => {
    if (!image) throw new Error(t("appshot_editor.gone"));
    return await exportPng(image, ops, background);
  }, [background, image, ops, t]);

  const copy = useCallback(async () => {
    setBusy("copy");
    try {
      const blob = await render();
      await copyAppshotPng(blob, { native, timeoutMessage: t("appshot_editor.copy_timeout") });
      setDirty(false);
      pushToast("success", t("appshot_editor.copied"));
    } catch (error) {
      pushToast("error", fill(t("appshot_editor.copy_failed"), { 0: (error as Error).message }));
    } finally {
      setBusy("");
    }
  }, [native, pushToast, render, t]);

  const save = useCallback(async () => {
    setBusy("save");
    try {
      const blob = await render();
      const filename = `appshot-${stamp()}.png`;
      const path = await saveOrDownload({ filename, blob, native });
      setDirty(false);
      if (path) pushToast("success", fill(t("appshot_editor.saved_to"), { 0: path }), { filePath: path, filename });
    } catch (error) {
      pushToast("error", fill(t("appshot_editor.save_failed"), { 0: (error as Error).message }));
    } finally {
      setBusy("");
    }
  }, [native, pushToast, render, t]);

  const use = useCallback(async () => {
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
      const body = (await response.json().catch(() => null)) as { appshot?: { delivered_to?: string } } | null;
      const where = body?.appshot?.delivered_to;
      // Done means "this is what I meant": say where the edited picture went.
      pushToast(
        "success",
        fill(
          t(where === "voice" ? "appshot_editor.applied_voice" : where === "none" ? "appshot_editor.applied_none" : "appshot_editor.applied_message"),
          { name: assistantName },
        ),
      );
      setDirty(false);
      onApplied?.();
      onClose();
    } catch (error) {
      pushToast("error", fill(t("appshot_editor.apply_failed"), { 0: (error as Error).message }));
    } finally {
      setBusy("");
    }
  }, [appshotId, assistantName, onApplied, onClose, pushToast, render, t]);

  const requestClose = useCallback(() => {
    if (dirty && ops.length > 0) setConfirmDiscard(true);
    else onClose();
  }, [dirty, onClose, ops.length]);

  // -- keyboard ------------------------------------------------------------
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (typing) return; // the text box owns the keys while it is open
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "SELECT" || target.isContentEditable)) return;
      const mod = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (event.key === "Escape") {
        event.preventDefault();
        if (menu) setMenu("");
        else if (confirmDiscard) setConfirmDiscard(false);
        else if (gestureRef.current) setGesture(null);
        else if (selectedId !== null) setSelectedId(null);
        else requestClose();
        return;
      }
      if (confirmDiscard) return;
      if (mod && key === "z" && !event.shiftKey) {
        event.preventDefault();
        undo();
      } else if (mod && (key === "y" || (key === "z" && event.shiftKey))) {
        event.preventDefault();
        redo();
      } else if (mod && key === "c") {
        event.preventDefault();
        void copy();
      } else if (mod && key === "s") {
        event.preventDefault();
        void save();
      } else if (mod && event.key === "Enter") {
        event.preventDefault();
        void use();
      } else if ((event.key === "Delete" || event.key === "Backspace") && selectedId !== null) {
        event.preventDefault();
        removeSelected();
      } else if (event.key.startsWith("Arrow") && selectedId !== null) {
        event.preventDefault();
        const step = (event.shiftKey ? 10 : 1) / Math.max(scale, 0.01);
        const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
        const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
        nudgeSelected(dx, dy);
      } else if (!mod && !event.altKey && /^[1-5]$/.test(event.key)) {
        setWidthIndex(Number(event.key) - 1);
      } else if (!mod && !event.altKey) {
        const next = toolForKey(event.key);
        if (next) chooseTool(next);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chooseTool, confirmDiscard, copy, menu, nudgeSelected, redo, removeSelected, requestClose, save, scale, selectedId, setGesture, typing, undo, use]);

  // -- drag out --------------------------------------------------------------
  // The handle writes the finished picture to a file only when pressed, then
  // hands it to the native drag bridge if the button is still held.
  const canDragOut = native && canNativeDrag();
  useEffect(() => {
    const release = () => {
      holdingRef.current = false;
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("blur", release);
    };
  }, []);

  const dragOut = async (event: React.PointerEvent) => {
    if (event.button !== 0 || !image || dragging) return;
    event.preventDefault();
    holdingRef.current = true;
    setDragging(true);
    try {
      const blob = await render();
      const response = await fetch("/api/appshot/drag-file", {
        method: "POST",
        headers: { "content-type": "image/png" },
        body: blob,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const { path } = (await response.json()) as { path: string };
      if (holdingRef.current) startNativeFileDrag(path);
      else pushToast("info", t("appshot_editor.drag_release"));
    } catch (error) {
      pushToast("error", fill(t("appshot_editor.drag_failed"), { 0: (error as Error).message }));
    } finally {
      setDragging(false);
    }
  };

  // Close an open menu on a press anywhere else.
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => {
      if (!(event.target as Element | null)?.closest?.("[data-editor-menu]")) setMenu("");
    };
    window.addEventListener("pointerdown", close, true);
    return () => window.removeEventListener("pointerdown", close, true);
  }, [menu]);

  const label = (key: string) => (ready ? t(`appshot_editor.${key}`) : "");
  const toolLabel = (name: Tool) => label(`tool_${name}`);
  const textScreen = typing
    ? { left: (typing.at.x - shown.x) * scale, top: (typing.at.y - shown.y) * scale }
    : null;
  const cursor =
    tool === "text" ? "text" : tool === "move" ? (gesture?.kind === "move" ? "grabbing" : "default") : tool === "background" ? "default" : "crosshair";
  const frameOn = framing.enabled;
  const preset = presetById(background.preset);
  const zoomLabel = zoom === "fit" ? label("zoom_fit") : `${Math.round(zoom * 100)}%`;

  const hint =
    tool === "move"
      ? label("hint_move")
      : tool === "counter"
        ? label("hint_counter")
        : tool === "spotlight"
          ? label("hint_spotlight")
          : "";
  const showCapsule = tool === "text" || tool === "redact" || tool === "crop" || tool === "background" || hint !== "";

  const toolButton = (name: Tool) => {
    const Icon = TOOL_ICONS[name];
    return (
      <QuickTooltip key={name} content={`${toolLabel(name)} (${KEY_OF[name].toUpperCase()})`} side="bottom">
        <button
          type="button"
          aria-label={toolLabel(name)}
          aria-pressed={tool === name}
          onClick={() => chooseTool(name)}
          data-testid={`appshot-editor-tool-${name}`}
          className={cn(
            "flex h-7 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors",
            "hover:bg-foreground/10 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
            (name === "crop" || name === "background") && "bg-foreground/10",
            tool === name && "bg-foreground/20 text-foreground",
          )}
        >
          {name === "filled" ? (
            <Square className="h-[15px] w-[15px]" fill="currentColor" aria-hidden />
          ) : name === "counter" ? (
            <span className="flex h-[15px] w-[15px] items-center justify-center rounded-full bg-current text-[9px] font-bold leading-none">
              <span className="text-popover">1</span>
            </span>
          ) : Icon ? (
            <Icon className="h-[15px] w-[15px]" aria-hidden />
          ) : null}
        </button>
      </QuickTooltip>
    );
  };

  return (
    <div
      className={cn(
        "flex h-full w-full items-center justify-center",
        variant === "overlay" && "bg-black/45 p-2 backdrop-blur-[2px] sm:p-5",
      )}
    >
      <div
        className={cn(
          "relative flex h-full w-full flex-col overflow-hidden bg-popover text-popover-foreground",
          variant === "overlay" && "max-w-[1800px] rounded-2xl border border-border shadow-2xl",
        )}
        data-testid="appshot-editor"
        data-variant={variant}
      >
        {/* Top bar: close · crop and background · tools · colour and size · Save, Done. */}
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
          <QuickTooltip content={`${label("close")} (Esc)`} side="bottom">
            <button
              type="button"
              onClick={requestClose}
              aria-label={label("close")}
              data-testid="appshot-editor-close"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground/10 text-muted-foreground transition-colors hover:bg-destructive hover:text-destructive-foreground"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          </QuickTooltip>
          <div className="flex shrink-0 items-center gap-1">
            {toolButton("crop")}
            {toolButton("background")}
          </div>
          <div className="h-5 w-px shrink-0 bg-border" aria-hidden />
          <div className="flex min-w-0 items-center gap-0.5 overflow-x-auto" role="toolbar" aria-label={label("tools")}>
            {TOOL_ORDER.map(toolButton)}
          </div>
          <div className="h-5 w-px shrink-0 bg-border" aria-hidden />

          {/* Colour (a menu) and size (a slider), like CleanShot's style controls. */}
          <div className="relative shrink-0" data-editor-menu>
            <QuickTooltip content={label("color")} side="bottom">
              <button
                type="button"
                aria-label={label("color")}
                aria-expanded={menu === "style"}
                onClick={() => setMenu(menu === "style" ? "" : "style")}
                data-testid="appshot-editor-style"
                className="flex h-7 items-center gap-1 rounded-full bg-foreground/10 pl-1.5 pr-1 transition-colors hover:bg-foreground/15"
              >
                <span className="h-4 w-4 rounded-full border border-border-strong" style={{ backgroundColor: color }} />
                <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              </button>
            </QuickTooltip>
            {menu === "style" && (
              <div
                className="absolute left-0 top-full z-10 mt-2 w-[208px] rounded-xl border border-border bg-popover p-2.5 shadow-2xl"
                role="group"
                aria-label={label("color")}
                data-testid="appshot-editor-style-menu"
              >
                <div className="grid grid-cols-5 gap-2">
                  {COLORS.map((swatch) => (
                    <button
                      key={swatch}
                      type="button"
                      aria-label={swatch}
                      aria-pressed={color === swatch}
                      onClick={() => {
                        setColor(swatch);
                        setMenu("");
                      }}
                      className={cn(
                        "h-7 w-7 rounded-full border border-border-strong transition-transform hover:scale-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
                        color === swatch && "ring-2 ring-foreground/70 ring-offset-2 ring-offset-popover",
                      )}
                      style={{ backgroundColor: swatch }}
                    />
                  ))}
                  <label
                    className={cn(
                      "relative h-7 w-7 cursor-pointer overflow-hidden rounded-full border border-border-strong",
                      !COLORS.includes(color) && "ring-2 ring-foreground/70 ring-offset-2 ring-offset-popover",
                    )}
                    style={{ background: "conic-gradient(#ff3b30, #ffcc00, #34c759, #0a84ff, #af52de, #ff3b30)" }}
                    title={label("custom_color")}
                  >
                    <input
                      type="color"
                      value={color}
                      onChange={(event) => setColor(event.target.value)}
                      aria-label={label("custom_color")}
                      className="absolute inset-0 cursor-pointer opacity-0"
                      data-testid="appshot-editor-custom-color"
                    />
                  </label>
                </div>
              </div>
            )}
          </div>
          <QuickTooltip content={`${label("size")} (1–5)`} side="bottom">
            <label className="flex shrink-0 items-center">
              <span className="sr-only">{label("size")}</span>
              <input
                type="range"
                min={0}
                max={STROKE_LEVELS.length - 1}
                step={1}
                value={widthIndex}
                onChange={(event) => setWidthIndex(Number(event.currentTarget.value))}
                aria-label={label("size")}
                data-testid="appshot-editor-size"
                className="h-1 w-24 cursor-pointer accent-primary"
              />
            </label>
          </QuickTooltip>

          {/* Free space: in its own window this is where the window is moved from. */}
          <div className={cn("h-full min-w-4 flex-1", variant === "window" && "pywebview-drag-region")} aria-hidden />
          <div className="flex shrink-0 items-center gap-1.5">
            <QuickTooltip content={`${label("save_hint")} (Ctrl+S)`} side="bottom">
              <button
                type="button"
                onClick={() => void save()}
                disabled={!image || busy !== ""}
                data-testid="appshot-editor-save"
                className="flex h-7 items-center gap-1.5 rounded-full bg-foreground/10 px-3 text-[13px] font-medium text-foreground transition-colors hover:bg-foreground/15 disabled:opacity-50"
              >
                {busy === "save" && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
                {label("save")}
              </button>
            </QuickTooltip>
            <QuickTooltip content={`${fill(label("done_hint"), { name: assistantName })} (Ctrl+Enter)`} side="bottom">
              <button
                type="button"
                onClick={() => void use()}
                disabled={!image || busy !== ""}
                data-testid="appshot-editor-apply"
                className="flex h-7 items-center gap-1.5 rounded-full bg-primary px-3.5 text-[13px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                {busy === "apply" && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
                {label("done")}
              </button>
            </QuickTooltip>
          </div>
        </div>

        {/* The picture. */}
        <div className="relative min-h-0 flex-1 bg-background/70">
          {/* Floats over the picture, so it never changes the space the picture fits into. */}
          {showCapsule && (
            <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex justify-center px-3 pt-3">
              <div
                className="pointer-events-auto flex max-w-full flex-wrap items-center justify-center gap-x-3 gap-y-1.5 rounded-2xl border border-border bg-popover/95 px-3 py-1.5 text-[13px] shadow-lg backdrop-blur"
                data-testid="appshot-editor-options"
              >
                {tool === "text" && (
                  <Segmented
                    label={label("text_style")}
                    value={textStyle}
                    onChange={(value) => setTextStyle(value as TextStyle)}
                    options={[
                      { value: "plain", label: label("text_plain") },
                      { value: "label", label: label("text_label") },
                      { value: "outline", label: label("text_outline") },
                    ]}
                    testId="appshot-editor-text-style"
                  />
                )}
                {tool === "redact" && (
                  <Segmented
                    label={label("redact_mode")}
                    value={redactMode}
                    onChange={(value) => setRedactMode(value as RedactMode)}
                    options={[
                      { value: "pixelate", label: label("redact_pixelate") },
                      { value: "blur", label: label("redact_blur") },
                    ]}
                    testId="appshot-editor-redact-mode"
                  />
                )}
                {tool === "crop" && (
                  <Segmented
                    label={label("crop_ratio")}
                    value={cropRatio}
                    onChange={setCropRatio}
                    options={CROP_RATIOS.map((entry) => ({ value: entry.id, label: entry.id === "free" ? label("crop_free") : entry.id }))}
                    testId="appshot-editor-crop-ratio"
                  />
                )}
                {tool === "background" && (
                  <>
                    <label className="flex items-center gap-2 text-muted-foreground">
                      <Switch
                        checked={background.enabled}
                        onCheckedChange={(enabled) => setBackground({ enabled })}
                        aria-label={label("background_enabled")}
                        data-testid="appshot-editor-background-enabled"
                      />
                      {label("background_enabled")}
                    </label>
                    <div className="flex items-center gap-1.5" role="group" aria-label={label("background_preset")}>
                      {BACKGROUND_PRESETS.map((entry) => (
                        <button
                          key={entry.id}
                          type="button"
                          aria-label={entry.id}
                          aria-pressed={background.preset === entry.id}
                          onClick={() => setBackground({ preset: entry.id, enabled: true })}
                          className={cn(
                            "h-6 w-6 rounded-md border border-border-strong focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
                            background.preset === entry.id && background.enabled && "ring-2 ring-foreground/70 ring-offset-2 ring-offset-popover",
                          )}
                          style={{ background: presetCss(entry) }}
                        />
                      ))}
                    </div>
                    <Slider label={label("background_padding")} min={0} max={0.2} step={0.01} value={background.padding} onChange={(padding) => setBackground({ padding, enabled: true })} />
                    <Slider label={label("background_radius")} min={0} max={40} step={1} value={background.radius} onChange={(radius) => setBackground({ radius, enabled: true })} />
                    <label className="flex items-center gap-2 text-muted-foreground">
                      <Switch checked={background.shadow} onCheckedChange={(shadow) => setBackground({ shadow })} aria-label={label("background_shadow")} />
                      {label("background_shadow")}
                    </label>
                  </>
                )}
                {hint && <p className="text-muted-foreground">{hint}</p>}
              </div>
            </div>
          )}

          <div ref={stageRef} className="h-full w-full overflow-auto" data-testid="appshot-editor-stage">
          <div className="flex min-h-full min-w-full items-center justify-center p-6" style={{ width: "max-content" }}>
            {load === "failed" ? (
              <div className="flex max-w-sm flex-col items-center gap-3 text-center" data-testid="appshot-editor-failed">
                <p className="text-base text-muted-foreground">{label("gone")}</p>
                <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                  {label("close")}
                </Button>
              </div>
            ) : !image ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {label("loading")}
              </div>
            ) : (
              <div
                className={cn(frameOn ? "" : "shadow-2xl ring-1 ring-border")}
                style={frameOn ? { padding: layout.image.x * scale, background: presetCss(preset), borderRadius: 6 } : undefined}
                data-testid="appshot-editor-frame"
              >
                <div
                  className="relative overflow-hidden"
                  style={{
                    width: cw,
                    height: ch,
                    borderRadius: frameOn ? layout.radius * scale : 0,
                    boxShadow: frameOn && background.shadow ? "0 12px 40px rgba(0,0,0,0.35)" : undefined,
                  }}
                >
                  <canvas
                    ref={canvasRef}
                    style={{ width: cw, height: ch, cursor, touchAction: "none", display: "block" }}
                    onPointerDown={onPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                    onPointerCancel={() => setGesture(null)}
                    onDoubleClick={onDoubleClick}
                    data-testid="appshot-editor-canvas"
                  />
                  {typing && textScreen && (
                    <textarea
                      autoFocus
                      value={typing.text}
                      placeholder={label("text_placeholder")}
                      onChange={(event) => setTyping({ ...typing, text: event.target.value })}
                      onBlur={commitText}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                          event.preventDefault();
                          commitText();
                        } else if (event.key === "Escape") {
                          event.preventDefault();
                          event.stopPropagation();
                          setTyping(null);
                        }
                      }}
                      rows={Math.max(1, typing.text.split("\n").length)}
                      data-testid="appshot-editor-text-input"
                      className="absolute resize-none border border-dashed border-white/70 bg-black/25 p-0 font-semibold leading-tight outline-none placeholder:text-white/60"
                      style={{
                        left: textScreen.left,
                        top: textScreen.top,
                        color,
                        fontSize: textSize(width) * scale,
                        minWidth: 140,
                      }}
                    />
                  )}
                </div>
              </div>
            )}
          </div>
          </div>
        </div>

        {/* Bottom bar: zoom and history · drag handle · selection, save and copy. */}
        <div className="grid h-12 shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-2 border-t border-border px-3">
          <div className="flex items-center gap-1">
            <div className="relative" data-editor-menu>
              <button
                type="button"
                aria-label={label("zoom")}
                aria-expanded={menu === "zoom"}
                onClick={() => setMenu(menu === "zoom" ? "" : "zoom")}
                data-testid="appshot-editor-zoom"
                className="flex h-7 items-center gap-1 rounded-full bg-foreground/10 px-2.5 text-[13px] font-medium tabular-nums transition-colors hover:bg-foreground/15"
              >
                {zoomLabel}
                <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              </button>
              {menu === "zoom" && (
                <div className="absolute bottom-full left-0 z-10 mb-2 min-w-[96px] rounded-xl border border-border bg-popover p-1 shadow-2xl" role="menu">
                  {(["fit", ...ZOOMS] as const).map((entry) => (
                    <button
                      key={String(entry)}
                      type="button"
                      role="menuitemradio"
                      aria-checked={zoom === entry}
                      onClick={() => {
                        setZoom(entry);
                        setMenu("");
                      }}
                      className={cn(
                        "flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] tabular-nums hover:bg-foreground/10",
                        zoom === entry && "bg-foreground/10 font-medium",
                      )}
                    >
                      {entry === "fit" ? label("zoom_fit") : `${Math.round(entry * 100)}%`}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <QuickTooltip content={`${label("undo")} (Ctrl+Z)`} side="top">
              <button type="button" onClick={undo} disabled={history.past.length === 0} aria-label={label("undo")} data-testid="appshot-editor-undo" className={ROUND_ICON}>
                <Undo2 className="h-[15px] w-[15px]" aria-hidden />
              </button>
            </QuickTooltip>
            <QuickTooltip content={`${label("redo")} (Ctrl+Shift+Z)`} side="top">
              <button type="button" onClick={redo} disabled={history.future.length === 0} aria-label={label("redo")} data-testid="appshot-editor-redo" className={ROUND_ICON}>
                <Redo2 className="h-[15px] w-[15px]" aria-hidden />
              </button>
            </QuickTooltip>
          </div>

          <div className="flex justify-center">
            {canDragOut && (
              <QuickTooltip content={label("drag_hint")} side="top">
                <button
                  type="button"
                  onPointerDown={(event) => void dragOut(event)}
                  onDragStart={(event) => event.preventDefault()}
                  disabled={!image}
                  data-testid="appshot-editor-drag"
                  className="flex h-7 cursor-grab select-none items-center gap-1.5 rounded-full bg-foreground/10 px-3 text-[13px] font-medium text-foreground transition-colors hover:bg-foreground/15 active:cursor-grabbing disabled:opacity-50"
                >
                  {dragging ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <GripVertical className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />}
                  {label("drag_me")}
                  <GripVertical className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                </button>
              </QuickTooltip>
            )}
          </div>

          <div className="flex items-center justify-end gap-1">
            {selected && (
              <QuickTooltip content={`${label("delete")} (Del)`} side="top">
                <button type="button" onClick={removeSelected} aria-label={label("delete")} data-testid="appshot-editor-delete" className={ROUND_ICON}>
                  <Trash2 className="h-[15px] w-[15px]" aria-hidden />
                </button>
              </QuickTooltip>
            )}
            <QuickTooltip content={`${label("save_hint")} (Ctrl+S)`} side="top">
              <button type="button" onClick={() => void save()} disabled={!image || busy !== ""} aria-label={label("save")} className={ROUND_ICON}>
                <Download className="h-[15px] w-[15px]" aria-hidden />
              </button>
            </QuickTooltip>
            <QuickTooltip content={`${label("copy")} (Ctrl+C)`} side="top">
              <button type="button" onClick={() => void copy()} disabled={!image || busy !== ""} aria-label={label("copy")} data-testid="appshot-editor-copy" className={ROUND_ICON}>
                {busy === "copy" ? <Loader2 className="h-[15px] w-[15px] animate-spin" aria-hidden /> : <Copy className="h-[15px] w-[15px]" aria-hidden />}
              </button>
            </QuickTooltip>
          </div>
        </div>

        {confirmDiscard && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-background/60 backdrop-blur-[2px]" data-testid="appshot-editor-discard">
            <div className="w-[min(360px,calc(100%-2rem))] rounded-2xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl" role="alertdialog" aria-label={label("discard_title")}>
              <p className="text-base font-semibold">{label("discard_title")}</p>
              <p className="mt-1.5 text-sm text-muted-foreground">{label("discard_body")}</p>
              <div className="mt-4 flex justify-end gap-2">
                <Button type="button" variant="secondary" size="sm" autoFocus onClick={() => setConfirmDiscard(false)}>
                  {label("discard_keep")}
                </Button>
                <Button type="button" variant="destructive" size="sm" onClick={onClose} data-testid="appshot-editor-discard-confirm">
                  {label("discard_confirm")}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const ROUND_ICON =
  "flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-40";

function Segmented({
  label,
  value,
  onChange,
  options,
  testId,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  testId: string;
}) {
  return (
    <div className="flex items-center gap-2" data-testid={testId}>
      <span className="text-muted-foreground">{label}</span>
      <div className="flex items-center gap-0.5 rounded-lg bg-foreground/10 p-0.5" role="group" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={value === option.value}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-7 rounded-md px-2.5 text-sm text-muted-foreground transition-colors hover:bg-popover hover:text-foreground",
              value === option.value && "bg-popover text-foreground shadow-sm",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Slider({
  label,
  min,
  max,
  step,
  value,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-muted-foreground">
      {label}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={label}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="h-1 w-24 cursor-pointer accent-primary"
      />
    </label>
  );
}
