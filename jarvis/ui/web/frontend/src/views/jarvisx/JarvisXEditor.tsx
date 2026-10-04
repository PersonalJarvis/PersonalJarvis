import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowUpRight,
  Check,
  Circle,
  Copy,
  Crop,
  FolderOpen,
  Grid3x3,
  Highlighter,
  Loader2,
  Minus,
  MousePointer2,
  PenLine,
  Plus,
  Redo2,
  Slash,
  Square,
  Trash2,
  Type,
  Undo2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useElementSize } from "@/hooks/useElementSize";
import { useT } from "@/i18n";
import {
  copyJarvisXItem,
  deleteJarvisXItem,
  fetchJarvisXItem,
  formatDuration,
  revealJarvisXItem,
  saveJarvisXEdited,
  withVersion,
  type JarvisXItem,
} from "@/lib/jarvisxApi";
import {
  DEFAULT_COLOR,
  DEFAULT_FONT_SIZE,
  DEFAULT_STROKE,
  FONT_SIZES,
  PALETTE,
  STROKE_WIDTHS,
  TOOL_SHORTCUTS,
  addShape,
  canRedo,
  canUndo,
  clampZoom,
  commit,
  createHistory,
  cropFromDrag,
  deleteShape,
  finishGesture,
  fitScale,
  hitHandle,
  hitTest,
  imageToView,
  isDirty,
  isMeaningful,
  moveShape,
  newShapeId,
  nextCounterNumber,
  rectFromPoints,
  redo,
  replacePresent,
  resizeShape,
  restyleShape,
  setCrop,
  shapeBounds,
  shapeHandles,
  stepZoom,
  toolForKey,
  undo,
  updateShape,
  viewToImage,
  visibleArea,
  type EditorDoc,
  type Handle,
  type History,
  type Point,
  type Rect,
  type Shape,
  type TextShape,
  type Tool,
} from "@/lib/jarvisxEditorModel";
import { exportPng, measureTextShape, paintDocument, textFont } from "@/lib/jarvisxRender";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { JarvisXConfirmDialog } from "@/views/jarvisx/JarvisXConfirmDialog";

/**
 * The Jarvis X annotation editor.
 *
 * One component for both places it appears: its own desktop window
 * (`/?view=jarvisx-editor&solo=1&item=<id>`) and an overlay over the library.
 * Annotations stay editable objects until Save flattens them into a PNG at the
 * capture's native resolution; an item that already has an edited version
 * opens that version as the base picture (earlier annotations are part of its
 * pixels by then), and "Original" starts over from the untouched capture.
 */

const IS_MAC = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform || "");
const MOD = IS_MAC ? "⌘" : "Ctrl";
/** Hit and handle tolerance, in SCREEN pixels (converted per zoom). */
const HIT_SLOP_PX = 6;
const HANDLE_PX = 5;

interface ToolDef {
  tool: Tool;
  labelKey: string;
  icon: ReactNode;
}

function CounterGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="h-4 w-4">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <text x="12" y="16.2" textAnchor="middle" fontSize="11" fontWeight="700" fill="currentColor">
        1
      </text>
    </svg>
  );
}

function HighlightGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="h-4 w-4">
      <rect x="3" y="6" width="18" height="12" rx="2" fill="currentColor" opacity="0.35" />
      <rect x="3" y="6" width="18" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

const TOOLS: ToolDef[] = [
  { tool: "select", labelKey: "jarvisx.editor.tool_select", icon: <MousePointer2 aria-hidden /> },
  { tool: "rect", labelKey: "jarvisx.editor.tool_rect", icon: <Square aria-hidden /> },
  { tool: "highlight", labelKey: "jarvisx.editor.tool_highlight", icon: <HighlightGlyph /> },
  { tool: "ellipse", labelKey: "jarvisx.editor.tool_ellipse", icon: <Circle aria-hidden /> },
  { tool: "arrow", labelKey: "jarvisx.editor.tool_arrow", icon: <ArrowUpRight aria-hidden /> },
  { tool: "line", labelKey: "jarvisx.editor.tool_line", icon: <Slash aria-hidden /> },
  { tool: "pen", labelKey: "jarvisx.editor.tool_pen", icon: <PenLine aria-hidden /> },
  { tool: "marker", labelKey: "jarvisx.editor.tool_marker", icon: <Highlighter aria-hidden /> },
  { tool: "text", labelKey: "jarvisx.editor.tool_text", icon: <Type aria-hidden /> },
  { tool: "counter", labelKey: "jarvisx.editor.tool_counter", icon: <CounterGlyph /> },
  { tool: "blur", labelKey: "jarvisx.editor.tool_blur", icon: <Grid3x3 aria-hidden /> },
  { tool: "crop", labelKey: "jarvisx.editor.tool_crop", icon: <Crop aria-hidden /> },
];

interface Style {
  color: string;
  width: number;
  shadow: boolean;
  fontSize: number;
}

