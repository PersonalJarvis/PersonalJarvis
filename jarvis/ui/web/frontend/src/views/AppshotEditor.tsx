import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Columns2,
  Copy,
  Download,
  FolderOpen,
  GripVertical,
  ImagePlus,
  Loader2,
  PictureInPicture2,
  Redo2,
  RotateCcw,
  Rows2,
  Trash2,
  Undo2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useCapabilities } from "@/hooks/useCapabilities";
import { fill, useLocaleChunk, useT } from "@/i18n";
import { copyAppshotPng } from "@/lib/appshotClipboard";
import {
  appshotLibraryImageUrl,
  fetchAppshotLibrary,
  latestAppshotImageUrl,
  type AppshotLibraryItem,
} from "@/lib/appshotApi";
import {
  ARROW_STYLES,
  BACKGROUND_PRESETS,
  COLORS,
  CROP_RATIOS,
  DEFAULT_BACKGROUND,
  PLACEMENTS,
  STROKE_LEVELS,
  TOOL_KEYS,
  addImage,
  bounds,
  buildScene,
  canvasMeasure,
  clampInto,
  commit,
  constrainEnd,
  counterSize,
  coversAll,
  cropHandleAt,
  cropHandles,
  emptyHistory,
  exportPng,
  extent,
  fitRatio,
  frameLayout,
  moveCrop,
  resizeCrop,
  withCrop,
  type CropHandle,
  type Placement,
  type Rect,
  grabScope,
  hitTest,
  isMeaningful,
  nextCounter,
  paintOps,
  presetById,
  presetCss,
  rectFrom,
  redo as redoHistory,
  strokeWidths,
  taperedArrowOutline,
  textSize,
  toolForKey,
  handleAt,
  handleCursor,
  handles,
  reshape,
  type HandleId,
  translate,
  undo as undoHistory,
  viewport,
  withId,
  type ArrowStyle,
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
 * The appshot editor — annotate a capture with select/move, arrow, line,
 * rectangle, filled rectangle, ellipse, draw,
 * highlighter, text (three styles), counter, spotlight, pixelate/blur, crop
 * (free or fixed ratio, an adjustable frame with grips) and a background
 * frame, with undo/redo and one-letter keys. "Add picture" (I) places an
 * earlier appshot from the gallery, a file, a pasted or a dropped picture
 * beside, below or on top of this one, so several pictures are marked up and
 * shared as one. The result can be copied, saved, or put back in place of
 * the appshot so the next message carries the edited picture.
 *
 * Copy goes through the OS clipboard on the desktop (`/api/appshot/clipboard`)
 * because the WebView cannot be trusted with images; Save writes into
 * Downloads through the backend because the desktop WebView drops browser
 * downloads. In a plain browser both fall back to the browser's own paths.
 *
 * The editor uses a floating rounded window over a dimmed app: one tool tray
 * of rounded-square buttons along the top (the active tool in the accent
 * colour), colour and size beside it, "Save", "Done" and close at the top
 * right, and a bottom bar with the zoom, a "Drag me" handle (a real file drag
 * into any app, where the desktop shell has the native drag bridge) and the
 * copy action. Its tools, keys and looks match the area picker's in-place
 * toolbar (``jarvis/appshot/picker``); the mouse wheel steps the size there
 * and here, and Enter finishes in both.
 */

/** Toolbar order: selection, shapes, text, effects, ink. */
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



const COLOR_KEY = "jarvis.appshotEditor.color";
/** Radius of a selection grip, in screen pixels. */
const GRIP_RADIUS = 5;
const ARROW_KEY = "jarvis.appshotEditor.arrowStyle";
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

const isArrowStyle = (value: unknown): value is ArrowStyle =>
  typeof value === "string" && (ARROW_STYLES as readonly string[]).includes(value);
const isColor = (value: unknown): value is string => typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
const isBackground = (value: unknown): value is Background =>
  typeof value === "object" && value !== null && "preset" in value && "padding" in value;

function stamp(): string {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-");
}

type Gesture =
  | { kind: "draw"; shape: Draft; start: Point }
  | { kind: "move"; id: number; start: Point; dx: number; dy: number }
  /** A grip of the selected annotation being dragged; `original` as it was. */
  | { kind: "resize"; id: number; handle: HandleId; original: Op; at: Point }
  /** A grip of the crop frame being dragged. */
  | { kind: "crop-resize"; handle: CropHandle; original: Rect; at: Point }
  /** The whole crop frame being dragged. */
  | { kind: "crop-move"; original: Rect; start: Point; at: Point };

const PLACEMENT_KEY = "jarvis.appshotEditor.placement";
const isPlacement = (value: unknown): value is Placement =>
  typeof value === "string" && (PLACEMENTS as readonly string[]).includes(value);

/** A picture of the user's to add: the first image file of a paste or a drop. */
function imageFile(items: DataTransferItemList | FileList | null | undefined): File | null {
  if (!items) return null;
  for (const entry of Array.from(items as ArrayLike<DataTransferItem | File>)) {
    const file = entry instanceof File ? entry : entry.kind === "file" ? entry.getAsFile() : null;
    if (file && file.type.startsWith("image/")) return file;
  }
  return null;
}

function insideRect(p: Point, rect: Rect): boolean {
  return p.x >= rect.x && p.x <= rect.x + rect.w && p.y >= rect.y && p.y <= rect.y + rect.h;
}

function loadPicture(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("The picture could not be loaded."));
    img.src = src;
  });
}

