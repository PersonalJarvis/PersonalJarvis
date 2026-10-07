import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AgenticTerminal } from "./AgenticTerminal";
import { AgentMark } from "./AgentMark";
import { ForkPaneDialog, type ForkMode, type ForkSource } from "./ForkPaneDialog";
import { MovePaneDialog, type MovePaneRequest } from "./MovePaneDialog";
import type { PaneSplitDirection } from "./WorkspaceTerminalHeader";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";
import { forkTerminal, moveTerminal, placeTerminal, renameTerminal, transferTerminal, type PaneMovePosition, type TransferPlacement } from "@/lib/agenticIdeApi";
import { useComputerChoices } from "@/hooks/useComputers";
import { useThemeValue } from "@/hooks/useTheme";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import type { PaneStyle } from "./terminalThemes";
import { cn } from "@/lib/utils";
import { PaneResizer } from "@/components/layout/PaneResizer";
import { treeLayout, treeLeaves, type LayoutNode, type PaneSeam } from "./treeLayout";
import { useTreeSizes } from "./useTreeSizes";
import { PANE_COMMAND_EVENT, neighborInDirection, type PaneCommandDetail } from "./ideHotkeys";
import { DOCK_LABELS, dockPosition, layoutSpan, paneStyle, previewDock, workspaceLayout } from "./workspaceDocking";

const GAP = 8;
const MIN_WIDTH = 280;
const idOf = (terminal: TerminalState) => terminal.history_id ?? terminal.key;

/*
 * A seam's grab area fills the whole gap between two panes, centred on the
 * boundary, and runs the full length of the boundary it divides.
 */
function seamStyle(seam: PaneSeam): React.CSSProperties {
  return seam.orientation === "vertical"
    ? { position: "absolute", left: `calc(${seam.x * 100}% - ${GAP / 2}px)`, top: `${seam.y * 100}%`, width: `${GAP}px`, height: `${seam.h * 100}%` }
    : { position: "absolute", top: `calc(${seam.y * 100}% - ${GAP / 2}px)`, left: `${seam.x * 100}%`, height: `${GAP}px`, width: `${seam.w * 100}%` };
}

function writeStyle(node: HTMLElement, style: React.CSSProperties) {
  for (const [property, value] of Object.entries(style)) {
    (node.style as unknown as Record<string, string>)[property] = String(value);
  }
}

interface Props {
  /** Hidden retained workspaces keep their sockets but park output and focus. */
  active?: boolean;
  session: SessionState;
  onChanged: (session: SessionState) => void;
  /** Open the agent picker; with an anchor, the new pane splits off that pane. */
  onAdd: (anchor?: string, direction?: PaneSplitDirection) => void;
  onClose: (terminal: TerminalState) => void;
  onSelect: (name: string) => void;
  selected: string;
  fontSize: number;
  appearance: "light" | "dark" | null;
  disabled?: boolean;
  onMutationStart?: () => void;
  onMutationEnd?: () => void;
  /**
   * How the panes are drawn: square multiplexer tiles with a slim title row,
   * or rounded cards. The reader picks it in Workspace options.
   */
  paneStyle?: PaneStyle;
  /** Every open workspace; a pane can be moved into any of the others. */
  workspaces?: readonly WorkspaceChoice[];
}

interface WorkspaceChoice { id: string; name: string; folder?: string }
interface DropTarget { id: string; position: PaneMovePosition }
interface DragFeedback { id: string; target: DropTarget | null; x: number; y: number; workspace?: WorkspaceChoice | null; blocked?: string | null }

/** The attribute a sidebar row carries when a dragged pane may be dropped on it. */
const WORKSPACE_DROP = "data-pane-drop-workspace";

/**
 * One folder, however the path is spelled. Panes only move between
 * workspaces on the same folder (the server refuses anything else); Windows
 * paths compare case-blind, like the filesystem under them.
 */
