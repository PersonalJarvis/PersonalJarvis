import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgenticTerminal } from "./AgenticTerminal";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";
import { reorderIdeTerminals } from "@/lib/agenticIdeApi";
import { useThemeValue } from "@/hooks/useTheme";
import { useEventStore } from "@/store/events";

export function columnsForSessions(count: number, width: number): number {
  if (count < 1) return 1;
  const readable = Math.max(1, Math.min(4, Math.floor(width / 320) || 1));
  const desired = count <= 3 ? count : count <= 4 ? 2 : count <= 6 ? 3 : 4;
  return Math.max(Math.ceil(count / 2), Math.min(readable, desired));
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
}

export function WorkspaceTerminalGrid({ session, onChanged, onAdd, onClose, onSelect, selected, fontSize, appearance }: Props) {
  const theme = useThemeValue();
  const pushToast = useEventStore((state) => state.pushToast);
  const frame = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1280);
  const [dragging, setDragging] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [maximized, setMaximized] = useState<string | null>(null);
  const dragCleanup = useRef<(() => void) | null>(null);
  const ordered = session.terminals;
  const columns = columnsForSessions(ordered.length, width);

  useEffect(() => {
    if (maximized && !ordered.some((terminal) => (terminal.history_id ?? terminal.key) === maximized)) setMaximized(null);
  }, [maximized, ordered]);

  useEffect(() => {
    const node = frame.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => () => dragCleanup.current?.(), []);

  const move = useCallback(async (sourceId: string, targetId: string) => {
    if (sourceId === targetId) return;
    const ids = ordered.map((terminal) => terminal.history_id ?? terminal.key);
    const from = ids.indexOf(sourceId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    try {
      const state = await reorderIdeTerminals(session.id, ids);
      if (state.session) onChanged(state.session);
    } catch (error) {
      pushToast("error", (error as Error).message);
    }
  }, [onChanged, ordered, pushToast, session.id]);

  const startDrag = useCallback((id: string, event: React.PointerEvent) => {
    if (event.button !== 0 || dragCleanup.current) return;
    const initialX = event.clientX;
    const initialY = event.clientY;
    let armed = false;
    const onMove = (motion: PointerEvent) => {
      if (!armed && Math.hypot(motion.clientX - initialX, motion.clientY - initialY) > 5) { armed = true; setDragging(id); }
      if (!armed) return;
      const tile = document.elementFromPoint(motion.clientX, motion.clientY)?.closest<HTMLElement>("[data-session-id]");
      setHover(tile?.dataset.sessionId ?? null);
    };
    const cleanup = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      dragCleanup.current = null;
      setDragging(null);
      setHover(null);
    };
    const onCancel = () => cleanup();
    const onUp = (release: PointerEvent) => {
      cleanup();
      const tile = document.elementFromPoint(release.clientX, release.clientY)?.closest<HTMLElement>("[data-session-id]");
      if (armed && tile?.dataset.sessionId) void move(id, tile.dataset.sessionId);
    };
    dragCleanup.current = cleanup;
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
  }, [move]);

  const tiles = useMemo(() => ordered.map((terminal) => ({ ...terminal, stableId: terminal.history_id ?? terminal.key })), [ordered]);
  return <div ref={frame} data-testid="workspace-terminal-grid" className="h-full min-h-0 overflow-auto p-2.5">
    <div className="grid h-full gap-2.5" style={{ gridTemplateColumns: `repeat(${maximized ? 1 : columns}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${maximized ? 1 : Math.ceil(tiles.length / columns)}, minmax(0, 1fr))`, minWidth: maximized ? undefined : `${columns * 320}px`, minHeight: "280px" }}>
      {tiles.map((terminal, index) => <div key={terminal.stableId} data-session-id={terminal.stableId}
        tabIndex={0} aria-label={`${terminal.name}, position ${index + 1} of ${tiles.length}. Press Alt+Arrow keys to reorder.`}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget && !(event.target instanceof HTMLElement && event.target.closest("[data-ide-drag-handle]"))) return;
          if (!event.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
          event.preventDefault();
          const delta = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : event.key === "ArrowUp" ? -columns : columns;
          const target = tiles[index + delta];
          if (target) void move(terminal.stableId, target.stableId);
        }}
        className={`relative min-h-0 min-w-0 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${dragging === terminal.stableId ? "opacity-60" : ""} ${hover === terminal.stableId && dragging !== terminal.stableId ? "ring-2 ring-primary" : ""} ${maximized && maximized !== terminal.stableId ? "hidden" : ""}`}>
        <AgenticTerminal name={terminal.name} workspaceId={session.id} displayName={terminal.display_name}
          recap={terminal.recap} promptCount={terminal.prompts_sent} appearance={appearance ?? theme} fontSize={fontSize}
          focused={selected === terminal.name} onFocus={() => onSelect(terminal.name)}
          maximized={maximized === terminal.stableId} onToggleMaximize={() => setMaximized((current) => current === terminal.stableId ? null : terminal.stableId)}
          onArrangeStart={maximized ? undefined : (event) => startDrag(terminal.stableId, event)}
          showArrangeHandle={!maximized}
          onClose={() => onClose(terminal)} onAttachError={(message) => pushToast("error", message)}
          splitDisabled={tiles.length >= 8} onSplit={() => onAdd()} />
      </div>)}
    </div>
  </div>;
}