type LoadState = "loading" | "ready" | "failed";

interface Typing {
  at: Point;
  text: string;
  /** The text annotation being changed, or null for a new one. */
  replaceId: number | null;
}

/**
 * How the editor ended when it ended with a result (Save or Done): where the
 * picture was on screen, ``[x, y, w, h]`` in global logical pixels, so it can
 * fly from there back into the corner card.
 */
export interface EditorExit {
  flyFrom?: [number, number, number, number];
}

export interface AppshotEditorProps {
  appshotId: string;
  /** Closed; ``exit`` is set when it closed with a saved or used picture. */
  onClose: (exit?: EditorExit) => void;
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
  const [arrowStyle, setArrowStyleState] = useState<ArrowStyle>(() =>
    readStored(ARROW_KEY, "tapered" as ArrowStyle, isArrowStyle),
  );
  const setArrowStyle = (next: ArrowStyle) => {
    setArrowStyleState(next);
    writeStored(ARROW_KEY, next);
  };
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
  // Pictures added to this one, by the ``src`` their layers name.
  const [sources, setSources] = useState<ReadonlyMap<string, HTMLImageElement>>(() => new Map());
  const blobUrls = useRef<string[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [placement, setPlacementState] = useState<Placement>(() => readStored(PLACEMENT_KEY, "beside" as Placement, isPlacement));
  const setPlacement = (next: Placement) => {
    setPlacementState(next);
    writeStored(PLACEMENT_KEY, next);
  };
  const [dropping, setDropping] = useState(false);
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
  // Everything there is: the first picture and every picture added to it.
  const area = extent(ops, iw, ih);
  // While cropping, show all of it so a crop can also grow back.
  const shown = tool === "crop" ? area : view;
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
  // The crop frame as it looks right now: drawn, reshaped or moved by the drag in progress.
  const cropRect: Rect =
    gesture?.kind === "draw" && gesture.shape.kind === "crop"
      ? gesture.shape.rect
      : gesture?.kind === "crop-resize"
        ? resizeCrop(gesture.original, gesture.handle, gesture.at, area, ratio)
        : gesture?.kind === "crop-move"
          ? moveCrop(gesture.original, gesture.at.x - gesture.start.x, gesture.at.y - gesture.start.y, area)
          : view;

  // An annotation as it looks right now: moved or reshaped by the drag in progress.
  const live = useCallback(
    (op: Op): Op => {
      if (gesture?.kind === "move" && gesture.id === op.id) return translate(op, gesture.dx, gesture.dy);
      if (gesture?.kind === "resize" && gesture.id === op.id) {
        return reshape(gesture.original, gesture.handle, gesture.at, measure);
      }
      return op;
    },
    [gesture, measure],
  );

  // What is on screen: the document, a move or reshape in progress, and the shape being drawn.
  const drawn = useMemo<Draft[]>(() => {
    const shapes: Draft[] = ops.map(live);
    if (gesture?.kind === "draw" && gesture.shape.kind !== "crop") shapes.push(gesture.shape);
    return shapes;
  }, [gesture, live, ops]);

  const selected = selectedId === null ? null : ops.find((op) => op.id === selectedId) ?? null;
  const selectedLive = selected ? live(selected) : null;
  const [hoverCursor, setHoverCursor] = useState<string | null>(null);

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
    paintOps(ctx, buildScene(image, iw, ih, drawn, sources), drawn);
    if (tool === "crop") {
      const crop = cropRect;
      ctx.save();
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.beginPath();
      ctx.rect(area.x, area.y, area.w, area.h);
      ctx.rect(crop.x, crop.y, crop.w, crop.h);
      ctx.fill("evenodd");
      // Thirds, to line the picture up.
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1 / scale;
      ctx.beginPath();
      for (const third of [1 / 3, 2 / 3]) {
        ctx.moveTo(crop.x + crop.w * third, crop.y);
        ctx.lineTo(crop.x + crop.w * third, crop.y + crop.h);
        ctx.moveTo(crop.x, crop.y + crop.h * third);
        ctx.lineTo(crop.x + crop.w, crop.y + crop.h * third);
      }
      ctx.stroke();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5 / scale;
      ctx.strokeRect(crop.x, crop.y, crop.w, crop.h);
      // Grips: an L at each corner, a bar on each edge of a free crop.
      ctx.lineWidth = 4 / scale;
      ctx.lineCap = "round";
      ctx.shadowColor = "rgba(0,0,0,0.5)";
      ctx.shadowBlur = 3 / scale;
      const arm = Math.min(18 / scale, crop.w / 3, crop.h / 3);
      ctx.beginPath();
      for (const grip of cropHandles(crop, ratio)) {
        const { x, y } = grip.at;
        if (grip.id === "n" || grip.id === "s") {
          ctx.moveTo(x - arm / 2, y);
          ctx.lineTo(x + arm / 2, y);
        } else if (grip.id === "e" || grip.id === "w") {
          ctx.moveTo(x, y - arm / 2);
          ctx.lineTo(x, y + arm / 2);
        } else {
          const sx = grip.id === "nw" || grip.id === "sw" ? 1 : -1;
          const sy = grip.id === "nw" || grip.id === "ne" ? 1 : -1;
          ctx.moveTo(x + sx * arm, y);
          ctx.lineTo(x, y);
          ctx.lineTo(x, y + sy * arm);
        }
      }
      ctx.stroke();
      ctx.restore();
    }
    if (selectedLive) {
      // The selection: a dashed frame (not for a line or an arrow, whose two
      // grips say it all) and a round grip wherever it can be reshaped.
      ctx.save();
      if (selectedLive.kind !== "arrow" && selectedLive.kind !== "line") {
        const box = bounds(selectedLive, measure);
        const pad = 6 / scale;
        ctx.lineWidth = 1.5 / scale;
        ctx.setLineDash([5 / scale, 4 / scale]);
        ctx.strokeStyle = "rgba(0,0,0,0.6)";
        ctx.strokeRect(box.x - pad, box.y - pad, box.w + pad * 2, box.h + pad * 2);
        ctx.lineDashOffset = 4.5 / scale;
        ctx.strokeStyle = "#ffffff";
        ctx.strokeRect(box.x - pad, box.y - pad, box.w + pad * 2, box.h + pad * 2);
        ctx.setLineDash([]);
      }
      for (const grip of handles(selectedLive, measure)) {
        ctx.beginPath();
        ctx.arc(grip.at.x, grip.at.y, GRIP_RADIUS / scale, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.shadowColor = "rgba(0,0,0,0.45)";
        ctx.shadowBlur = 4 / scale;
        ctx.fill();
        ctx.shadowColor = "transparent";
        ctx.lineWidth = 1.5 / scale;
        ctx.strokeStyle = "#0a84ff";
        ctx.stroke();
      }
      ctx.restore();
    }
  }, [image, drawn, tool, cw, ch, scale, shown.x, shown.y, shown.w, shown.h, iw, ih, area, cropRect, ratio, sources, selectedLive, measure]);

