import { useCallback, useEffect, useRef, useState } from "react";
import { AgenticTerminal } from "./AgenticTerminal";
import { AgentMark } from "./AgentMark";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";
import { renameTerminal, reorderIdeTerminals } from "@/lib/agenticIdeApi";
import { useThemeValue } from "@/hooks/useTheme";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";

const GAP = 8;
const MIN_WIDTH = 320;
const idOf = (terminal: TerminalState) => terminal.history_id ?? terminal.key;

export function columnsForSessions(count: number, width: number, preference = 0): number {
  if (count < 1) return 1;
  const minimum = Math.ceil(count / 2);
  if (preference > 0) return Math.max(minimum, Math.min(count, 4, preference));
  const readable = Math.max(1, Math.min(4, Math.floor((width + GAP) / 380)));
  // Four wide panes in two rows left huge horizontal voids on large monitors.
  // Fit four beside each other when readable; six keep the reference's 3 x 2.
  const desired = count === 4 ? (readable >= 4 ? 4 : 2) : count <= 3 ? count : minimum;
  return Math.max(minimum, Math.min(readable, desired));
}

export function swapSessions(ids: string[], source: string, target: string): string[] {
  const from = ids.indexOf(source), to = ids.indexOf(target);
  if (from < 0 || to < 0 || from === to) return ids;
  const next = [...ids];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

interface Props {
  session: SessionState;
  onChanged: (session: SessionState) => void;
  onAdd: () => void;
  onClose: (terminal: TerminalState) => void;
  onSelect: (name: string) => void;
  selected: string;
  fontSize: number;
  appearance: "light" | "dark" | null;
  columnPreference?: number;
  onMutationStart?: () => void;
  onMutationEnd?: () => void;
}

interface DragFeedback { id: string; target: string | null; x: number; y: number }

export function WorkspaceTerminalGrid({ session, onChanged, onAdd, onClose, onSelect, selected, fontSize, appearance, columnPreference = 0, onMutationStart, onMutationEnd }: Props) {
  const theme = useThemeValue();
  const pushToast = useEventStore((state) => state.pushToast);
  const frame = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1280);
  const [drag, setDrag] = useState<DragFeedback | null>(null);
  const [optimistic, setOptimistic] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [maximized, setMaximized] = useState<string | null>(null);
  const [restarts, setRestarts] = useState<Record<string, number>>({});
  const [announcement, setAnnouncement] = useState("");
  const dragCleanup = useRef<(() => void) | null>(null);
  const saveInFlight = useRef(false);
  const mounted = useRef(true);
  const latest = useRef({ session, onChanged, onSelect, onMutationStart, onMutationEnd });
  latest.current = { session, onChanged, onSelect, onMutationStart, onMutationEnd };

  const members = session.terminals.map(idOf);
  const pendingOrderMatches = optimistic && optimistic.length === members.length && optimistic.every((id) => members.includes(id));
  const ids = pendingOrderMatches ? optimistic : members;
  const tiles = ids.map((id) => session.terminals.find((terminal) => idOf(terminal) === id)!);
  const columns = columnsForSessions(tiles.length, width, columnPreference);
  const visibleMaximized = maximized && members.includes(maximized) ? maximized : null;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; dragCleanup.current?.(); };
  }, []);
  useEffect(() => {
    if (maximized && !members.includes(maximized)) setMaximized(null);
  }, [maximized, members]);
  useEffect(() => {
    const node = frame.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(entry.contentRect.width);
      else dragCleanup.current?.();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const move = useCallback(async (sourceId: string, targetId: string) => {
    if (saveInFlight.current || sourceId === targetId) return;
    const owner = latest.current.session;
    const before = owner.terminals.map(idOf);
    const next = swapSessions(before, sourceId, targetId);
    if (next === before) return;
    saveInFlight.current = true;
    latest.current.onMutationStart?.();
    setSaving(true);
    setOptimistic(next);
    const source = owner.terminals.find((terminal) => idOf(terminal) === sourceId)!;
    const target = owner.terminals.find((terminal) => idOf(terminal) === targetId)!;
    try {
      const state = await reorderIdeTerminals(owner.id, next);
      if (!mounted.current || latest.current.session.id !== owner.id) return;
      // A rename/close or a newer snapshot must never be overwritten by the
      // response to an earlier drag. The parent also invalidates pending polls.
      if (latest.current.session === owner && state.session?.id === owner.id) latest.current.onChanged(state.session);
      setAnnouncement(`${source.name} and ${target.name} swapped.`);
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
    if (event.button !== 0 || dragCleanup.current || saveInFlight.current) return;
    const handle = event.currentTarget;
    const pointer = event.pointerId;
    const initialX = event.clientX, initialY = event.clientY;
    let armed = false;
    // Capture keeps xterm's selection/pointer handlers from eating a release.
    handle.setPointerCapture?.(pointer);
    const targetAt = (x: number, y: number) => {
      const candidates = frame.current?.querySelectorAll<HTMLElement>("[data-session-id]") ?? [];
      for (const tile of candidates) {
        const rect = tile.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) return tile.dataset.sessionId ?? null;
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
      if (armed && target && target !== id) void move(id, target);
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
    <div className="grid h-full gap-2" style={{
      gridTemplateColumns: `repeat(${visibleMaximized ? 1 : columns}, minmax(0, 1fr))`,
      gridTemplateRows: `repeat(${visibleMaximized ? 1 : Math.max(1, Math.ceil(tiles.length / columns))}, minmax(0, 1fr))`,
      minWidth: visibleMaximized ? undefined : `${columns * MIN_WIDTH + (columns - 1) * GAP}px`, minHeight: "280px",
    }}>
      {tiles.map((terminal, index) => {
        const id = idOf(terminal);
        return <div key={id} data-session-id={id} tabIndex={0}
          aria-label={`${terminal.name}, position ${index + 1} of ${tiles.length}. Alt+Arrow swaps with a neighbor.`}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget && !(event.target instanceof HTMLElement && event.target.closest("[data-ide-drag-handle]"))) return;
            if (!event.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            if (visibleMaximized) return;
            const delta = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : event.key === "ArrowUp" ? -columns : columns;
            const target = tiles[index + delta];
            if (target) void move(id, idOf(target));
          }}
          className={cn("relative min-h-0 min-w-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            drag?.id === id && "opacity-50", drag?.target === id && drag.id !== id && "ring-2 ring-ring",
            visibleMaximized && visibleMaximized !== id && "hidden")}>
          <AgenticTerminal headerMode="compact" agent={terminal.agent}
            name={terminal.name} workspaceId={session.id} displayName={terminal.display_name}
            recap={terminal.recap} promptCount={terminal.prompts_sent} appearance={appearance ?? theme} fontSize={fontSize}
            focused={selected === terminal.name} onFocus={() => onSelect(terminal.name)}
            maximized={visibleMaximized === id} onToggleMaximize={() => setMaximized((current) => current === id ? null : id)}
            onArrangeStart={visibleMaximized || saving ? undefined : (event) => startDrag(id, event)} arranging={drag?.id === id}
            onClose={() => onClose(terminal)} onAttachError={(message) => pushToast("error", message)}
            onRename={(name) => rename(terminal, name)}
            restartToken={restarts[id] ?? 0} onRestart={() => setRestarts((current) => ({ ...current, [id]: (current[id] ?? 0) + 1 }))}
            splitDisabled={tiles.length >= 8} onSplit={() => onAdd()} />
          {drag?.target === id && drag.id !== id && <span aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-11 mx-auto w-fit rounded-md bg-popover px-3 py-1.5 text-xs font-medium text-popover-foreground shadow-lg">Release to swap</span>}
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