type Gesture =
  | { type: "draw"; before: EditorDoc; id: string; origin: Point }
  | { type: "move"; before: EditorDoc; id: string; origin: Point; original: Shape }
  | { type: "resize"; before: EditorDoc; id: string; handle: Handle; original: Shape }
  | { type: "crop"; origin: Point; current: Point };

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function handleCursor(handle: Handle): string {
  switch (handle) {
    case "nw":
    case "se":
      return "nwse-resize";
    case "ne":
    case "sw":
      return "nesw-resize";
    case "n":
    case "s":
      return "ns-resize";
    case "e":
    case "w":
      return "ew-resize";
    default:
      return "move";
  }
}

/** Constrain a drag with Shift: square boxes, 45-degree lines. */
function constrain(tool: Tool, origin: Point, p: Point): Point {
  const dx = p.x - origin.x;
  const dy = p.y - origin.y;
  if (tool === "arrow" || tool === "line") {
    const angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
    const len = Math.hypot(dx, dy);
    return { x: origin.x + len * Math.cos(angle), y: origin.y + len * Math.sin(angle) };
  }
  const side = Math.max(Math.abs(dx), Math.abs(dy));
  return { x: origin.x + Math.sign(dx || 1) * side, y: origin.y + Math.sign(dy || 1) * side };
}

function draftShape(tool: Tool, id: string, origin: Point, style: Style): Shape | null {
  const base = { id, color: style.color, width: style.width, shadow: style.shadow };
  switch (tool) {
    case "rect":
    case "highlight":
    case "ellipse":
    case "blur":
      return { ...base, kind: tool, x: origin.x, y: origin.y, w: 0, h: 0 };
    case "arrow":
    case "line":
      return { ...base, kind: tool, x1: origin.x, y1: origin.y, x2: origin.x, y2: origin.y };
    case "pen":
    case "marker":
      return { ...base, kind: tool, points: [origin] };
    default:
      return null;
  }
}

function extendDraft(shape: Shape, origin: Point, p: Point): Shape {
  switch (shape.kind) {
    case "rect":
    case "highlight":
    case "ellipse":
    case "blur":
      return { ...shape, ...rectFromPoints(origin, p) };
    case "arrow":
    case "line":
      return { ...shape, x2: p.x, y2: p.y };
    case "pen":
    case "marker": {
      const last = shape.points[shape.points.length - 1];
      if (last && Math.hypot(p.x - last.x, p.y - last.y) < 1) return shape;
      return { ...shape, points: [...shape.points, p] };
    }
    default:
      return shape;
  }
}

export interface JarvisXEditorProps {
  /** The capture to edit; absent together with `devSrc` opens a local picture read-only. */
  itemId: string | null;
  /** Dev/verification fallback: a same-origin image URL to annotate without an item. */
  devSrc?: string | null;
  variant: "window" | "overlay";
  onClose: () => void;
  /** Extra controls for the leading edge of the toolbar row (the window caption). */
  leading?: ReactNode;
  /** Controls for the trailing edge (the window buttons). */
  trailing?: ReactNode;
}