  // -- editing -------------------------------------------------------------
  const apply = useCallback((next: Op[]) => {
    setHistory((current) => commit(current, next));
    setDirty(true);
  }, []);

  /** Add a shape; its id, so a fresh annotation can be selected at once. */
  const push = useCallback(
    (shape: Draft) => {
      const op = withId(shape);
      apply([...history.present, op]);
      return op.id;
    },
    [apply, history.present],
  );

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

  // -- crop ------------------------------------------------------------------
  /** Crop to ``rect``; all of the document (or null) means no crop at all. */
  const setCrop = useCallback(
    (rect: Rect | null) => {
      const next = rect && !coversAll(rect, area) ? rect : null;
      if (!next && !ops.some((op) => op.kind === "crop")) return;
      apply(withCrop(ops, next));
    },
    [apply, area, ops],
  );

  /** A fixed ratio reshapes the crop at once: the largest such frame in what is visible. */
  const chooseRatio = (id: string) => {
    setCropRatio(id);
    const next = CROP_RATIOS.find((entry) => entry.id === id)?.ratio ?? null;
    if (next !== null) setCrop(fitRatio(view, next));
  };

  // -- more pictures -----------------------------------------------------------
  // Read when an added picture has finished loading, after other edits may have landed.
  const opsRef = useRef(ops);
  opsRef.current = ops;