function sameFolder(left: string | undefined, right: string | undefined): boolean {
  if (!left || !right) return false;
  const clean = (path: string) => path.replace(/\\/g, "/").replace(/\/+$/, "");
  const [a, b] = [clean(left), clean(right)];
  return /^[a-z]:\//i.test(a) ? a.toLowerCase() === b.toLowerCase() : a === b;
}

export function WorkspaceTerminalGrid({ session, onChanged, onAdd, onClose, onSelect, selected, fontSize, appearance, disabled = false, onMutationStart, onMutationEnd, paneStyle: look = "classic", workspaces = [], active = true }: Props) {
  const theme = useThemeValue();
  const pushToast = useEventStore((state) => state.pushToast);
  // The pane an agent card in the side panel pointed at, framed in blue.
  const spotlight = useIdeSidePanelStore((state) => state.spotlight);
  const setSpotlight = useIdeSidePanelStore((state) => state.setSpotlight);
  // While the reader works in the side panel, the panel wears the blue frame
  // and the selected pane lets go of its own; a press on a pane takes it back.
  const panelInUse = useIdeSidePanelStore((state) => state.inUse);
  const setPanelInUse = useIdeSidePanelStore((state) => state.setInUse);
  const spotlitPane = spotlight?.workspaceId === session.id ? spotlight.pane : null;
  const frame = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<DragFeedback | null>(null);
  const [optimistic, setOptimistic] = useState<LayoutNode | null>(null);
  const [saving, setSaving] = useState(false);
  const [maximized, setMaximized] = useState<string | null>(null);
  const [restarts, setRestarts] = useState<Record<string, number>>({});
  const [announcement, setAnnouncement] = useState("");
  const [forking, setForking] = useState<ForkSource | null>(null);
  const [forkBusy, setForkBusy] = useState(false);
  const dragCleanup = useRef<(() => void) | null>(null);
  const saveInFlight = useRef(false);
  const mounted = useRef(true);
  const latest = useRef({ session, disabled, onChanged, onSelect, onMutationStart, onMutationEnd });
  latest.current = { session, disabled, onChanged, onSelect, onMutationStart, onMutationEnd };

  const members = session.terminals.map(idOf);
  const keys = session.terminals.map((terminal) => terminal.key);
  const pendingKeys = treeLeaves(optimistic);
  const pendingOrderMatches = optimistic && pendingKeys.length === keys.length && pendingKeys.every((key) => keys.includes(key));
  const tiles = session.terminals;
  const canvas = useRef<HTMLDivElement>(null);
  const paneNodes = useRef(new Map<string, HTMLElement>());
  const seamNodes = useRef(new Map<string, HTMLElement>());
  /*
   * A seam drag repaints the boxes directly, once per frame, instead of
   * re-rendering every live terminal per pointer move; state is written once
   * on release (see `useTreeSizes`).
   */
  const paintDragged = useCallback((next: LayoutNode) => {
    const terminals = latest.current.session.terminals;
    const live = treeLayout(next, terminals);
    terminals.forEach((terminal, index) => {
      const node = paneNodes.current.get(idOf(terminal));
      const box = live.boxes[index];
      if (node && box) writeStyle(node, paneStyle(box));
    });
    for (const seam of live.seams) {
      const node = seamNodes.current.get(seam.id);
      if (node) writeStyle(node, seamStyle(seam));
    }
  }, []);
  const sizes = useTreeSizes(
    workspaceLayout(session.layout, session.terminals),
    useCallback(() => ({ width: canvas.current?.clientWidth ?? 0, height: canvas.current?.clientHeight ?? 0 }), []),
    paintDragged,
  );
  const tree = pendingOrderMatches ? optimistic : sizes.tree;
  const layout = treeLayout(tree, tiles);
  const resizing = sizes.dragging !== null;
  const columns = Math.max(1, layoutSpan(tree, "row"));
  const visibleMaximized = maximized && members.includes(maximized) ? maximized : null;
  const minimal = look === "minimal";

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; dragCleanup.current?.(); };
  }, []);
  useEffect(() => {
    if (maximized && !members.includes(maximized)) setMaximized(null);
  }, [maximized, members]);
  // "Show me that terminal" from the office: the pane fills the grid, live. A
  // request for another workspace waits for that workspace's own grid.
  const paneRequest = useIdeChatStore((state) => state.paneRequest);
  const settlePaneMaximize = useIdeChatStore((state) => state.settlePaneMaximize);
  useLayoutEffect(() => {
    if (!active || !paneRequest?.maximize || paneRequest.workspaceId !== session.id) return;
    const terminal = session.terminals.find((entry) => entry.name === paneRequest.pane);
    if (!terminal) return;
    settlePaneMaximize(paneRequest.nonce);
    setMaximized(idOf(terminal));
  }, [active, paneRequest, session.id, session.terminals, settlePaneMaximize]);
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

  // "Move to <workspace>…", from the pane menu or by dropping the pane on a
  // workspace in the sidebar: a dialog asks where in that grid it goes, then
  // the pane leaves this grid and joins that tab with its agent still
  // running, and that tab's grid re-joins it.
  const [transferring, setTransferring] = useState<string | null>(null);
  const [moving, setMoving] = useState<{ id: string; request: MovePaneRequest } | null>(null);
  // Only the other workspaces on this one's folder: a pane never moves to
  // another project's folder.
  const movable = workspaces.filter((workspace) => workspace.id !== session.id && sameFolder(workspace.folder, session.folder));
  const movableRef = useRef(movable);
  movableRef.current = movable;
  const askWhere = useCallback((sourceId: string, target: WorkspaceChoice) => {
    const terminal = latest.current.session.terminals.find((entry) => idOf(entry) === sourceId);
    if (!terminal || !movableRef.current.some((entry) => entry.id === target.id)) return;
    setMoving({ id: sourceId, request: { pane: { name: terminal.name, agent: terminal.agent, displayName: terminal.display_name }, target } });
  }, []);
  const transfer = useCallback(async (sourceId: string, target: WorkspaceChoice, placement: TransferPlacement | null = null) => {
    const owner = latest.current.session;
    const terminal = owner.terminals.find((entry) => idOf(entry) === sourceId);
    if (!terminal || target.id === owner.id || saveInFlight.current || latest.current.disabled) return;
    saveInFlight.current = true;
    latest.current.onMutationStart?.();
    setTransferring(sourceId);
    try {
      const result = await transferTerminal(terminal.history_id ? `pane:${terminal.history_id}` : terminal.name, owner.id, target.id, placement);
      if (!mounted.current) return;
      setMoving(null);
      const next = result.state.session;
      if (next && latest.current.session === owner && next.id === owner.id) latest.current.onChanged(next);
      const renamed = result.terminal.name !== terminal.name ? ` as ${result.terminal.name}` : "";
      pushToast("success", `${terminal.name} moved to ${target.name}${renamed}. Its agent keeps running.`);
      setAnnouncement(`${terminal.name} moved to ${target.name}${renamed}.`);
    } catch (error) {
      // The dialog stays open, so another place can be picked.
      if (mounted.current) pushToast("error", (error as Error).message);
    } finally {
      saveInFlight.current = false;
      latest.current.onMutationEnd?.();
      if (mounted.current) setTransferring(null);
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
          if (!source || !target || targetId === id) return null;
          return { id: targetId!, position: dockPosition(x, y, rect) };
        }
      }
      return null;
    };
    // A workspace row in the sidebar under the pointer. It is lit straight on
    // the DOM: the sidebar is another component, and this is one attribute
    // for the length of a drag, not state it needs to own. A row on another
    // folder stays dark and is named as `blocked`, so the drag can say why.
    let litRow: HTMLElement | null = null;
    const workspaceAt = (x: number, y: number): { workspace: WorkspaceChoice | null; blocked: string | null } => {
      const row = document.elementFromPoint?.(x, y)?.closest<HTMLElement>(`[${WORKSPACE_DROP}]`) ?? null;
      const targetId = row?.getAttribute(WORKSPACE_DROP) ?? "";
      const workspace = targetId ? movableRef.current.find((entry) => entry.id === targetId) ?? null : null;
      const hit = workspace ? row : null;
      if (hit !== litRow) {
        litRow?.removeAttribute("data-pane-drop-active");
        hit?.setAttribute("data-pane-drop-active", "true");
        litRow = hit;
      }
      const blocked = row && targetId && !workspace && targetId !== latest.current.session.id
        ? row.getAttribute("data-pane-drop-name") || "that workspace" : null;
      return { workspace, blocked };
    };
    const onMove = (motion: PointerEvent) => {
      if (motion.pointerId !== pointer) return;
      if (!armed && Math.hypot(motion.clientX - initialX, motion.clientY - initialY) <= 5) return;
      armed = true;
      motion.preventDefault();
      const { workspace, blocked } = workspaceAt(motion.clientX, motion.clientY);
      setDrag({ id, target: workspace || blocked ? null : targetAt(motion.clientX, motion.clientY), workspace, blocked, x: motion.clientX, y: motion.clientY });
    };
    const cleanup = () => {
      litRow?.removeAttribute("data-pane-drop-active");
      litRow = null;
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
      const workspace = armed ? workspaceAt(release.clientX, release.clientY).workspace : null;
      const target = workspace ? null : targetAt(release.clientX, release.clientY);
      cleanup();
      if (armed && workspace) askWhere(id, workspace);
      else if (armed && target) void move(id, target.id, target.position);
    };
    dragCleanup.current = cleanup;
    window.addEventListener("pointermove", onMove, { capture: true, passive: false });
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("pointercancel", onCancel, true);
    window.addEventListener("blur", cleanup);
    window.addEventListener("keydown", onKey, true);
  }, [move, askWhere]);

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
  // A fork opens beside its source; the new pane becomes the selected one,
  // because it was opened to be worked with next.
  const fork = async ({ mode, branch }: { mode: ForkMode; branch: string }) => {
    if (!forking) return;
    const owner = latest.current.session;
    setForkBusy(true);
    latest.current.onMutationStart?.();
    try {
      const { session: next, terminal } = await forkTerminal(forking.name, {
        workspaceId: owner.id, worktree: mode === "worktree", branch: mode === "worktree" ? branch : undefined,
      });
      if (!mounted.current) return;
      setForking(null);
      if (next.id !== latest.current.session.id) return;
      latest.current.onChanged(next);
      latest.current.onSelect(terminal.name);
      setAnnouncement(terminal.branch ? `${forking.name} forked into ${terminal.name} on branch ${terminal.branch}.` : `${forking.name} forked into ${terminal.name}.`);
    } catch (error) { pushToast("error", (error as Error).message); }
    finally {
      latest.current.onMutationEnd?.();
      if (mounted.current) setForkBusy(false);
    }
  };
  // "Run on <computer>" / "Bring back": the pane's agent moves with its folder
  // and conversation; the pane reconnects to wherever it runs now.
  const computers = useComputerChoices();
  const [placing, setPlacing] = useState<string | null>(null);
  // A whole-workspace move (sidebar menu) restarted every pane in its new
  // place; reconnect each one so it shows the agent where it runs now.
  useEffect(() => {
    const onReconnect = (event: Event) => {
      const detail = (event as CustomEvent<{ workspaceId?: string }>).detail;
      if (detail?.workspaceId && detail.workspaceId !== latest.current.session.id) return;
      setRestarts((current) => Object.fromEntries(
        latest.current.session.terminals.map((terminal) => [idOf(terminal), (current[idOf(terminal)] ?? 0) + 1])));
    };
    window.addEventListener("jarvis:ide-panes-reconnect", onReconnect);
    return () => window.removeEventListener("jarvis:ide-panes-reconnect", onReconnect);
  }, []);
  const place = async (terminal: TerminalState, computerId: string | null) => {
    const id = idOf(terminal);
    if (placing) return;
    const target = computerId ? (computers.find((c) => c.id === computerId)?.name ?? "the computer") : "this computer";
    setPlacing(id);
    pushToast("info", `Moving ${terminal.name} to ${target}. The folder and the conversation go with it.`);
    latest.current.onMutationStart?.();
    try {
      const { session: next, message } = await placeTerminal(terminal.name, latest.current.session.id, computerId);
      if (!mounted.current) return;
      if (next && next.id === latest.current.session.id) latest.current.onChanged(next);
      setRestarts((current) => ({ ...current, [id]: (current[id] ?? 0) + 1 }));
      pushToast("success", `${terminal.name} now runs on ${target}. ${message}`.trim());
      setAnnouncement(`${terminal.name} now runs on ${target}.`);
    } catch (error) { pushToast("error", (error as Error).message); }
    finally {
      latest.current.onMutationEnd?.();
      if (mounted.current) setPlacing(null);
    }
  };
  const placementItems = (terminal: TerminalState) => {
    if (placing) return [];
    if (terminal.computer_id) return [{ label: "Bring back to this computer", run: () => void place(terminal, null) }];
    return computers.filter((computer) => computer.health.status !== "provisioning")
      .map((computer) => ({ label: `Run on ${computer.name}`, run: () => void place(terminal, computer.id) }));
  };
  const computerName = (terminal: TerminalState) => terminal.computer_id
    ? (computers.find((computer) => computer.id === terminal.computer_id)?.name ?? "another computer") : undefined;
  // Any other render mid-drag (a poll, a pane going live) would paint the
  // pre-drag sizes; re-apply the in-flight layout right after it.
  useLayoutEffect(() => {
    if (sizes.dragging === null) return;
    const inFlight = sizes.liveTree.current;
    if (inFlight) paintDragged(inFlight);
  });
  // The IDE's key menu (see ./ideHotkeys) acts on the selected pane of the
  // workspace on screen. The handler is rebuilt each render so it reads the
  // current layout; the listener itself is added once.
  const paneCommand = useRef<(detail: PaneCommandDetail) => void>(() => {});
  paneCommand.current = ({ pane, command }) => {
    const index = tiles.findIndex((terminal) => terminal.name === pane);
    const terminal = tiles[index];
    if (!terminal) return;
    const id = idOf(terminal);
    const focusPane = (target: TerminalState) => {
      onSelect(target.name);
      const typeInto = () => paneNodes.current.get(idOf(target))?.querySelector<HTMLElement>("textarea")?.focus();
      requestAnimationFrame(typeInto);
    };
    if (command.kind === "focus" || command.kind === "swap") {
      const neighbor = neighborInDirection(layout.boxes, index, command.direction);
      const target = neighbor === null ? undefined : tiles[neighbor];
      if (!target) { setAnnouncement(`No pane ${command.direction === "up" ? "above" : command.direction === "down" ? "below" : `to the ${command.direction}`} of ${terminal.name}.`); return; }
      if (command.kind === "swap") { void move(id, idOf(target)); return; }
      setMaximized(null);
      focusPane(target);
      return;
    }
    if (command.kind === "maximize") { setMaximized((current) => current === id ? null : id); focusPane(terminal); return; }
    if (command.kind === "fork") {
      if (terminal.accepts_prompts !== false) setForking({ name: terminal.name, agent: terminal.agent, displayName: terminal.display_name, workspaceId: session.id });
      return;
    }
    if (command.kind === "rename") void rename(terminal, command.name);
  };
  useEffect(() => {
    const onCommand = (event: Event) => {
      const detail = (event as CustomEvent<PaneCommandDetail>).detail;
      if (detail?.workspaceId === latest.current.session.id) paneCommand.current(detail);
    };
    window.addEventListener(PANE_COMMAND_EVENT, onCommand);
    return () => window.removeEventListener(PANE_COMMAND_EVENT, onCommand);
  }, []);
  const showSeams = !visibleMaximized && !drag && !saving && !disabled && !optimistic && tiles.length > 1;
  const dragged = drag ? session.terminals.find((terminal) => idOf(terminal) === drag.id) : null;

  // A pane picked from the side panel may sit off-screen on a narrow grid.
  useEffect(() => {
    if (!spotlitPane) return;
    const node = frame.current?.querySelector("[data-spotlit=\"true\"]");
    if (node instanceof HTMLElement && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    }
  }, [spotlitPane]);

  return <div ref={frame} data-testid="workspace-terminal-grid" aria-busy={saving} className="relative h-full min-h-0 overflow-auto p-2">
    <div ref={canvas} className={cn("relative h-full", resizing && "select-none")} style={{
      minWidth: visibleMaximized ? undefined : `${columns * MIN_WIDTH + (columns - 1) * GAP}px`,
      minHeight: visibleMaximized ? "240px" : `${Math.max(1, layoutSpan(tree, "column")) * 200}px`,
    }}>
      {tiles.map((terminal, index) => {
        const id = idOf(terminal);
        return <div key={id} ref={(node) => { if (node) paneNodes.current.set(id, node); else paneNodes.current.delete(id); }} data-session-id={id} data-spotlit={spotlitPane === terminal.name ? "true" : undefined} tabIndex={0}
          aria-label={`${terminal.name}. Drag to an edge to dock, or the center to swap. Alt+Arrow swaps with a neighbor.`}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget && !(event.target instanceof HTMLElement && event.target.closest("[data-ide-drag-handle]"))) return;
            if (!event.altKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            if (visibleMaximized) return;
            const direction = ({ ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" } as const)[event.key as "ArrowLeft"];
            const neighbor = neighborInDirection(layout.boxes, index, direction);
            if (neighbor !== null && tiles[neighbor]) void move(id, idOf(tiles[neighbor]));
          }}
          style={paneStyle(visibleMaximized === id ? { x: 0, y: 0, w: 1, h: 1 } : layout.boxes[index]!)}
          className={cn("min-h-0 min-w-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            minimal ? "rounded-none" : "rounded-2xl",
            drag?.id === id && "opacity-50",
            spotlitPane === terminal.name && "ring-2 ring-accent ring-offset-2 ring-offset-background",
            visibleMaximized && visibleMaximized !== id && "hidden")}>
          <AgenticTerminal active={active} headerMode={minimal ? "minimal" : "compact"} agent={terminal.agent}
            name={terminal.name} workspaceId={session.id} displayName={terminal.display_name}
            recap={terminal.recap} promptCount={terminal.prompts_sent} appearance={appearance ?? theme} fontSize={fontSize}
            focused={active && selected === terminal.name} markFocus={!panelInUse}
            onFocus={() => { if (spotlitPane && spotlitPane !== terminal.name) setSpotlight(null); setPanelInUse(false); onSelect(terminal.name); }}
            onSwapWithFocused={saving || disabled || selected === terminal.name || !tiles.some((entry) => entry.name === selected)
              ? undefined : () => {
                const target = tiles.find((entry) => entry.name === selected);
                if (target) void move(id, idOf(target));
              }}
            layoutBusy={resizing}
            maximized={visibleMaximized === id} onToggleMaximize={() => setMaximized((current) => current === id ? null : id)}
            onArrangeStart={visibleMaximized || saving || disabled ? undefined : (event) => startDrag(id, event)} arranging={drag?.id === id}
            onClose={() => onClose(terminal)} onAttachError={(message) => pushToast("error", message)}
            onRename={(name) => rename(terminal, name)}
            restartToken={restarts[id] ?? 0} onRestart={() => setRestarts((current) => ({ ...current, [id]: (current[id] ?? 0) + 1 }))}
            onSplit={(direction) => onAdd(terminal.name, direction)}
            branch={terminal.branch || undefined} folder={terminal.folder || undefined}
            computerName={computerName(terminal)} placementItems={placementItems(terminal)}
            workspaceItems={transferring || saving || disabled ? [] : movable
              .map((workspace) => ({ label: `Move to ${workspace.name}…`, run: () => askWhere(id, workspace) }))}
            onFork={terminal.accepts_prompts === false ? undefined : () => setForking({ name: terminal.name, agent: terminal.agent, displayName: terminal.display_name, workspaceId: session.id })} />
          {drag?.target?.id === id && <div aria-hidden="true" data-testid="dock-preview" data-position={drag.target.position}
            className={cn("pointer-events-none absolute z-20 flex items-center justify-center border-2 p-2", minimal ? "rounded-none" : "rounded-xl", "border-ring/70 bg-accent/[0.15]",
              drag.target.position === "left" ? "inset-y-1 left-1 w-1/2" : drag.target.position === "right" ? "inset-y-1 right-1 w-1/2" : drag.target.position === "above" ? "inset-x-1 top-1 h-1/2" : drag.target.position === "below" ? "inset-x-1 bottom-1 h-1/2" : "inset-1")}>
            <span className="rounded-md bg-popover px-3 py-2 text-center text-xs font-medium text-popover-foreground shadow-lg">{DOCK_LABELS[drag.target.position]}</span>
          </div>}
        </div>;
      })}
      {/*
        The invisible boundaries between panes: drag one to give the panes on either side
        more or less room, double-click it to even them out, or focus it and
        use the arrow keys. Only the panes it divides change size.
      */}
      {showSeams && layout.seams.map((seam) => (
        <PaneResizer key={seam.id}
          ref={(node) => { if (node) seamNodes.current.set(seam.id, node); else seamNodes.current.delete(seam.id); }}
          testId={`pane-seam-${seam.id}`} orientation={seam.orientation} title={seam.label}
          active={sizes.dragging === seam.id}
          // The gap itself is the grip: no drawn line or stub, only the
          // resize cursor on hover (maintainer, 2026-09-29).
          showLine={false}
          onPointerDown={(event) => { if (event.button === 0) sizes.startDrag(seam, event); }}
          onDoubleClick={() => sizes.even(seam)}
          // PaneResizer's vertical sign is written for a pane's own edge; here
          // an arrow key moves the seam the way it points.
          onNudge={(delta) => sizes.nudge(seam, seam.orientation === "horizontal" ? -delta : delta)}
          className="!z-30" style={seamStyle(seam)} />
      ))}
    </div>
    {drag && dragged && <div aria-hidden="true" className="pointer-events-none fixed z-[100] flex items-center gap-2 rounded-lg border border-border bg-popover px-3 py-2 text-sm font-medium text-popover-foreground shadow-xl"
      style={{ left: drag.x + 14, top: drag.y + 14 }}>
      <AgentMark agent={dragged.agent} label={dragged.display_name} variant="plain" />{dragged.name}
      {drag.workspace && <span className="font-normal text-muted-foreground">Move to {drag.workspace.name}</span>}
      {drag.blocked && <span className="font-normal text-destructive">Not into {drag.blocked}: another folder</span>}
    </div>}
    <span className="sr-only" role="status">{announcement}</span>
    <ForkPaneDialog source={forking} busy={forkBusy} onCancel={() => setForking(null)} onConfirm={(choice) => void fork(choice)} />
    <MovePaneDialog request={moving?.request ?? null} busy={transferring !== null} onCancel={() => setMoving(null)}
      onConfirm={(placement) => { if (moving) void transfer(moving.id, moving.request.target, placement); }} />
  </div>;
}