export function JarvisXEditor({ itemId, devSrc, variant, onClose, leading, trailing }: JarvisXEditorProps) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);

  const [item, setItem] = useState<JarvisXItem | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [base, setBase] = useState<"edited" | "original">("edited");
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [history, setHistory] = useState<History>(() => createHistory());
  const [savedDoc, setSavedDoc] = useState<EditorDoc>(history.present);
  const [tool, setTool] = useState<Tool>("rect");
  const [style, setStyle] = useState<Style>({
    color: DEFAULT_COLOR,
    width: DEFAULT_STROKE,
    shadow: true,
    fontSize: DEFAULT_FONT_SIZE,
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingTextId, setEditingTextId] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number | "fit">("fit");
  const [cropPreview, setCropPreview] = useState<Rect | null>(null);
  const [busy, setBusy] = useState<null | "save" | "copy" | "close">(null);
  const [confirm, setConfirm] = useState<null | "discard" | "delete" | "original">(null);
  const [cursor, setCursor] = useState("crosshair");
  const [reloadKey, setReloadKey] = useState(0);

  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const historyRef = useRef(history);
  historyRef.current = history;
  const stage = useElementSize(stageRef);

  const doc = history.present;
  const isVideo = item?.kind === "video";
  const canPersist = Boolean(itemId && item);

  // ---- loading -----------------------------------------------------------

  useEffect(() => {
    if (!itemId) {
      setItem(null);
      setLoadError(devSrc ? null : t("jarvisx.editor.no_item"));
      return;
    }
    let live = true;
    setLoadError(null);
    fetchJarvisXItem(itemId)
      .then((next) => {
        if (!live) return;
        setItem(next);
        setBase(next.edited_url ? "edited" : "original");
      })
      .catch((error: Error) => live && setLoadError(error.message));
    return () => {
      live = false;
    };
  }, [itemId, devSrc, reloadKey, t]);

  const imageSrc = useMemo(() => {
    if (item && item.kind === "image") {
      if (base === "edited" && item.edited_url) return withVersion(item.edited_url, reloadKey);
      return item.url;
    }
    return !itemId && devSrc ? devSrc : null;
  }, [item, base, itemId, devSrc, reloadKey]);

  useEffect(() => {
    if (!imageSrc) {
      setImage(null);
      return;
    }
    let live = true;
    const img = new Image();
    img.decoding = "async";
    img.onload = () => {
      if (!live) return;
      setImage(img);
      const fresh = createHistory();
      setHistory(fresh);
      setSavedDoc(fresh.present);
      setSelectedId(null);
      setEditingTextId(null);
      setZoom("fit");
    };
    img.onerror = () => live && setLoadError(t("jarvisx.editor.image_failed"));
    img.src = imageSrc;
    return () => {
      live = false;
    };
  }, [imageSrc, t]);

  // ---- geometry ----------------------------------------------------------

  const natW = image?.naturalWidth ?? 0;
  const natH = image?.naturalHeight ?? 0;
  // The crop tool shows the whole picture so the crop can grow again.
  const area: Rect = tool === "crop" ? { x: 0, y: 0, w: natW, h: natH } : visibleArea(doc, natW, natH);
  const fit = fitScale(stage.width, stage.height, area.w, area.h);
  const scale = zoom === "fit" ? fit : zoom;
  const viewW = Math.max(1, Math.round(area.w * scale));
  const viewH = Math.max(1, Math.round(area.h * scale));
  const slop = HIT_SLOP_PX / scale;

  const measure = useCallback((shape: TextShape) => measureTextShape(shape), []);

  // ---- painting ----------------------------------------------------------

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !image) return;
    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    canvas.width = Math.round(viewW * dpr);
    canvas.height = Math.round(viewH * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const k = scale * dpr;
    ctx.setTransform(k, 0, 0, k, -area.x * k, -area.y * k);
    ctx.imageSmoothingQuality = "high";
    paintDocument(ctx, image, doc, editingTextId);

    // Overlays in device pixels so they stay crisp at any zoom.
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (tool === "crop") {
      const crop = cropPreview ?? doc.crop;
      if (crop) {
        const a = imageToView({ x: crop.x, y: crop.y }, scale, area);
        const w = crop.w * scale;
        const h = crop.h * scale;
        ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
        ctx.beginPath();
        ctx.rect(0, 0, viewW, viewH);
        ctx.rect(a.x, a.y, w, h);
        ctx.fill("evenodd");
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]);
        ctx.strokeRect(a.x, a.y, w, h);
        ctx.setLineDash([]);
      }
    }
    const selected = selectedId && selectedId !== editingTextId ? doc.shapes.find((s) => s.id === selectedId) : null;
    if (selected && tool !== "crop") {
      const b = shapeBounds(selected, measure);
      const pad = selected.width / 2 + 3 / scale;
      const tl = imageToView({ x: b.x - pad, y: b.y - pad }, scale, area);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(tl.x, tl.y, (b.w + pad * 2) * scale, (b.h + pad * 2) * scale);
      ctx.strokeStyle = "rgba(0, 0, 0, 0.6)";
      ctx.lineDashOffset = 3.5;
      ctx.strokeRect(tl.x, tl.y, (b.w + pad * 2) * scale, (b.h + pad * 2) * scale);
      ctx.setLineDash([]);
      ctx.lineDashOffset = 0;
      for (const h of shapeHandles(selected)) {
        const v = imageToView(h, scale, area);
        ctx.fillStyle = "#ffffff";
        ctx.strokeStyle = "rgba(0, 0, 0, 0.7)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(v.x, v.y, HANDLE_PX, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
    }
  }, [image, doc, scale, viewW, viewH, area.x, area.y, tool, cropPreview, selectedId, editingTextId, measure]);

  // ---- edits -------------------------------------------------------------

  const apply = useCallback((next: (d: EditorDoc) => EditorDoc) => {
    setHistory((h) => commit(h, next(h.present)));
  }, []);

  const doUndo = useCallback(() => {
    setEditingTextId(null);
    setHistory((h) => undo(h));
  }, []);
  const doRedo = useCallback(() => setHistory((h) => redo(h)), []);

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    apply((d) => deleteShape(d, selectedId));
    setSelectedId(null);
  }, [apply, selectedId]);

  const changeStyle = useCallback(
    (patch: Partial<Style>) => {
      setStyle((s) => ({ ...s, ...patch }));
      if (selectedId) apply((d) => updateShape(d, selectedId, (shape) => restyleShape(shape, patch)));
    },
    [apply, selectedId],
  );

  const chooseTool = useCallback((next: Tool) => {
    setTool(next);
    setCropPreview(null);
    if (next !== "select") setSelectedId(null);
  }, []);

  // ---- pointer -----------------------------------------------------------

  const toImage = (event: React.PointerEvent | React.MouseEvent): Point => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return viewToImage({ x: event.clientX - rect.left, y: event.clientY - rect.top }, scale, area);
  };

  const commitText = useCallback(
    (id: string, text: string) => {
      setEditingTextId(null);
      setHistory((h) => {
        const shape = h.present.shapes.find((s) => s.id === id);
        if (!shape || shape.kind !== "text") return h;
        // A new text shape entered the document without a history step; its
        // undo point is the document WITHOUT it.
        const isNew = shape.text === "";
        const without = deleteShape(h.present, id);
        if (!text.trim()) return isNew ? replacePresent(h, without) : commit(h, without);
        if (shape.text === text) return h;
        const next = updateShape(h.present, id, (s) => ({ ...(s as TextShape), text }));
        return isNew ? finishGesture(replacePresent(h, next), without) : commit(h, next);
      });
    },
    [],
  );

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!image || event.button !== 0) return;
    if (editingTextId) {
      // A click outside the text field ends the edit; the textarea's blur commits it.
      (document.activeElement as HTMLElement | null)?.blur();
      return;
    }
    const p = toImage(event);
    const present = historyRef.current.present;
    event.currentTarget.setPointerCapture(event.pointerId);

    if (tool === "crop") {
      gestureRef.current = { type: "crop", origin: p, current: p };
      setCropPreview(null);
      return;
    }

    // The selected shape's handles win in every tool: resize right after drawing.
    const selected = selectedId ? present.shapes.find((s) => s.id === selectedId) : null;
    if (selected) {
      const handle = hitHandle(selected, p, (HANDLE_PX + 3) / scale);
      if (handle) {
        gestureRef.current = { type: "resize", before: present, id: selected.id, handle, original: selected };
        return;
      }
    }

    if (tool === "select") {
      const hit = hitTest(present.shapes, p, slop, measure);
      setSelectedId(hit);
      if (hit) {
        const original = present.shapes.find((s) => s.id === hit)!;
        gestureRef.current = { type: "move", before: present, id: hit, origin: p, original };
        if (original.kind === "text") {
          setStyle((s) => ({ ...s, color: original.color, fontSize: original.fontSize }));
        } else {
          setStyle((s) => ({ ...s, color: original.color, width: original.width, shadow: original.shadow }));
        }
      }
      return;
    }

    if (tool === "text") {
      const existing = hitTest(present.shapes, p, slop, measure);
      const hitShape = existing ? present.shapes.find((s) => s.id === existing) : null;
      if (hitShape && hitShape.kind === "text") {
        setSelectedId(hitShape.id);
        setEditingTextId(hitShape.id);
        return;
      }
      const id = newShapeId();
      const shape: TextShape = {
        id,
        kind: "text",
        color: style.color,
        width: style.width,
        shadow: style.shadow,
        fontSize: style.fontSize,
        x: p.x,
        y: p.y - (style.fontSize * 1.25) / 2,
        text: "",
      };
      // Not in the history until it has text: an empty click leaves no step.
      setHistory((h) => replacePresent(h, addShape(h.present, shape)));
      setSelectedId(id);
      setEditingTextId(id);
      return;
    }

    if (tool === "counter") {
      const id = newShapeId();
      apply((d) =>
        addShape(d, {
          id,
          kind: "counter",
          color: style.color,
          width: style.width,
          shadow: style.shadow,
          x: p.x,
          y: p.y,
          n: nextCounterNumber(d.shapes),
        }),
      );
      setSelectedId(id);
      return;
    }

    const id = newShapeId();
    const draft = draftShape(tool, id, p, style);
    if (!draft) return;
    gestureRef.current = { type: "draw", before: present, id, origin: p };
    setHistory((h) => replacePresent(h, addShape(h.present, draft)));
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!image) return;
    let p = toImage(event);
    const gesture = gestureRef.current;
    if (!gesture) {
      // Hover feedback only.
      const present = historyRef.current.present;
      const selected = selectedId ? present.shapes.find((s) => s.id === selectedId) : null;
      const handle = selected && tool !== "crop" ? hitHandle(selected, p, (HANDLE_PX + 3) / scale) : null;
      if (handle) setCursor(handleCursor(handle));
      else if (tool === "select") setCursor(hitTest(present.shapes, p, slop, measure) ? "move" : "default");
      else if (tool === "text") setCursor("text");
      else setCursor("crosshair");
      return;
    }
    if (gesture.type === "crop") {
      gesture.current = p;
      setCropPreview(rectFromPoints(gesture.origin, p));
      return;
    }
    if (gesture.type === "draw") {
      if (event.shiftKey) p = constrain(tool, gesture.origin, p);
      setHistory((h) =>
        replacePresent(h, updateShape(h.present, gesture.id, (s) => extendDraft(s, gesture.origin, p))),
      );
      return;
    }
    if (gesture.type === "move") {
      const dx = p.x - gesture.origin.x;
      const dy = p.y - gesture.origin.y;
      setHistory((h) => replacePresent(h, updateShape(h.present, gesture.id, () => moveShape(gesture.original, dx, dy))));
      return;
    }
    if (gesture.type === "resize") {
      setHistory((h) =>
        replacePresent(h, updateShape(h.present, gesture.id, () => resizeShape(gesture.original, gesture.handle, p))),
      );
    }
  };

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const gesture = gestureRef.current;
    gestureRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (!gesture || !image) return;
    if (gesture.type === "crop") {
      setCropPreview(null);
      const crop = cropFromDrag(gesture.origin, gesture.current, natW, natH);
      if (crop) apply((d) => setCrop(d, crop));
      return;
    }
    if (gesture.type === "draw") {
      setHistory((h) => {
        const shape = h.present.shapes.find((s) => s.id === gesture.id);
        if (!shape || !isMeaningful(shape)) return replacePresent(h, gesture.before);
        return finishGesture(h, gesture.before);
      });
      setSelectedId(gesture.id);
      return;
    }
    setHistory((h) => finishGesture(h, gesture.before));
  };

  const onDoubleClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (tool !== "select") return;
    const p = toImage(event);
    const hit = hitTest(doc.shapes, p, slop, measure);
    const shape = hit ? doc.shapes.find((s) => s.id === hit) : null;
    if (shape?.kind === "text") {
      setSelectedId(shape.id);
      setEditingTextId(shape.id);
    }
  };

  const onWheel = (event: React.WheelEvent) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    setZoom(clampZoom(scale * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
  };

  // ---- actions -----------------------------------------------------------

  const dirtySinceSave = doc !== savedDoc && isDirty(doc);

  const save = useCallback(async (): Promise<boolean> => {
    if (!image || !item || !itemId) return false;
    setEditingTextId(null);
    try {
      const png = await exportPng(image, historyRef.current.present);
      const next = await saveJarvisXEdited(itemId, png);
      setItem((current) => ({ ...(current ?? item), ...(next ?? {}) }));
      setSavedDoc(historyRef.current.present);
      return true;
    } catch (error) {
      pushToast("error", t("jarvisx.editor.save_failed").replace("{0}", (error as Error).message));
      return false;
    }
  }, [image, item, itemId, pushToast, t]);

  const onSave = useCallback(async () => {
    if (!canPersist || busy) return;
    setBusy("save");
    if (await save()) pushToast("success", t("jarvisx.editor.saved"));
    setBusy(null);
  }, [busy, canPersist, pushToast, save, t]);

  const onCopy = useCallback(async () => {
    if (!canPersist || busy || !itemId) return;
    setBusy("copy");
    try {
      let edited = base === "edited" && Boolean(item?.edited_url);
      if (isVideo) edited = false;
      else if (dirtySinceSave) {
        if (!(await save())) return;
        edited = true;
      } else if (isDirty(doc)) edited = true;
      const result = await copyJarvisXItem(itemId, edited);
      if (result.ok === false) pushToast("error", result.message || t("jarvisx.copy_failed"));
      else pushToast("success", t("jarvisx.copied"));
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setBusy(null);
    }
  }, [base, busy, canPersist, dirtySinceSave, doc, isVideo, item, itemId, pushToast, save, t]);

  const onSaveAndClose = useCallback(async () => {
    if (!canPersist || busy) return;
    setBusy("close");
    const ok = !dirtySinceSave || (await save());
    setBusy(null);
    if (ok) onClose();
  }, [busy, canPersist, dirtySinceSave, onClose, save]);

  const onReveal = useCallback(async () => {
    if (!itemId) return;
    try {
      const result = await revealJarvisXItem(itemId);
      if (result.ok === false && result.message) pushToast("error", result.message);
    } catch (error) {
      pushToast("error", (error as Error).message);
    }
  }, [itemId, pushToast]);

  const onDelete = useCallback(async () => {
    if (!itemId) return;
    try {
      await deleteJarvisXItem(itemId);
      pushToast("success", t("jarvisx.deleted"));
      onClose();
    } catch (error) {
      pushToast("error", (error as Error).message);
    }
  }, [itemId, onClose, pushToast, t]);

  const requestClose = useCallback(() => {
    if (dirtySinceSave) setConfirm("discard");
    else onClose();
  }, [dirtySinceSave, onClose]);

  // ---- keyboard ----------------------------------------------------------

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (confirm || isTypingTarget(event.target)) return;
      const mod = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (mod && key === "z") {
        event.preventDefault();
        if (event.shiftKey) doRedo();
        else doUndo();
        return;
      }
      if (mod && key === "y") {
        event.preventDefault();
        doRedo();
        return;
      }
      if (mod && key === "s") {
        event.preventDefault();
        void onSave();
        return;
      }
      if (mod && key === "c") {
        event.preventDefault();
        void onCopy();
        return;
      }
      if (mod && (key === "0" || key === "1")) {
        event.preventDefault();
        setZoom(key === "0" ? "fit" : 1);
        return;
      }
      if (mod && (key === "=" || key === "+" || key === "-")) {
        event.preventDefault();
        setZoom(clampZoom(stepZoom(scale, key === "-" ? -1 : 1)));
        return;
      }
      if (mod || event.altKey) return;
      if ((event.key === "Delete" || event.key === "Backspace") && selectedId) {
        event.preventDefault();
        deleteSelected();
        return;
      }
      if (event.key === "Escape") {
        if (selectedId) setSelectedId(null);
        else if (tool === "crop") chooseTool("select");
        else requestClose();
        return;
      }
      if (event.key === "Enter" && tool === "crop") {
        chooseTool("select");
        return;
      }
      const next = toolForKey(event.key);
      if (next && !isVideo) chooseTool(next);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chooseTool, confirm, deleteSelected, doRedo, doUndo, isVideo, onCopy, onSave, requestClose, scale, selectedId, tool]);

  // ---- render ------------------------------------------------------------

  const selectedShape = selectedId ? doc.shapes.find((s) => s.id === selectedId) ?? null : null;
  const styleTarget: Tool | Shape["kind"] = selectedShape?.kind ?? tool;
  const showsColor = styleTarget !== "blur" && styleTarget !== "crop" && styleTarget !== "select";
  const showsWidth = !["text", "crop", "select", "highlight"].includes(styleTarget);
  const showsFont = styleTarget === "text";
  const showsShadow = ["rect", "ellipse", "arrow", "line", "pen", "text", "counter"].includes(styleTarget);
  const editingText = editingTextId ? (doc.shapes.find((s) => s.id === editingTextId) as TextShape | undefined) : undefined;
  const title = item?.filename ?? (devSrc ? devSrc.split("/").pop() ?? "" : "");

  return (
    <div
      data-testid="jarvisx-editor"
      className={cn(
        "flex h-full min-h-0 w-full flex-col bg-background text-foreground",
        variant === "overlay" && "rounded-xl border border-border shadow-2xl",
      )}
    >
      {/* Toolbar row. In the window variant its empty middle is the drag handle. */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-card pl-2">
        {leading}
        {!isVideo && (
          <div role="toolbar" aria-label={t("jarvisx.editor.tools")} className="flex items-center gap-0.5">
            {TOOLS.map((def) => {
              const label = `${t(def.labelKey)} (${TOOL_SHORTCUTS[def.tool]})`;
              return (
                <QuickTooltip key={def.tool} content={label} side="bottom">
                  <button
                    type="button"
                    aria-label={label}
                    aria-pressed={tool === def.tool}
                    data-testid={`jarvisx-tool-${def.tool}`}
                    onClick={() => chooseTool(def.tool)}
                    className={cn(
                      "grid h-8 w-8 place-items-center rounded-md text-muted-foreground transition-colors [&>svg]:h-4 [&>svg]:w-4",
                      "hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      tool === def.tool && "bg-accent-soft text-accent hover:bg-accent-soft hover:text-accent",
                    )}
                  >
                    {def.icon}
                  </button>
                </QuickTooltip>
              );
            })}
          </div>
        )}
        <div
          className={cn("h-full min-w-6 flex-1 truncate px-2 text-center text-sm leading-[3rem] text-muted-foreground", variant === "window" && "pywebview-drag-region")}
          title={title}
        >
          {title}
        </div>
        <div className="flex shrink-0 items-center gap-1 pr-2">
          {!isVideo && (
            <>
              <IconButton label={`${t("jarvisx.editor.undo")} (${MOD}+Z)`} disabled={!canUndo(history)} onClick={doUndo}>
                <Undo2 aria-hidden />
              </IconButton>
              <IconButton label={`${t("jarvisx.editor.redo")} (${MOD}+Shift+Z)`} disabled={!canRedo(history)} onClick={doRedo}>
                <Redo2 aria-hidden />
              </IconButton>
              <span className="mx-1 h-5 w-px bg-border" aria-hidden />
            </>
          )}
          {canPersist && (
            <>
              <IconButton label={t("jarvisx.reveal")} onClick={() => void onReveal()}>
                <FolderOpen aria-hidden />
              </IconButton>
              <IconButton label={t("jarvisx.delete")} onClick={() => setConfirm("delete")} testId="jarvisx-editor-delete">
                <Trash2 aria-hidden />
              </IconButton>
              <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={() => void onCopy()} data-testid="jarvisx-editor-copy">
                {busy === "copy" ? <Loader2 className="animate-spin" aria-hidden /> : <Copy aria-hidden />}
                {t("jarvisx.copy")}
              </Button>
              {!isVideo && (
                <Button type="button" size="sm" variant="secondary" disabled={busy !== null || !dirtySinceSave} onClick={() => void onSave()} data-testid="jarvisx-editor-save">
                  {busy === "save" && <Loader2 className="animate-spin" aria-hidden />}
                  {t("jarvisx.editor.save")}
                </Button>
              )}
              <Button type="button" size="sm" disabled={busy !== null} onClick={() => void (isVideo ? onClose() : onSaveAndClose())} data-testid="jarvisx-editor-done">
                {busy === "close" ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
                {isVideo ? t("common.close") : t("jarvisx.editor.done")}
              </Button>
            </>
          )}
          {!canPersist && (
            <IconButton label={t("common.close")} onClick={requestClose}>
              <X aria-hidden />
            </IconButton>
          )}
        </div>
        {trailing}
      </div>

      {/* Style row: only what the active tool or selection actually uses. */}
      {!isVideo && image && (
        <div className="flex h-11 shrink-0 items-center gap-3 overflow-x-auto border-b border-border bg-card px-3 scrollbar-jarvis">
          {showsColor && (
            <div role="radiogroup" aria-label={t("jarvisx.editor.color")} className="flex items-center gap-1">
              {PALETTE.map((color) => (
                <button
                  key={color}
                  type="button"
                  role="radio"
                  aria-checked={style.color === color}
                  aria-label={color}
                  onClick={() => changeStyle({ color })}
                  className={cn(
                    "h-5 w-5 rounded-full border border-border-strong transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    style.color === color && "ring-2 ring-accent ring-offset-2 ring-offset-card",
                  )}
                  style={{ backgroundColor: color }}
                />
              ))}
            </div>
          )}
          {showsWidth && (
            <div role="radiogroup" aria-label={t("jarvisx.editor.stroke")} className="flex items-center gap-0.5">
              {STROKE_WIDTHS.map((width) => (
                <button
                  key={width}
                  type="button"
                  role="radio"
                  aria-checked={style.width === width}
                  aria-label={`${t("jarvisx.editor.stroke")} ${width}`}
                  onClick={() => changeStyle({ width })}
                  className={cn(
                    "grid h-7 w-7 place-items-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground",
                    style.width === width && "bg-secondary text-foreground",
                  )}
                >
                  <span className="rounded-full bg-current" style={{ width: 4 + width * 1.2, height: 4 + width * 1.2 }} />
                </button>
              ))}
            </div>
          )}
          {showsFont && (
            <div role="radiogroup" aria-label={t("jarvisx.editor.font_size")} className="flex items-center gap-0.5">
              {FONT_SIZES.map((size) => (
                <button
                  key={size}
                  type="button"
                  role="radio"
                  aria-checked={style.fontSize === size}
                  onClick={() => changeStyle({ fontSize: size })}
                  className={cn(
                    "h-7 min-w-8 rounded-md px-1.5 text-sm tabular-nums text-muted-foreground hover:bg-secondary hover:text-foreground",
                    style.fontSize === size && "bg-secondary text-foreground",
                  )}
                >
                  {size}
                </button>
              ))}
            </div>
          )}
          {showsShadow && (
            <label className="flex cursor-pointer items-center gap-1.5 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={style.shadow}
                onChange={(event) => changeStyle({ shadow: event.target.checked })}
                className="h-3.5 w-3.5 accent-[hsl(var(--accent))]"
              />
              {t("jarvisx.editor.shadow")}
            </label>
          )}
          {tool === "crop" && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <span>{doc.crop ? `${doc.crop.w} × ${doc.crop.h}` : t("jarvisx.editor.crop_hint")}</span>
              {doc.crop && (
                <Button type="button" size="sm" variant="ghost" onClick={() => apply((d) => setCrop(d, null))}>
                  {t("jarvisx.editor.crop_reset")}
                </Button>
              )}
              <Button type="button" size="sm" variant="secondary" onClick={() => chooseTool("select")}>
                {t("jarvisx.editor.crop_apply")}
              </Button>
            </div>
          )}
          {tool === "select" && !selectedShape && (
            <span className="text-sm text-muted-foreground">{t("jarvisx.editor.select_hint")}</span>
          )}
          {selectedShape && (
            <IconButton label={`${t("jarvisx.editor.delete_shape")} (Del)`} onClick={deleteSelected}>
              <Trash2 aria-hidden />
            </IconButton>
          )}
          <div className="ml-auto flex shrink-0 items-center gap-1">
            {item?.edited_url && (
              <div className="mr-2 flex items-center rounded-md bg-secondary p-0.5 text-sm" role="radiogroup" aria-label={t("jarvisx.editor.base")}>
                {(["edited", "original"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={base === value}
                    onClick={() => {
                      if (base === value) return;
                      if (isDirty(doc) && doc !== savedDoc) setConfirm("original");
                      else setBase(value);
                    }}
                    className={cn(
                      "h-6 rounded px-2 text-muted-foreground",
                      base === value && "bg-card text-foreground shadow-sm",
                    )}
                  >
                    {t(`jarvisx.editor.base_${value}`)}
                  </button>
                ))}
              </div>
            )}
            <IconButton label={`${t("jarvisx.editor.zoom_out")} (${MOD}+−)`} onClick={() => setZoom(clampZoom(stepZoom(scale, -1)))}>
              <Minus aria-hidden />
            </IconButton>
            <button
              type="button"
              onClick={() => setZoom(zoom === "fit" ? 1 : "fit")}
              title={`${t("jarvisx.editor.zoom_fit")} (${MOD}+0) · 100 % (${MOD}+1)`}
              className="h-7 min-w-14 rounded-md px-2 text-sm tabular-nums text-muted-foreground hover:bg-secondary hover:text-foreground"
              data-testid="jarvisx-zoom"
            >
              {Math.round(scale * 100)} %
            </button>
            <IconButton label={`${t("jarvisx.editor.zoom_in")} (${MOD}++)`} onClick={() => setZoom(clampZoom(stepZoom(scale, 1)))}>
              <Plus aria-hidden />
            </IconButton>
          </div>
        </div>
      )}

      {/* Stage */}
      <div
        ref={stageRef}
        onWheel={onWheel}
        className="relative min-h-0 flex-1 overflow-auto bg-secondary/50 scrollbar-jarvis"
      >
        {loadError ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
            <p className="text-base text-foreground">{t("jarvisx.editor.load_failed")}</p>
            <p className="max-w-md text-sm text-muted-foreground">{loadError}</p>
            {itemId && (
              <Button type="button" size="sm" variant="secondary" onClick={() => setReloadKey((k) => k + 1)}>
                {t("common.retry")}
              </Button>
            )}
          </div>
        ) : isVideo && item ? (
          <div className="flex h-full items-center justify-center p-6">
            <video
              src={item.url}
              controls
              className="max-h-full max-w-full rounded-lg bg-scrim shadow-lg"
              data-testid="jarvisx-video"
            />
            {item.duration_s !== null && (
              <span className="sr-only">{formatDuration(item.duration_s)}</span>
            )}
          </div>
        ) : !image ? (
          <div className="flex h-full items-center justify-center" role="status" aria-busy="true">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
          </div>
        ) : (
          <div
            className="flex min-h-full min-w-full items-center justify-center p-6"
            style={{ width: viewW + 48, height: viewH + 48 }}
          >
            <div className="relative shrink-0 shadow-lg ring-1 ring-border" style={{ width: viewW, height: viewH }}>
              <canvas
                ref={canvasRef}
                data-testid="jarvisx-canvas"
                style={{ width: viewW, height: viewH, cursor, touchAction: "none" }}
                className="block"
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
                onDoubleClick={onDoubleClick}
              />
              {editingText && (
                <TextEditor
                  key={editingText.id}
                  shape={editingText}
                  scale={scale}
                  area={area}
                  onCommit={(text) => commitText(editingText.id, text)}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {!canPersist && devSrc && !itemId && (
        <p className="shrink-0 border-t border-border bg-card px-4 py-2 text-sm text-muted-foreground">
          {t("jarvisx.editor.dev_hint")}
        </p>
      )}

      {confirm === "discard" && (
        <JarvisXConfirmDialog
          title={t("jarvisx.editor.discard_title")}
          body={t("jarvisx.editor.discard_body")}
          confirmLabel={t("jarvisx.editor.discard_confirm")}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            onClose();
          }}
        />
      )}
      {confirm === "original" && (
        <JarvisXConfirmDialog
          title={t("jarvisx.editor.switch_title")}
          body={t("jarvisx.editor.switch_body")}
          confirmLabel={t("jarvisx.editor.discard_confirm")}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            setBase(base === "edited" ? "original" : "edited");
          }}
        />
      )}
      {confirm === "delete" && (
        <JarvisXConfirmDialog
          title={t("jarvisx.delete_title")}
          body={t("jarvisx.delete_body")}
          confirmLabel={t("jarvisx.delete")}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            void onDelete();
          }}
        />
      )}
    </div>
  );
}

