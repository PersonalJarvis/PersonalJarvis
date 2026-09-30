/**
 * A coding session on the coding floor, shown as the pane it is — in a window
 * over the map: the Agentic IDE's title bar (state dot, agent mark, the pane's
 * title, the pane actions as quiet icons) over the IDE's own live terminal.
 * Nothing else — no chips, no toolbar, no side notes, no second prompt box.
 *
 * The window opens bottom-right at about half the stage's width, moves by its
 * title bar, resizes by any edge or corner, and remembers where the reader
 * left it (./paneWindow). Its size is fixed while nothing is being dragged, so
 * the terminal inside never resizes the agent on its own.
 *
 * The terminal is the IDE pane's `AgenticTerminal` itself, attached as one more
 * viewer of the same PTY: the same renderer, font and colours, and typed into
 * directly, exactly like the pane in the IDE. It opens as the pane's size lead
 * in this window (../../agentic/paneSizeLead), so the same pane in the IDE grid
 * behind the office follows this window's size instead of trading it back and
 * forth. Colours come from the pane's own appearance tables (./terminalThemes),
 * so the window matches the IDE's panes in light and dark, including a light
 * pane in a dark app.
 */
import {
  useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
  type CSSProperties, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type RefObject,
} from "react";
import { Footprints, Hand, LocateFixed, Maximize2, Square, Trash2, X } from "lucide-react";
import { useT } from "@/i18n";
import { useThemeValue } from "@/hooks/useTheme";
import { closeTerminal, forkTerminal, interruptTerminal } from "@/lib/agenticIdeApi";
import { usePaneTitle } from "@/store/paneRecaps";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { AgentMark } from "@/components/agentic/AgentMark";
import { AgenticTerminal } from "@/components/agentic/AgenticTerminal";
import { FONT_DEFAULT } from "@/components/agentic/paneFont";
import { warmTerminalFont } from "@/lib/terminalFont";
import { BranchIcon } from "@/components/agentic/branchIcon";
import { PANE_BRAND, PANE_CHROME, PANE_SOLID, PANE_TILE, storedPaneStyle, storedTerminalAppearance, themeFor } from "@/components/agentic/terminalThemes";
import type { PaneOccupant } from "./codingFloor";
import { player, useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { CALL_MS } from "./AgentTalkPanel";
import {
  defaultRect, loadStoredWindow, moveRect, placeStored, resizeRect, saveStoredWindow, toStored,
  type ResizeEdge, type StageSize, type WindowRect,
} from "./paneWindow";
import "./paneCommand.css";

// This module loads with the office, well before a session is clicked: fetch
// the terminal's font now, so the first panel opens on a font that is already
// here instead of waiting for it (measured: ~0.4 s of the first open).
warmTerminalFont(FONT_DEFAULT);

/** A second press within this window confirms closing the session. */
const CONFIRM_MS = 4000;
/** How long a success line stays on the window; an error stays until the next action. */
const NOTE_MS = 4000;
const EDGES: readonly ResizeEdge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

function sinceLabel(at: number | null, t: (key: string) => string): string {
  if (!at) return "";
  const seconds = Math.max(0, Date.now() / 1000 - at);
  if (seconds < 60) return t("society.office.since_now");
  if (seconds < 3600) return t("society.office.since_minutes").replace("{0}", String(Math.floor(seconds / 60)));
  if (seconds < 86400) return t("society.office.since_hours").replace("{0}", String(Math.floor(seconds / 3600)));
  return t("society.office.since_days").replace("{0}", String(Math.floor(seconds / 86400)));
}

type Note = { tone: "ok" | "error"; text: string } | null;
type Gesture = {
  kind: "move" | "resize";
  edge: ResizeEdge;
  pointerId: number;
  startX: number;
  startY: number;
  start: WindowRect;
};

/** The stage the window floats over (its offset parent), measured before paint and on every resize. */
function useStageSize(panel: RefObject<HTMLElement | null>): StageSize | null {
  const [stage, setStage] = useState<StageSize | null>(null);
  useLayoutEffect(() => {
    const host = panel.current?.parentElement;
    if (!host) return;
    const measure = () => {
      const width = host.clientWidth, height = host.clientHeight;
      setStage((prev) => (prev && prev.width === width && prev.height === height ? prev : { width, height }));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => observer.disconnect();
  }, [panel]);
  return stage;
}

export function PaneCommandPanel({ occupant, compact = false, onOpen, onClose }: {
  occupant: PaneOccupant;
  /** The office inside the IDE's side panel: the window opens clear of the HUD buttons along the bottom. */
  compact?: boolean;
  onOpen: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const headingId = useId();
  const { agent, pane } = occupant;
  const panel = useRef<HTMLElement>(null);
  const [note, setNote] = useState<Note>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const appTheme = useThemeValue();
  // The IDE's pane appearance: the reader's stored choice, else the app's theme.
  const appearance = storedTerminalAppearance() ?? appTheme;
  // The IDE's pane style (Workspace options): the window wears the same tile
  // as the panes in the grid — square, slim title row, blue while in use.
  const paneStyle = storedPaneStyle();
  const brand = PANE_BRAND[appearance];
  const chrome = PANE_CHROME[appearance];
  const ansi = themeFor(appearance);
  const paneTitle = usePaneTitle(pane.workspace_id, pane.name);
  const where = agentPositions.get(agent.agentId);
  const live = pane.status === "live";
  const working = occupant.dot === "working";
  const since = sinceLabel(pane.activity_since || null, t);
  const title = paneTitle || pane.recap.trim() || agent.name;

  const stage = useStageSize(panel);
  const [stored, setStored] = useState(() => loadStoredWindow(compact));
  // The window mid-drag. Held apart from `stored` so a drag costs one small
  // render per frame and is written to storage once, when it ends.
  const [dragRect, setDragRect] = useState<WindowRect | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const frame = useRef<number | null>(null);
  const [resizing, setResizing] = useState(false);
  const rest = stage ? (stored ? placeStored(stored, stage) : defaultRect(stage, compact)) : null;
  const rect = dragRect ?? rest;

  useEffect(() => { panel.current?.focus({ preventScroll: true }); setNote(null); setConfirmClose(false); }, [agent.agentId]);
  useEffect(() => {
    if (!confirmClose) return;
    const timer = setTimeout(() => setConfirmClose(false), CONFIRM_MS);
    return () => clearTimeout(timer);
  }, [confirmClose]);
  useEffect(() => {
    if (note?.tone !== "ok") return;
    const timer = setTimeout(() => setNote(null), NOTE_MS);
    return () => clearTimeout(timer);
  }, [note]);
  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current); }, []);

  const refresh = () => { void useWorkspacePanesStore.getState().load(); };

  /** Run one session action; it answers with the line the window shows once it is done. */
  const run = async (action: () => Promise<string>) => {
    setNote(null);
    try {
      setNote({ tone: "ok", text: await action() });
      refresh();
    } catch (err) {
      setNote({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    }
  };

  const stop = () => void run(async () => {
    await interruptTerminal(pane.name, pane.workspace_id);
    return t("society.office.cmd_stopped").replace("{0}", agent.name);
  });
  const fork = () => void run(async () => {
    const { terminal } = await forkTerminal(pane.name, { workspaceId: pane.workspace_id, worktree: false });
    return t("society.office.cmd_forked").replace("{0}", terminal.name);
  });
  const close = () => {
    if (!confirmClose) { setConfirmClose(true); return; }
    setConfirmClose(false);
    void run(async () => {
      await closeTerminal(pane.name, pane.workspace_id);
      return t("society.office.cmd_closed").replace("{0}", agent.name);
    });
  };

  /* ---- moving and resizing ------------------------------------------------ */

  const begin = (event: ReactPointerEvent<HTMLElement>, kind: Gesture["kind"], edge: ResizeEdge = "se") => {
    if (event.button !== 0 || !rest) return;
    // The title bar's buttons stay buttons.
    if (kind === "move" && (event.target as HTMLElement).closest("button")) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    gesture.current = { kind, edge, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, start: rest };
    setDragRect(rest);
    if (kind === "resize") setResizing(true);
  };

  const follow = (event: ReactPointerEvent<HTMLElement>) => {
    const g = gesture.current;
    if (!g || g.pointerId !== event.pointerId || !stage) return;
    const dx = event.clientX - g.startX, dy = event.clientY - g.startY;
    const next = g.kind === "move" ? moveRect(g.start, dx, dy, stage) : resizeRect(g.start, g.edge, dx, dy, stage);
    // At most one render per frame, however fast the pointer reports.
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => { frame.current = null; setDragRect(next); });
  };

  const finish = (event: ReactPointerEvent<HTMLElement>) => {
    const g = gesture.current;
    if (!g || g.pointerId !== event.pointerId) return;
    gesture.current = null;
    if (frame.current !== null) { cancelAnimationFrame(frame.current); frame.current = null; }
    if (stage) {
      const dx = event.clientX - g.startX, dy = event.clientY - g.startY;
      const end = g.kind === "move" ? moveRect(g.start, dx, dy, stage) : resizeRect(g.start, g.edge, dx, dy, stage);
      // A press without a real drag moves nothing and remembers nothing.
      if (end.x !== g.start.x || end.y !== g.start.y || end.w !== g.start.w || end.h !== g.start.h) {
        const next = toStored(end, stage);
        setStored(next);
        saveStoredWindow(compact, next);
      }
    }
    setDragRect(null);
    setResizing(false);
  };

  /**
   * The pointer was taken away mid-drag (the OS, a touch turned into a scroll,
   * capture lost). Its last coordinates are not a place anyone chose — a
   * cancelled pointer often reports (0, 0) — so the window goes back to where
   * the drag began and nothing is remembered. After a normal release the
   * gesture is already over and this does nothing.
   */
  const abandon = (event: ReactPointerEvent<HTMLElement>) => {
    const g = gesture.current;
    if (!g || g.pointerId !== event.pointerId) return;
    gesture.current = null;
    if (frame.current !== null) { cancelAnimationFrame(frame.current); frame.current = null; }
    setDragRect(null);
    setResizing(false);
  };

  const gestureHandlers = (kind: Gesture["kind"], edge?: ResizeEdge) => ({
    onPointerDown: (event: ReactPointerEvent<HTMLElement>) => begin(event, kind, edge),
    onPointerMove: follow,
    onPointerUp: finish,
    onPointerCancel: abandon,
    onLostPointerCapture: abandon,
  });

  /** Double-click on the title bar: back to where the window first opened. */
  const resetPlace = (event: ReactMouseEvent) => {
    if ((event.target as HTMLElement).closest("button")) return;
    setStored(null);
    saveStoredWindow(compact, null);
  };

  /* ---- the terminal ------------------------------------------------------- */

  // Built once per pane and size state, so the window re-rendering while it is
  // carried around never re-renders the terminal inside it. Mounted only once
  // the stage has a size: the first size the agent hears is the window's, and
  // a window with no room cannot hold the pane's size lead against the grid.
  const ready = stage !== null && stage.width > 0 && stage.height > 0;
  const terminal = useMemo(() => ready ? (
    // Keyed by the pane, so another session is a fresh socket, never this one's screen under a new name.
    <AgenticTerminal key={`${pane.workspace_id}/${pane.name}`} headerMode="none" sizeLead
      name={pane.name} workspaceId={pane.workspace_id} agent={pane.agent}
      displayName={pane.display_name || pane.agent || agent.name}
      appearance={appearance} fontSize={FONT_DEFAULT} layoutBusy={resizing}
      onAttachError={(message) => setNote({ tone: "error", text: message })} />
  ) : null, [ready, pane.workspace_id, pane.name, pane.agent, pane.display_name, agent.name, appearance, resizing]);

  const dot = occupant.dot === "working" ? ansi.green
    : occupant.dot === "waiting" ? ansi.yellow
      : occupant.dot === "error" ? ansi.red : brand.inkFaint;
  const edge = occupant.dot === "error" ? chrome.edge.error : live ? chrome.edge.live : chrome.edge.exited;
  const vars = {
    "--pane-ink": brand.ink,
    "--pane-ink-muted": brand.inkMuted,
    "--pane-ink-faint": brand.inkFaint,
    "--pane-chip": brand.chip,
    "--pane-rule": chrome.border,
    "--pane-edge": edge,
    "--pane-ground": PANE_SOLID[appearance],
    "--pane-float": chrome.float,
    "--pane-ok": ansi.green,
    "--pane-fault": ansi.red,
    "--pane-focus": PANE_TILE[appearance].focus,
  } as CSSProperties;
  const style: CSSProperties = rect
    ? { ...vars, left: rect.x, top: rect.y, width: rect.w, height: rect.h }
    // Not placed until the stage is measured, which happens before the first paint.
    : { ...vars, visibility: "hidden" };

  return (
    <aside ref={panel} className="office-pane" data-office-ui data-appearance={appearance} data-style={paneStyle}
      data-gesture={dragRect ? (resizing ? "resize" : "move") : undefined}
      style={style} aria-labelledby={headingId} tabIndex={-1}>
      <header className="office-pane-head" title={t("society.office.pane_window_hint")}
        {...gestureHandlers("move")} onDoubleClick={resetPlace}>
        <span className="office-pane-dot" style={{ background: dot }} role="img"
          aria-label={t(`society.office.pane_state_${occupant.stateKey}`)} />
        <AgentMark agent={pane.agent} label={pane.display_name || pane.agent} variant="plain" size="sm"
          className="!text-[color:var(--pane-ink)] [&>.bg-foreground]:!bg-[color:var(--pane-ink)]" />
        <h2 id={headingId} title={`${title} (${pane.name})`} className={paneStyle === "minimal" ? "font-mono" : undefined}>{title}</h2>
        <span className="office-pane-meta">
          {t(`society.office.pane_state_${occupant.stateKey}`)}{since ? ` · ${since}` : ""}
        </span>
        <div className="office-pane-actions" role="toolbar" aria-label={t("society.office.talk_tools")}>
          {working && live && (
            <button type="button" onClick={stop} title={t("society.office.cmd_stop")} aria-label={t("society.office.cmd_stop")}>
              <Square aria-hidden />
            </button>
          )}
          <button type="button" disabled={!live} onClick={fork} title={t("society.office.cmd_fork")} aria-label={t("society.office.cmd_fork")}>
            <BranchIcon aria-hidden />
          </button>
          <button type="button" disabled={!where} onClick={() => where && useOfficeStore.getState().requestWalk(where)}
            title={t("society.office.action_walk")} aria-label={t("society.office.action_walk")}>
            <Footprints aria-hidden />
          </button>
          <button type="button" onClick={() => useOfficeStore.getState().summon([agent.agentId], { x: player.x, z: player.z }, CALL_MS)}
            title={t("society.office.action_call")} aria-label={t("society.office.action_call")}>
            <Hand aria-hidden />
          </button>
          <button type="button" disabled={!where} onClick={() => where && useOfficeStore.getState().focusOn(where)}
            title={t("society.office.action_focus")} aria-label={t("society.office.action_focus")}>
            <LocateFixed aria-hidden />
          </button>
          <button type="button" onClick={onOpen} title={t("society.office.pane_open")} aria-label={t("society.office.pane_open")}>
            <Maximize2 aria-hidden />
          </button>
          <button type="button" data-danger aria-pressed={confirmClose} onClick={close}
            title={t(confirmClose ? "society.office.cmd_close_confirm" : "society.office.cmd_close")}
            aria-label={t(confirmClose ? "society.office.cmd_close_confirm" : "society.office.cmd_close")}>
            <Trash2 aria-hidden />
          </button>
          <span className="office-pane-sep" aria-hidden />
          <button type="button" onClick={onClose} title={t("society.office.close")} aria-label={t("society.office.close")}>
            <X aria-hidden />
          </button>
        </div>
      </header>

      <div className="office-pane-term">{terminal}</div>
      {/* Over the terminal, never below it: a line that took rows away would resize the agent. */}
      {note && <p className="office-pane-status" role="status" data-tone={note.tone} onClick={() => setNote(null)}>{note.text}</p>}
      {EDGES.map((grip) => (
        <div key={grip} className="office-pane-grip" data-edge={grip} aria-hidden {...gestureHandlers("resize", grip)} />
      ))}
    </aside>
  );
}