  useEffect(() => {
    const urls = blobUrls.current;
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, []);

  const addPicture = useCallback(
    async (src: string) => {
      if (!image) return;
      setImporting(true);
      try {
        const picture = await loadPicture(src);
        setSources((current) => new Map(current).set(src, picture));
        const added = addImage(
          opsRef.current,
          iw,
          ih,
          { src, width: picture.naturalWidth, height: picture.naturalHeight },
          placement,
        );
        apply(added.ops);
        setImportOpen(false);
        // Selected with the select tool, so it can be moved and sized at once.
        setTool("move");
        setSelectedId(added.id);
      } catch {
        pushToast("error", t("appshot_editor.import_failed"));
      } finally {
        setImporting(false);
      }
    },
    [apply, ih, image, iw, placement, pushToast, t],
  );

  const addFile = useCallback(
    (file: File) => {
      const url = URL.createObjectURL(file);
      blobUrls.current.push(url);
      void addPicture(url);
    },
    [addPicture],
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
      setSelectedId(push({ kind: "text", at: current.at, text, color, size: textSize(width), style: textStyle }));
    }
  }, [apply, color, ops, push, setTyping, textStyle, width]);

  const editTextAt = (p: Point): boolean => {
    const hit = hitTest(ops, p, 6 / scale, measure);
    if (!hit || hit.kind !== "text") return false;
    setTyping({ at: hit.at, text: hit.text, replaceId: hit.id });
    return true;
  };

  /** The annotation a press at ``p`` would move with the current tool, if any. */
  const grabbable = (p: Point) => {
    const scope = grabScope(tool);
    if (scope === "none" || scope === "grips") return null;
    const hit = hitTest(ops, p, 6 / scale, measure, { areas: scope === "any" });
    if (!hit || (tool === "text" && hit.kind === "text")) return null;
    return hit;
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!image || event.button !== 0) return;
    const p = toImage(event);
    if (tool === "background") return;
    if (tool === "crop") {
      // A grip reshapes the crop frame, a press inside it moves it, a press
      // outside draws a new one.
      event.preventDefault();
      event.currentTarget.setPointerCapture?.(event.pointerId);
      const grip = cropHandleAt(view, p, (GRIP_RADIUS + 6) / scale, ratio);
      if (grip) {
        setGesture({ kind: "crop-resize", handle: grip, original: view, at: p });
      } else if (!coversAll(view, area) && insideRect(p, view)) {
        setGesture({ kind: "crop-move", original: view, start: p, at: p });
      } else {
        setGesture({ kind: "draw", shape: { kind: "crop", rect: { x: p.x, y: p.y, w: 0, h: 0 } }, start: p });
      }
      return;
    }
    // A grip of the selected annotation reshapes it; any drawn annotation
    // under the pointer is taken and moved (the select tool also takes
    // spotlights, redactions and added pictures); pen and highlighter only
    // take grips.
    const scope = grabScope(tool);
    if (scope !== "none" && selected && selectedLive) {
      const grip = handleAt(selectedLive, p, (GRIP_RADIUS + 4) / scale, measure);
      if (grip) {
        event.preventDefault();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        setGesture({ kind: "resize", id: selected.id, handle: grip, original: selected, at: p });
        return;
      }
    }
    // Spotlights, redactions and added pictures cover what lies in them, so
    // only the select tool picks them; with a drawing tool they stay drawable on.
    const hit = grabbable(p);
    if (hit) {
      event.preventDefault();
      if (typing) commitText();
      event.currentTarget.setPointerCapture?.(event.pointerId);
      setSelectedId(hit.id);
      setGesture({ kind: "move", id: hit.id, start: p, dx: 0, dy: 0 });
      return;
    }
    setSelectedId(null);
    if (tool === "text") {
      // Keep the press from moving focus away: it would blur (and so close)
      // the text box this click is about to open.
      event.preventDefault();
      if (typing) commitText();
      if (!editTextAt(p)) setTyping({ at: p, text: "", replaceId: null });
      return;
    }
    if (tool === "counter") {
      // Selected like every fresh annotation; a click beside it still places
      // the next number, a press on it moves it.
      setSelectedId(push({ kind: "counter", at: p, n: nextCounter(ops), color, size: counterSize(width) }));
      return;
    }
    event.currentTarget.setPointerCapture?.(event.pointerId);
    if (tool === "move") return;
    const zero = { x: p.x, y: p.y, w: 0, h: 0 };
    let shape: Draft;
    switch (tool) {
      case "arrow":
        shape = { kind: "arrow", from: p, to: p, color, width, style: arrowStyle };
        break;
      case "line":
        shape = { kind: "line", from: p, to: p, color, width };
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
      default:
        shape = { kind: "redact", rect: zero, mode: redactMode, block: Math.max(8, width * 3) };
    }
    setGesture({ kind: "draw", shape, start: p });
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const current = gestureRef.current;
    const p = toImage(event);
    if (!current) {
      // Say with the pointer what a press here would do: reshape, move, or draw.
      if (!image || tool === "background") return setHoverCursor(null);
      if (tool === "crop") {
        const cropGrip = cropHandleAt(view, p, (GRIP_RADIUS + 6) / scale, ratio);
        if (cropGrip) return setHoverCursor(handleCursor(cropGrip));
        return setHoverCursor(!coversAll(view, area) && insideRect(p, view) ? "move" : null);
      }
      const grip =
        selectedLive && grabScope(tool) !== "none"
          ? handleAt(selectedLive, p, (GRIP_RADIUS + 4) / scale, measure)
          : null;
      if (grip) return setHoverCursor(handleCursor(grip));
      setHoverCursor(grabbable(p) ? "move" : null);
      return;
    }
    if (current.kind === "resize" || current.kind === "crop-resize" || current.kind === "crop-move") {
      setGesture({ ...current, at: p });
      return;
    }
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
    if (current.kind === "resize") {
      const reshaped = reshape(current.original, current.handle, current.at, measure);
      if ("rect" in reshaped && (reshaped.rect.w < 2 || reshaped.rect.h < 2)) return;
      apply(ops.map((op) => (op.id === current.id ? reshaped : op)));
      return;
    }
    if (current.kind === "move") {
      if (current.dx !== 0 || current.dy !== 0) {
        apply(ops.map((op) => (op.id === current.id ? translate(op, current.dx, current.dy) : op)));
      }
      return;
    }
    if (current.kind === "crop-resize") {
      setCrop(resizeCrop(current.original, current.handle, current.at, area, ratio));
      return;
    }
    if (current.kind === "crop-move") {
      const dx = current.at.x - current.start.x;
      const dy = current.at.y - current.start.y;
      if (dx !== 0 || dy !== 0) setCrop(moveCrop(current.original, dx, dy, area));
      return;
    }
    let shape = current.shape;
    if (shape.kind === "crop" || shape.kind === "redact" || shape.kind === "spotlight") {
      const rect = clampInto(shape.rect, area);
      if (!rect) return;
      shape = { ...shape, rect } as Draft;
    }
    if (!isMeaningful(shape)) return;
    // A crop stays in crop mode with its grips, to be fine-tuned; Enter or
    // another tool finishes it.
    if (shape.kind === "crop") return setCrop(shape.rect);
    // Every fresh annotation is selected at once, so its grips are right
    // there (the previous one loses them) and it can be moved again.
    setSelectedId(push(shape));
  };

  const onDoubleClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    // Only the select and text tools open a text for editing; a quick
    // double stroke with the pen stays ink.
    if (!image || typing || (tool !== "move" && tool !== "text")) return;
    editTextAt(toImage(event));
  };

  // -- results -------------------------------------------------------------
  const render = useCallback(async () => {
    if (!image) throw new Error(t("appshot_editor.gone"));
    return await exportPng(image, ops, background, sources);
  }, [background, image, ops, sources, t]);

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

  /** Where the picture is on screen right now, for the flight back to the corner. */
  const flyFrom = useCallback((): EditorExit["flyFrom"] => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const r = canvas.getBoundingClientRect();
    // A framed window adds its title bar and borders above/around the page.
    const chromeX = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
    const chromeY = Math.max(0, window.outerHeight - window.innerHeight - chromeX);
    return [window.screenX + chromeX + r.left, window.screenY + chromeY + r.top, r.width, r.height];
  }, []);

  const save = useCallback(async () => {
    setBusy("save");
    let saved = false;
    try {
      const blob = await render();
      const filename = `appshot-${stamp()}.png`;
      const path = await saveOrDownload({ filename, blob, native });
      setDirty(false);
      saved = true;
      if (path) pushToast("success", fill(t("appshot_editor.saved_to"), { 0: path }), { filePath: path, filename });
      // The saved state is the appshot from now on: the corner card, a drag
      // and the next message all carry the edit. A gone appshot only skips this.
      await fetch(`/api/appshot/latest/image?id=${encodeURIComponent(appshotId)}`, {
        method: "PUT",
        headers: { "content-type": "image/png" },
        body: blob,
      }).catch(() => undefined);
    } catch (error) {
      pushToast("error", fill(t("appshot_editor.save_failed"), { 0: (error as Error).message }));
    } finally {
      setBusy("");
    }
    // Saved: back into the corner, flying from where the picture is now.
    if (saved) onClose({ flyFrom: flyFrom() });
  }, [appshotId, flyFrom, native, onClose, pushToast, render, t]);

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
      onClose({ flyFrom: flyFrom() });
    } catch (error) {
      pushToast("error", fill(t("appshot_editor.apply_failed"), { 0: (error as Error).message }));
    } finally {
      setBusy("");
    }
  }, [appshotId, assistantName, flyFrom, onApplied, onClose, pushToast, render, t]);

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
        else if (importOpen) setImportOpen(false);
        else if (gestureRef.current) setGesture(null);
        else if (selectedId !== null) setSelectedId(null);
        else if (tool === "crop") chooseTool("move");
        else requestClose();
        return;
      }
      if (confirmDiscard) return;
      if (event.key === "Enter" && !event.shiftKey && !mod && tool === "crop") {
        // Enter finishes the crop first; the next Enter is Done.
        event.preventDefault();
        chooseTool("move");
        return;
      }
      if (!mod && !event.altKey && key === "i") {
        event.preventDefault();
        setImportOpen((open) => !open);
        return;
      }
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
      } else if (event.key === "Enter" && !event.shiftKey) {
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
  }, [chooseTool, confirmDiscard, copy, importOpen, menu, nudgeSelected, redo, removeSelected, requestClose, save, scale, selectedId, setGesture, tool, typing, undo, use]);

  // A picture pasted while the editor is open joins this one.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      if (typingRef.current || !image) return;
      const file = imageFile(event.clipboardData?.items);
      if (!file) return;
      event.preventDefault();
      addFile(file);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [addFile, image]);

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
  const toolCursor =
    tool === "text" ? "text" : tool === "move" || tool === "background" ? "default" : "crosshair";
  const cursor =
    gesture?.kind === "move"
      ? "grabbing"
      : gesture?.kind === "resize"
        ? handleCursor(gesture.handle)
        : hoverCursor ?? toolCursor;
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
          : tool === "crop"
            ? label("hint_crop")
            : "";
  const showCapsule = tool === "text" || tool === "redact" || tool === "crop" || tool === "background" || hint !== "";

  const toolButton = (name: Tool) => {
    return (
      <QuickTooltip key={name} content={`${toolLabel(name)} (${KEY_OF[name].toUpperCase()})`} side="bottom">
        <button
          type="button"
          aria-label={toolLabel(name)}
          aria-pressed={tool === name}
          onClick={() => chooseTool(name)}
          data-testid={`appshot-editor-tool-${name}`}
          className={cn(
            "flex h-7 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors",
            "hover:bg-foreground/10 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
            tool === name && "bg-accent text-accent-foreground hover:bg-accent hover:text-accent-foreground",
          )}
        >
          <ToolGlyph name={name} />
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
        {/* Top bar: one tool tray (tools · crop and background) · colour and size · Save, Done, close. */}
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
          <div
            className="flex min-w-0 items-center gap-0.5 overflow-x-auto rounded-xl border border-border bg-foreground/[0.04] p-0.5"
            role="toolbar"
            aria-label={label("tools")}
          >
            {TOOL_ORDER.map(toolButton)}
            <div className="mx-0.5 h-5 w-px shrink-0 bg-border" aria-hidden />
            {toolButton("crop")}
            {toolButton("background")}
            <div className="mx-0.5 h-5 w-px shrink-0 bg-border" aria-hidden />
            <QuickTooltip content={`${label("import")} (I)`} side="bottom">
              <button
                type="button"
                aria-label={label("import")}
                aria-pressed={importOpen}
                onClick={() => setImportOpen((open) => !open)}
                disabled={!image}
                data-testid="appshot-editor-import"
                className={cn(
                  "flex h-7 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors",
                  "hover:bg-foreground/10 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-40",
                  importOpen && "bg-accent text-accent-foreground hover:bg-accent hover:text-accent-foreground",
                )}
              >
                {importing ? <Loader2 className="h-[17px] w-[17px] animate-spin" aria-hidden /> : <ImagePlus className="h-[17px] w-[17px]" aria-hidden />}
              </button>
            </QuickTooltip>
          </div>

          {/* Colour (a menu) and size (a slider). */}
          <div className="relative shrink-0" data-editor-menu>
            <QuickTooltip content={label("color")} side="bottom">
              <button
                type="button"
                aria-label={label("color")}
                aria-expanded={menu === "style"}
                onClick={() => setMenu(menu === "style" ? "" : "style")}
                data-testid="appshot-editor-style"
                className="flex h-7 items-center gap-1 rounded-lg border border-border bg-foreground/[0.04] pl-1.5 pr-1 transition-colors hover:bg-foreground/10"
              >
                <span className="h-4 w-4 rounded-md border border-border-strong" style={{ backgroundColor: color }} />
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
          {tool === "arrow" && (
            <div className="flex shrink-0 items-center gap-0.5" role="group" aria-label={label("arrow_style")} data-testid="appshot-editor-arrow-style">
              {ARROW_STYLES.map((style) => (
                <QuickTooltip key={style} content={label(`arrow_${style}`)} side="bottom">
                  <button
                    type="button"
                    aria-label={label(`arrow_${style}`)}
                    aria-pressed={arrowStyle === style}
                    onClick={() => setArrowStyle(style)}
                    className={cn(
                      "flex h-7 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground",
                      arrowStyle === style && "bg-accent text-accent-foreground hover:bg-accent hover:text-accent-foreground",
                    )}
                  >
                    <ArrowStyleIcon style={style} />
                  </button>
                </QuickTooltip>
              ))}
            </div>
          )}

          {/* Free space: in its own window this is where the window is moved from. */}
          <div className={cn("h-full min-w-4 flex-1", variant === "window" && "pywebview-drag-region")} aria-hidden />
          <div className="flex shrink-0 items-center gap-1.5">
            <QuickTooltip content={`${label("save_hint")} (Ctrl+S)`} side="bottom">
              <button
                type="button"
                onClick={() => void save()}
                disabled={!image || busy !== ""}
                data-testid="appshot-editor-save"
                className="flex h-7 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium text-foreground transition-colors hover:bg-foreground/10 disabled:opacity-50"
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
                className="flex h-7 items-center gap-1.5 rounded-lg bg-accent px-3.5 text-[13px] font-medium text-accent-foreground transition-colors hover:bg-accent/90 disabled:opacity-50"
              >
                {busy === "apply" && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
                {label("done")}
              </button>
            </QuickTooltip>
            <div className="mx-0.5 h-5 w-px shrink-0 bg-border" aria-hidden />
            <QuickTooltip content={`${label("close")} (Esc)`} side="bottom">
              <button
                type="button"
                onClick={requestClose}
                aria-label={label("close")}
                data-testid="appshot-editor-close"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive hover:text-destructive-foreground"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </QuickTooltip>
          </div>
        </div>

        {/* The picture; an image file dropped here joins it. */}
        <div
          className="relative min-h-0 flex-1 bg-background/70"
          onDragOver={(event) => {
            if (!image || !Array.from(event.dataTransfer.types).includes("Files")) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
            setDropping(true);
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropping(false);
          }}
          onDrop={(event) => {
            setDropping(false);
            const file = imageFile(event.dataTransfer.files);
            if (!file || !image) return;
            event.preventDefault();
            addFile(file);
          }}
        >
          {dropping && (
            <div className="pointer-events-none absolute inset-3 z-30 flex items-center justify-center rounded-2xl border-2 border-dashed border-accent bg-accent/10 text-sm font-medium text-foreground" data-testid="appshot-editor-drop">
              {fill(label("import_drop"), { 0: label(`place_${placement}`) })}
            </div>
          )}
          {importOpen && image && (
            <ImportPanel
              label={label}
              placement={placement}
              onPlacement={setPlacement}
              busy={importing}
              onPick={(item) => void addPicture(appshotLibraryImageUrl(item))}
              onFile={addFile}
              onClose={() => setImportOpen(false)}
            />
          )}
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
                  <>
                    <Segmented
                      label={label("crop_ratio")}
                      value={cropRatio}
                      onChange={chooseRatio}
                      options={CROP_RATIOS.map((entry) => ({ value: entry.id, label: entry.id === "free" ? label("crop_free") : entry.id }))}
                      testId="appshot-editor-crop-ratio"
                    />
                    <span className="tabular-nums text-muted-foreground" data-testid="appshot-editor-crop-size">
                      {Math.round(cropRect.w)} × {Math.round(cropRect.h)}
                    </span>
                    <button
                      type="button"
                      onClick={() => setCrop(null)}
                      disabled={coversAll(view, area)}
                      data-testid="appshot-editor-crop-reset"
                      className="flex h-7 items-center gap-1.5 rounded-lg px-2 text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                    >
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                      {label("crop_reset")}
                    </button>
                    <QuickTooltip content={`${label("crop_apply")} (Enter)`} side="bottom">
                      <button
                        type="button"
                        onClick={() => chooseTool("move")}
                        data-testid="appshot-editor-crop-apply"
                        className="flex h-7 items-center gap-1.5 rounded-lg bg-accent px-2.5 font-medium text-accent-foreground transition-colors hover:bg-accent/90"
                      >
                        <Check className="h-3.5 w-3.5" aria-hidden />
                        {label("crop_apply")}
                      </button>
                    </QuickTooltip>
                  </>
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
                <Button type="button" variant="secondary" size="sm" onClick={() => onClose()}>
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
                    onPointerLeave={() => setHoverCursor(null)}
                    onDoubleClick={onDoubleClick}
                    onWheel={(event) => {
                      // At "fit" nothing scrolls, so the wheel steps the size.
                      if (zoom !== "fit" || event.ctrlKey || event.deltaY === 0) return;
                      setWidthIndex((index) =>
                        Math.max(0, Math.min(STROKE_LEVELS.length - 1, index + (event.deltaY < 0 ? 1 : -1))),
                      );
                    }}
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
                className="flex h-7 items-center gap-1 rounded-lg border border-border px-2.5 text-[13px] font-medium tabular-nums transition-colors hover:bg-foreground/10"
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
                  className="flex h-7 cursor-grab select-none items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium text-foreground transition-colors hover:bg-foreground/10 active:cursor-grabbing disabled:opacity-50"
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
                <Button type="button" variant="destructive" size="sm" onClick={() => onClose()} data-testid="appshot-editor-discard-confirm">
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

/** The arrow glyph: the editor's own tapered arrow, drawn small. */
const ARROW_GLYPH = taperedArrowOutline({ x: 3.5, y: 14.5 }, { x: 15, y: 3 }, 2.2)
  .map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`)
  .join(" ");

/**
 * The tool glyphs — the same drawings as the area picker's toolbar
 * (``jarvis/appshot/picker/annotate.py``), so both editors read alike.
 */
function ToolGlyph({ name }: { name: Tool }) {
  const line = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  let body: React.ReactNode;
  switch (name) {
    case "move":
      body = <path d="M4 3 L4 15 L7.5 11.5 L10 16.5 L12 15.5 L9.6 10.6 L14.5 10.6 Z" {...line} />;
      break;
    case "rect":
      body = <rect x={3} y={4.5} width={12} height={9} rx={2} {...line} />;
      break;
    case "filled":
      body = <rect x={3} y={4.5} width={12} height={9} rx={2} {...line} fill="currentColor" />;
      break;
    case "ellipse":
      body = <ellipse cx={9} cy={9} rx={6.5} ry={5} {...line} />;
      break;
    case "line":
      body = <path d="M4 14 L14 4" {...line} />;
      break;
    case "arrow":
      body = <polygon points={ARROW_GLYPH} fill="currentColor" />;
      break;
    case "text":
      body = (
        <>
          <rect x={1.5} y={1.5} width={15} height={15} rx={3} {...line} strokeWidth={1.1} strokeDasharray="2.2 1.8" opacity={0.6} />
          <path d="M5.5 5.5 L12.5 5.5 M9 5.5 L9 13" {...line} strokeWidth={2.3} />
        </>
      );
      break;
    case "redact":
      body = (
        <>
          {[0, 1, 2].flatMap((i) =>
            [0, 1, 2].map((j) => (
              <rect key={`${i}${j}`} x={3 + i * 4.2} y={3 + j * 4.2} width={3.6} height={3.6} fill="currentColor" opacity={(i + j) % 2 === 0 ? 1 : 0.43} />
            )),
          )}
        </>
      );
      break;
    case "spotlight":
      body = (
        <>
          <circle cx={9} cy={9} r={4.5} {...line} />
          <path d="M9 1.5 L9 3 M9 15 L9 16.5 M1.5 9 L3 9 M15 9 L16.5 9" {...line} />
        </>
      );
      break;
    case "counter":
      body = (
        <>
          <circle cx={9} cy={9} r={6.5} {...line} />
          <text x={9} y={12.2} textAnchor="middle" fontSize={9} fontWeight={700} fill="currentColor">
            1
          </text>
        </>
      );
      break;
    case "pen":
      body = <path d="M3 15 L4 11.5 L12 3.5 L14.5 6 L6.5 14 Z" {...line} />;
      break;
    case "highlight":
      body = (
        <>
          <rect x={5} y={3} width={8} height={8} rx={1.5} {...line} />
          <path d="M7 11 L7 13.5 M11 11 L11 13.5 M3 15.5 L15 15.5" {...line} />
        </>
      );
      break;
    case "crop":
      body = <path d="M5 1.5 L5 13 L16.5 13 M1.5 5 L13 5 L13 16.5" {...line} />;
      break;
    case "background":
      body = (
        <>
          <rect x={2} y={3} width={14} height={12} rx={2.5} {...line} />
          <circle cx={6.5} cy={7} r={1.4} {...line} />
          <path d="M2.5 13.5 L7 9.5 L10 12 L12.5 10 L15.5 13" {...line} />
        </>
      );
      break;
  }
  return (
    <svg viewBox="0 0 18 18" className="h-[17px] w-[17px]" aria-hidden>
      {body}
    </svg>
  );
}

/** The three arrow looks, drawn small. */
function ArrowStyleIcon({ style }: { style: ArrowStyle }) {
  return (
    <svg viewBox="0 0 20 20" className="h-[15px] w-[15px]" aria-hidden>
      {style === "tapered" ? (
        <polygon points={ARROW_GLYPH} transform="translate(1 1)" fill="currentColor" />
      ) : (
        <>
          <path d="M4 16 L15 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          <path d="M16.5 3.5 L10.8 5.2 L14.8 9.2 Z" fill="currentColor" />
          {style === "double" && <path d="M2.5 17.5 L4.2 11.8 L8.2 15.8 Z" fill="currentColor" />}
        </>
      )}
    </svg>
  );
}

const PLACEMENT_ICON: Record<Placement, typeof Columns2> = {
  beside: Columns2,
  below: Rows2,
  over: PictureInPicture2,
};

/**
 * "Add picture": where the next picture goes (beside, below, on top), the
 * user's kept appshots to pick from, and a file from the disk.
 */
function ImportPanel({
  label,
  placement,
  onPlacement,
  busy,
  onPick,
  onFile,
  onClose,
}: {
  label: (key: string) => string;
  placement: Placement;
  onPlacement: (next: Placement) => void;
  busy: boolean;
  onPick: (item: AppshotLibraryItem) => void;
  onFile: (file: File) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<AppshotLibraryItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let alive = true;
    fetchAppshotLibrary()
      .then((body) => alive && setItems(
        Array.isArray(body.items) ? body.items.filter((item) => item.mime.startsWith("image/")) : [],
      ))
      .catch(() => {
        // The gallery is optional here: a file still works without it.
        if (alive) {
          setFailed(true);
          setItems([]);
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div
      className="absolute bottom-3 right-3 top-3 z-20 flex w-[min(340px,calc(100%-1.5rem))] flex-col overflow-hidden rounded-2xl border border-border bg-popover/95 text-[13px] shadow-2xl backdrop-blur"
      role="dialog"
      aria-label={label("import")}
      data-testid="appshot-editor-import-panel"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <p className="font-semibold">{label("import")}</p>
        <button type="button" onClick={onClose} aria-label={label("close")} className={ROUND_ICON}>
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <div className="flex flex-col gap-1.5 border-b border-border px-3 py-2.5">
        <span className="text-muted-foreground">{label("import_place")}</span>
        <div className="grid grid-cols-3 gap-1 rounded-lg bg-foreground/10 p-0.5" role="group" aria-label={label("import_place")}>
          {PLACEMENTS.map((entry) => {
            const Icon = PLACEMENT_ICON[entry];
            return (
              <button
                key={entry}
                type="button"
                aria-pressed={placement === entry}
                onClick={() => onPlacement(entry)}
                data-testid={`appshot-editor-place-${entry}`}
                className={cn(
                  "flex h-8 items-center justify-center gap-1.5 rounded-md text-muted-foreground transition-colors hover:bg-popover hover:text-foreground",
                  placement === entry && "bg-popover text-foreground shadow-sm",
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {label(`place_${entry}`)}
              </button>
            );
          })}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2.5">
        <p className="mb-2 text-muted-foreground">{label("import_library")}</p>
        {items === null ? (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {label("loading")}
          </div>
        ) : items.length === 0 ? (
          <p className="text-muted-foreground" data-testid="appshot-editor-import-empty">
            {label(failed ? "import_library_failed" : "import_library_empty")}
          </p>
        ) : (
          <ul className="grid grid-cols-2 gap-2" data-testid="appshot-editor-import-list">
            {items.map((item) => (
              <li key={`${item.id}:${item.variant}`}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onPick(item)}
                  title={item.app_name || item.label}
                  data-testid="appshot-editor-import-item"
                  className="group relative block w-full overflow-hidden rounded-lg border border-border bg-background transition-colors hover:border-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-50"
                >
                  <img
                    src={appshotLibraryImageUrl(item, true)}
                    alt={item.app_name || item.label}
                    loading="lazy"
                    draggable={false}
                    className="aspect-[4/3] w-full object-cover"
                  />
                  {item.variant === "edited" && (
                    <span className="absolute left-1 top-1 rounded bg-accent px-1.5 py-px text-[11px] font-medium text-accent-foreground">
                      {label("library_edited_badge")}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="border-t border-border px-3 py-2.5">
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="hidden"
          data-testid="appshot-editor-import-file"
          onChange={(event) => {
            const file = imageFile(event.currentTarget.files);
            event.currentTarget.value = "";
            if (file) onFile(file);
          }}
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
          className="flex h-8 w-full items-center justify-center gap-2 rounded-lg border border-border font-medium transition-colors hover:bg-foreground/10 disabled:opacity-50"
        >
          <FolderOpen className="h-4 w-4 text-muted-foreground" aria-hidden />
          {label("import_file")}
        </button>
        <p className="mt-1.5 text-center text-[12px] text-muted-foreground">{label("import_paste_hint")}</p>
      </div>
    </div>
  );
}

const ROUND_ICON =
  "flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:pointer-events-none disabled:opacity-40";

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
