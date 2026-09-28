import { useCallback, useEffect, useRef, useState } from "react";
import { AgenticTerminal } from "./AgenticTerminal";
import { AgentMark } from "./AgentMark";
import type { PaneSplitDirection } from "./WorkspaceTerminalHeader";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";
import { moveTerminal, renameTerminal, type PaneMovePosition } from "@/lib/agenticIdeApi";
import { useThemeValue } from "@/hooks/useTheme";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { treeLayout, treeLeaves, type LayoutNode } from "./treeLayout";
import { DOCK_LABELS, GRID_LIMIT_HINT, MAX_GRID_COLUMNS, MAX_GRID_ROWS, MAX_WORKSPACE_PANES, dockPosition, fitsWorkspace, layoutSpan, paneStyle, previewDock, workspaceLayout } from "./workspaceDocking";

const GAP = 8;
const MIN_WIDTH = 280;
const idOf = (terminal: TerminalState) => terminal.history_id ?? terminal.key;

interface Props {
  session: SessionState;
  onChanged: (session: SessionState) => void;
  /** Open the agent picker; with an anchor, the new pane splits off that pane. */
  onAdd: (anchor?: string, direction?: PaneSplitDirection) => void;
  onClose: (terminal: TerminalState) => void;
  onSelect: (name: string) => void;
  selected: string;
  /** The server's per-workspace pane limit; splitting stops there. */
  maxPanes?: number;
  fontSize: number;
  appearance: "light" | "dark" | null;
  disabled?: boolean;
  onMutationStart?: () => void;
  onMutationEnd?: () => void;
}

interface DropTarget { id: string; position: PaneMovePosition; allowed: boolean }
interface DragFeedback { id: string; target: DropTarget | null; x: number; y: number }