function IconButton({
  label,
  onClick,
  disabled,
  children,
  testId,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <QuickTooltip content={label} side="bottom">
      <button
        type="button"
        aria-label={label}
        disabled={disabled}
        onClick={onClick}
        data-testid={testId}
        className="grid h-8 w-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40 [&>svg]:h-4 [&>svg]:w-4"
      >
        {children}
      </button>
    </QuickTooltip>
  );
}

/** In-place text field over a text shape; Enter commits, Shift+Enter breaks the line. */
function TextEditor({
  shape,
  scale,
  area,
  onCommit,
}: {
  shape: TextShape;
  scale: number;
  area: Rect;
  onCommit: (text: string) => void;
}) {
  const [value, setValue] = useState(shape.text);
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  const pos = imageToView({ x: shape.x, y: shape.y }, scale, area);
  const size = measureTextShape({ text: value || "M", fontSize: shape.fontSize });

  useEffect(() => {
    // Focus after the pointer gesture that created it has finished.
    const id = window.setTimeout(() => ref.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, []);

  const finish = () => {
    if (done.current) return;
    done.current = true;
    onCommit(value);
  };

  return (
    <textarea
      ref={ref}
      value={value}
      data-testid="jarvisx-text-input"
      onChange={(event) => setValue(event.target.value)}
      onBlur={finish}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          finish();
        } else if (event.key === "Escape") {
          event.preventDefault();
          finish();
        }
      }}
      spellCheck={false}
      rows={Math.max(1, value.split("\n").length)}
      className="absolute resize-none overflow-hidden border border-dashed border-accent bg-transparent p-0 outline-none"
      style={{
        left: pos.x,
        top: pos.y,
        width: size.w * scale + 24,
        height: size.h * scale + 4,
        font: textFont(shape.fontSize * scale),
        lineHeight: 1.25,
        color: shape.color,
        caretColor: shape.color,
      }}
    />
  );
}