export function WorkspaceTerminalGrid({ session, onChanged, onAdd, onClose, onSelect, selected, maxPanes = MAX_WORKSPACE_PANES, fontSize, appearance, disabled = false, onMutationStart, onMutationEnd }: Props) {
  const theme = useThemeValue();
  const pushToast = useEventStore((state) => state.pushToast);
  const frame = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<DragFeedback | null>(null);
  const [optimistic, setOptimistic] = useState<LayoutNode | null>(null);
  const [saving, setSaving] = useState(false);
  const [maximized, setMaximized] = useState<string | null>(null);
  const [restarts, setRestarts] = useState<Record<string, number>>({});
  const [announcement, setAnnouncement] = useState("");
  const dragCleanup = useRef<(() => void) | null>(null);
  const saveInFlight = useRef(false);
  const mounted = useRef(true);
  const latest = useRef({ session, disabled, onChanged, onSelect, onMutationStart, onMutationEnd });
  latest.current = { session, disabled, onChanged, onSelect, onMutationStart, onMutationEnd };

  const members = session.terminals.map(idOf);
  const keys = session.terminals.map((terminal) => terminal.key);
  const pendingKeys = treeLeaves(optimistic);
  const pendingOrderMatches = optimistic && pendingKeys.length === keys.length && pendingKeys.every((key) => keys.includes(key));
  const tree = pendingOrderMatches ? optimistic : workspaceLayout(session.layout, session.terminals);
  const tiles = session.terminals;
  const layout = treeLayout(tree, tiles);
  const columns = Math.max(1, layoutSpan(tree, "row"));
  const visibleMaximized = maximized && members.includes(maximized) ? maximized : null;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; dragCleanup.current?.(); };
  }, []);
  useEffect(() => {
    if (maximized && !members.includes(maximized)) setMaximized(null);
  }, [maximized, members]);
  useEffect(() => { if (disabled) dragCleanup.current?.(); }, [disabled]);
  useEffect(() => {
    const node = frame.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width === 0) dragCleanup.current?.();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const move = useCallback(async (sourceId: string, targetId: string, position: PaneMovePosition = "swap") => {
    if (saveInFlight.current || latest.current.disabled || sourceId === targetId) return;
    const owner = latest.current.session;
    const source = owner.terminals.find((terminal) => idOf(terminal) === sourceId);
    const target = owner.terminals.find((terminal) => idOf(terminal) === targetId);
    const before = workspaceLayout(owner.layout, owner.terminals);
    if (!before || !source || !target) return;
    const next = previewDock(before, source.key, target.key, position);
    if (!fitsWorkspace(next)) { setAnnouncement(GRID_LIMIT_HINT); return; }
    saveInFlight.current = true;
    latest.current.onMutationStart?.();
    setSaving(true);
    setOptimistic(next);
    try {
      // Unique pane identities fail closed if the active workspace changes.
      const nextSession = await moveTerminal(source.history_id ? `pane:${source.history_id}` : source.name,
        target.history_id ? `pane:${target.history_id}` : target.name, position);
      if (!mounted.current || latest.current.session.id !== owner.id) return;
      // A rename/close or a newer snapshot must never be overwritten by the
      // response to an earlier drag. The parent also invalidates pending polls.
      if (latest.current.session === owner && nextSession.id === owner.id) latest.current.onChanged(nextSession);
      setAnnouncement(position === "swap" ? `${source.name} and ${target.name} swapped.` : `${source.name} placed ${position} ${target.name}.`);
    } catch (error) {
      if (mounted.current) {
        pushToast("error", (error as Error).message);
        setAnnouncement("Could not save the arrangement. The previous order was restored.");
      }
    } finally {
      saveInFlight.current = false;
      latest.current.onMutationEnd?.();
      if (mounted.current) { setSaving(false); setOptimistic(null); }
    }
  }, [pushToast]);

  const startDrag = useCallback((id: string, event: React.PointerEvent) => {
    if (event.button !== 0 || latest.current.disabled || dragCleanup.current || saveInFlight.current) return;
    const handle = event.currentTarget;
    const pointer = event.pointerId;
    const initialX = event.clientX, initialY = event.clientY;
    let armed = false;
    // Capture keeps xterm's selection/pointer handlers from eating a release.
    handle.setPointerCapture?.(pointer);
    const targetAt = (x: number, y: number): DropTarget | null => {
      const candidates = frame.current?.querySelectorAll<HTMLElement>("[data-session-id]") ?? [];
      for (const tile of candidates) {
        const rect = tile.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
          const targetId = tile.dataset.sessionId;
          const owner = latest.current.session;
          const source = owner.terminals.find((terminal) => idOf(terminal) === id);
          const target = owner.terminals.find((terminal) => idOf(terminal) === targetId);
          const before = workspaceLayout(owner.layout, owner.terminals);
          if (!source || !target || !before || targetId === id) return null;
          const position = dockPosition(x, y, rect);
          return { id: targetId!, position, allowed: fitsWorkspace(previewDock(before, source.key, target.key, position)) };
        }
      }
      return null;
    };
    const onMove = (motion: PointerEvent) => {
      if (motion.pointerId !== pointer) return;
      if (!armed && Math.hypot(motion.clientX - initialX, motion.clientY - initialY) <= 5) return;
      armed = true;
      motion.preventDefault();
      setDrag({ id, target: targetAt(motion.clientX, motion.clientY), x: motion.clientX, y: motion.clientY });
    };
    const cleanup = () => {
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointercancel", onCancel, true);
      window.removeEventListener("blur", cleanup);
      window.removeEventListener("keydown", onKey, true);
      if (handle.hasPointerCapture?.(pointer)) handle.releasePointerCapture(pointer);
      dragCleanup.current = null;
      if (mounted.current) setDrag(null);
    };
    const onCancel = (cancel: PointerEvent) => { if (cancel.pointerId === pointer) cleanup(); };
    const onKey = (key: KeyboardEvent) => { if (key.key === "Escape") { key.preventDefault(); key.stopPropagation(); cleanup(); } };
    const onUp = (release: PointerEvent) => {
      if (release.pointerId !== pointer) return;
      const target = targetAt(release.clientX, release.clientY);
      cleanup();
      if (armed && target?.allowed) void move(id, target.id, target.position);
    };
    dragCleanup.current = cleanup;
    window.addEventListener("pointermove", onMove, { capture: true, passive: false });
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("pointercancel", onCancel, true);
    window.addEventListener("blur", cleanup);
    window.addEventListener("keydown", onKey, true);
  }, [move]);

  const rename = async (terminal: TerminalState, name: string) => {
    const owner = latest.current.session;
    latest.current.onMutationStart?.();
    try {
      const next = await renameTerminal(terminal.history_id ? `pane:${terminal.history_id}` : terminal.name, name);
      if (!mounted.current || next.id !== latest.current.session.id) return false;
      if (latest.current.session === owner) latest.current.onChanged(next);
      if (selected === terminal.name) latest.current.onSelect(next.terminals.find((entry) => idOf(entry) === idOf(terminal))?.name ?? name);
      return true;
    } catch (error) { pushToast("error", (error as Error).message); return false; }
    finally { latest.current.onMutationEnd?.(); }
  };
  const dragged = drag ? session.terminals.find((terminal) => idOf(terminal) === drag.id) : null;

  return <div ref={frame} data-testid="workspace-terminal-grid" aria-busy={saving} className="relative h-full min-h-0 overflow-auto p-2">
    <div className="relative h-full" style={{
      minWidth: visibleMaximized ? undefined : `${columns * MIN_WIDTH + (columns - 1) * GAP}px`,
      minHeight: visibleMaximized ? "240px" : `${Math.max(1, layoutSpan(tree, "column")) * 200}px`,
    }}>
      {tiles.map((terminal, index) => {
        const id = idOf(terminal);
        return <div key={id} data-session-id={id} tabIndex={0}
          aria-label={`${terminal.name}. Drag to an edge to dock, or the center to swap. Alt+Arrow swaps with a neighbor.`}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget && !(event.target instanceof HTMLElement && event.target.closest("[data-ide-drag-handle]"))) return;
            if (!event.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            if (visibleMaximized) return;
            const box = layout.boxes[index];
            if (!box) return;
            const horizontal = event.key === "ArrowLeft" || event.key === "ArrowRight";
            const sign = event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1;
            const center = horizontal ? box.x + box.w / 2 : box.y + box.h / 2;
            const crossCenter = horizontal ? box.y + box.h / 2 : box.x + box.w / 2;
            const candidates = tiles.map((entry, at) => ({ entry, box: layout.boxes[at] })).filter((candidate) => candidate.box && idOf(candidate.entry) !== id)
              .map((candidate) => ({ ...candidate,
                distance: ((horizontal ? candidate.box!.x + candidate.box!.w / 2 : candidate.box!.y + candidate.box!.h / 2) - center) * sign,
                crossDistance: Math.abs((horizontal ? candidate.box!.y + candidate.box!.h / 2 : candidate.box!.x + candidate.box!.w / 2) - crossCenter),
                overlap: horizontal
                  ? Math.min(box.y + box.h, candidate.box!.y + candidate.box!.h) - Math.max(box.y, candidate.box!.y)
                  : Math.min(box.x + box.w, candidate.box!.x + candidate.box!.w) - Math.max(box.x, candidate.box!.x),
              }))
              .filter((candidate) => candidate.distance > 0.001 && candidate.overlap > 0.001).sort((a, b) => a.distance - b.distance || a.crossDistance - b.crossDistance);
            if (candidates[0]) void move(id, idOf(candidates[0].entry));
          }}
          style={paneStyle(visibleMaximized === id ? { x: 0, y: 0, w: 1, h: 1 } : layout.boxes[index]!)}
          className={cn("min-h-0 min-w-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            drag?.id === id && "opacity-50",
            visibleMaximized && visibleMaximized !== id && "hidden")}>
          <AgenticTerminal headerMode="compact" agent={terminal.agent}
            name={terminal.name} workspaceId={session.id} displayName={terminal.display_name}
            recap={terminal.recap} promptCount={terminal.prompts_sent} appearance={appearance ?? theme} fontSize={fontSize}
            focused={selected === terminal.name} onFocus={() => onSelect(terminal.name)}
            maximized={visibleMaximized === id} onToggleMaximize={() => setMaximized((current) => current === id ? null : id)}
            onArrangeStart={visibleMaximized || saving || disabled ? undefined : (event) => startDrag(id, event)} arranging={drag?.id === id}
            onClose={() => onClose(terminal)} onAttachError={(message) => pushToast("error", message)}
            onRename={(name) => rename(terminal, name)}
            restartToken={restarts[id] ?? 0} onRestart={() => setRestarts((current) => ({ ...current, [id]: (current[id] ?? 0) + 1 }))}
            splitDisabled={tiles.length >= maxPanes} onSplit={(direction) => onAdd(terminal.name, direction)} />
          {drag?.target?.id === id && <div aria-hidden="true" data-testid="dock-preview" data-position={drag.target.position}
            className={cn("pointer-events-none absolute z-20 flex items-center justify-center rounded-xl border-2 p-2", drag.target.allowed ? "border-ring/70 bg-accent/[0.15]" : "border-destructive bg-background/80",
              drag.target.position === "left" ? "inset-y-1 left-1 w-1/2" : drag.target.position === "right" ? "inset-y-1 right-1 w-1/2" : drag.target.position === "above" ? "inset-x-1 top-1 h-1/2" : drag.target.position === "below" ? "inset-x-1 bottom-1 h-1/2" : "inset-1")}>
            <span className="rounded-md bg-popover px-3 py-2 text-center text-xs font-medium text-popover-foreground shadow-lg">{drag.target.allowed ? DOCK_LABELS[drag.target.position] : `Maximum ${MAX_GRID_COLUMNS} columns × ${MAX_GRID_ROWS} rows`}</span>
          </div>}
        </div>;
      })}
    </div>
    {drag && dragged && <div aria-hidden="true" className="pointer-events-none fixed z-[100] flex items-center gap-2 rounded-lg border border-border bg-popover px-3 py-2 text-sm font-medium text-popover-foreground shadow-xl"
      style={{ left: drag.x + 14, top: drag.y + 14 }}>
      <AgentMark agent={dragged.agent} label={dragged.display_name} variant="plain" />{dragged.name}
    </div>}
    <span className="sr-only" role="status">{announcement}</span>
  </div>;
}
