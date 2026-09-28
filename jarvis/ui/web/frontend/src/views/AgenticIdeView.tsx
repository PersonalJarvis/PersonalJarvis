import { useCallback, useEffect, useRef, useState } from "react";
import { FolderPlus, Loader2, X } from "lucide-react";
import { FolderPicker } from "@/components/agentic/FolderPicker";
import { VoiceBubble, storedVoiceBubbleOpen, storeVoiceBubbleOpen } from "@/components/agentic/VoiceBubble";
import { WorkspaceTerminalGrid } from "@/components/agentic/WorkspaceTerminalGrid";
import { WorkspaceAgentSetup } from "@/components/agentic/WorkspaceAgentSetup";
import { WorkspaceOptionsDialog } from "@/components/agentic/WorkspaceOptionsDialog";
import { IdeSidePanelFrame } from "@/components/agentic/sidePanel/IdeSidePanel";
import { fitsWorkspace, isBalancedWorkspace, canSplitFit } from "@/components/agentic/workspaceDocking";
import { AgentMark } from "@/components/agentic/AgentMark";
import { SplitRightIcon, SplitBelowIcon, SplitLeftIcon, SplitAboveIcon } from "@/components/agentic/splitIcons";
import type { PaneSplitDirection } from "@/components/agentic/WorkspaceTerminalHeader";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { openProject } from "@/lib/chatLibraryApi";
import {
  activateWorkspace, addTerminal, closeTerminal, closeWorkspace, fetchIdeAgents, fetchIdeProjects, fetchIdeState, renameWorkspace,
  reorderIdeTerminals, restoreIdeWorkspace, startIdeSession, syncAgenticIdeSurface,
  type AgentStatus, type IdeProject, type IdeState, type TerminalState,
} from "@/lib/agenticIdeApi";

const FONT_KEY = "jarvis.agenticIde.terminalFontSize";
const APPEARANCE_KEY = "jarvis.agenticIde.terminalAppearance";
const SPLIT_DIRECTION_KEY = "jarvis.agenticIde.splitDirection";

const SPLIT_DIRECTIONS: { id: PaneSplitDirection; label: string; hint: string; Icon: typeof SplitRightIcon }[] = [
  { id: "right", label: "Right", hint: "Open the new agent to the right of the pane", Icon: SplitRightIcon },
  { id: "down", label: "Down", hint: "Open the new agent below the pane", Icon: SplitBelowIcon },
  { id: "left", label: "Left", hint: "Open the new agent to the left of the pane", Icon: SplitLeftIcon },
  { id: "above", label: "Up", hint: "Open the new agent above the pane", Icon: SplitAboveIcon },
];

const isSplitDirection = (value: unknown): value is PaneSplitDirection =>
  SPLIT_DIRECTIONS.some((item) => item.id === value);

/** The direction last picked in the dialog; storage may be blocked, so never throw. */
function storedSplitDirection(): PaneSplitDirection {
  try {
    const stored = localStorage.getItem(SPLIT_DIRECTION_KEY);
    return isSplitDirection(stored) ? stored : "right";
  } catch { return "right"; }
}
function storeSplitDirection(direction: PaneSplitDirection): void {
  try { localStorage.setItem(SPLIT_DIRECTION_KEY, direction); } catch { /* a convenience only */ }
}

export interface AgenticIdeViewProps { onScreen?: boolean }

/** Project and workspace navigation is shared with Sidebar; PTY tiles stay mounted by stable session ID. */
export function AgenticIdeView({ onScreen = true }: AgenticIdeViewProps) {
  const pushToast = useEventStore((state) => state.pushToast);
  const action = useIdeProjectsStore((state) => state.action);
  const publishProjects = useIdeProjectsStore((state) => state.publish);
  const refreshRequest = useIdeProjectsStore((state) => state.refreshRequest);
  const setWorkspace = useIdeChatStore((state) => state.setWorkspace);
  const setWorkspaces = useIdeChatStore((state) => state.setWorkspaces);
  const paneRequest = useIdeChatStore((state) => state.paneRequest);
  const setStagedPane = useIdeChatStore((state) => state.setStagedPane);
  const [state, setState] = useState<IdeState | null>(null);
  const [projects, setProjects] = useState<IdeProject[]>([]);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [projectDialog, setProjectDialog] = useState(false);
  const [projectPath, setProjectPath] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("");
  const [workspaceProject, setWorkspaceProject] = useState<IdeProject | null>(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceAgents, setWorkspaceAgents] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [agentPicker, setAgentPicker] = useState<{ id: string; name: string } | null>(null);
  // Where the next agent opens: split off `splitAnchor` (a pane call-sign) in
  // `splitDirection`, or — with no anchor — the automatic even grid.
  const [splitDirection, setSplitDirection] = useState<PaneSplitDirection>(storedSplitDirection);
  const [splitAnchor, setSplitAnchor] = useState("");
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(storedVoiceBubbleOpen);
  const [fontSize, setFontSize] = useState(() => Number(localStorage.getItem(FONT_KEY)) || 13);
  const [appearance, setAppearance] = useState<"light" | "dark" | null>(() => {
    const stored = localStorage.getItem(APPEARANCE_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  });
  const handledAction = useRef(0);
  const handledPaneRequest = useRef(0);
  const refreshEpoch = useRef(0);
  const activationRunning = useRef(false);
  const pendingActivation = useRef<string | null>(null);
  const gridMutations = useRef(0);
  const normalizingLayout = useRef(false);
  const session = state?.session ?? null;
  const installed = agents.filter((agent) => agent.installed && agent.kind !== "shell" && agent.accepts_prompts !== false);
  const dialogOpen = projectDialog || workspaceProject !== null || renameOpen || agentPicker !== null;

  useEffect(() => {
    setOptionsOpen(false);
  }, [session?.id]);

  useEffect(() => {
    if (!dialogOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = document.querySelector<HTMLElement>("[data-ide-dialog]");
    if (!dialog) return;
    const focusable = () => [...dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])')];
    (focusable()[0] ?? dialog).focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (workspaceProject && busy) { event.stopPropagation(); return; }
        setProjectDialog(false); setWorkspaceProject(null); setRenameOpen(false); setAgentPicker(null);
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) { event.preventDefault(); dialog.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => { document.removeEventListener("keydown", onKeyDown, true); previous?.focus(); };
  }, [dialogOpen, projectDialog, workspaceProject, renameOpen, agentPicker, busy]);

  const refresh = useCallback(async (allowDuringActivation = false) => {
    if (normalizingLayout.current || ((activationRunning.current || gridMutations.current > 0) && !allowDuringActivation)) return;
    const epoch = ++refreshEpoch.current;
    let [nextState, listing] = await Promise.all([fetchIdeState(), fetchIdeProjects()]);
    // A workspace switch can fall between the two reads. Never publish a tree
    // whose active row disagrees with the grid it is paired with.
    if (nextState.active_id !== listing.active_workspace_id) {
      [nextState, listing] = await Promise.all([fetchIdeState(), fetchIdeProjects()]);
    }
    if (epoch !== refreshEpoch.current || pendingActivation.current || ((activationRunning.current || gridMutations.current > 0) && !allowDuringActivation) || nextState.active_id !== listing.active_workspace_id) return;
    const incoming = nextState.session;
    if (incoming?.layout && incoming.terminals.length <= 8 && !fitsWorkspace(incoming.layout)) {
      // Older snapshots and terminals opened outside this view may exceed the
      // grid bounds. Persist the same balanced fallback the grid displays.
      normalizingLayout.current = true;
      try {
        nextState = await reorderIdeTerminals(incoming.id, incoming.terminals.map((terminal) => terminal.history_id ?? terminal.key));
        listing = await fetchIdeProjects();
      } finally { normalizingLayout.current = false; }
      if (epoch !== refreshEpoch.current || pendingActivation.current || nextState.active_id !== listing.active_workspace_id) return;
    }
    setState(nextState);
    setProjects(listing.projects);
    publishProjects(listing.projects, listing.active_workspace_id);
  }, [publishProjects]);

  useEffect(() => {
    if (refreshRequest) void refresh().catch((error) => pushToast("error", (error as Error).message));
  }, [refreshRequest, refresh, pushToast]);

  const beginGridMutation = useCallback(() => {
    gridMutations.current += 1;
    ++refreshEpoch.current;
  }, []);
  const endGridMutation = useCallback(() => {
    gridMutations.current = Math.max(0, gridMutations.current - 1);
    ++refreshEpoch.current;
    if (gridMutations.current === 0) void refresh().catch((error) => pushToast("error", (error as Error).message));
  }, [refresh, pushToast]);

  useEffect(() => {
    void refresh().catch((error) => pushToast("error", (error as Error).message));
    void fetchIdeAgents(true).then((response) => setAgents(response.agents)).catch((error) => pushToast("error", (error as Error).message));
  }, [refresh, pushToast]);

  useEffect(() => {
    if (!onScreen) return;
    void refresh().catch((error) => console.warn("Agentic IDE state refresh failed:", error));
    const timer = window.setInterval(() => {
      void refresh().catch((error) => console.warn("Agentic IDE state refresh failed:", error));
    }, 4000);
    return () => window.clearInterval(timer);
  }, [onScreen, refresh]);

  useEffect(() => {
    setWorkspace(session ? { id: session.id, name: session.name ?? session.project.name, path: session.folder } : null);
    setWorkspaces((state?.workspaces ?? []).map((workspace) => ({ id: workspace.id, name: workspace.name, folder: workspace.folder, active: workspace.active })));
    if (session && !session.terminals.some((terminal) => terminal.name === selected)) {
      // The pane the workspace was left on, when it still exists; else the first.
      const focused = session.terminals.find((terminal) => terminal.name === session.focused);
      setSelected(focused?.name ?? session.terminals[0]?.name ?? "");
    }
  }, [session, state?.workspaces, selected, setWorkspace, setWorkspaces]);

  // Report the selected pane: it is the voice/prompt target, and the backend
  // saves it with the workspace so a reopened app restores the focus.
  const sessionId = session?.id ?? "";
  useEffect(() => {
    if (!sessionId || !selected) return;
    void syncAgenticIdeSurface({ workspaceId: sessionId, view: "grid", onScreen, terminal: null, promptTarget: selected })
      .catch((error) => console.warn("Agentic IDE focus report failed:", error));
  }, [sessionId, selected, onScreen]);

  const run = useCallback(async (work: () => Promise<void>) => {
    ++refreshEpoch.current; // invalidate reads started before this mutation
    setBusy(true);
    try { await work(); await refresh(); }
    catch (error) { pushToast("error", (error as Error).message); }
    finally { setBusy(false); }
  }, [pushToast, refresh]);

  const activateFromTree = useCallback(async (workspaceId: string) => {
    pendingActivation.current = workspaceId;
    useIdeProjectsStore.getState().setPendingWorkspaceId(workspaceId);
    if (activationRunning.current) return;
    activationRunning.current = true;
    ++refreshEpoch.current;
    setBusy(true);
    let lastAttempt = workspaceId;
    try {
      while (pendingActivation.current) {
        const targetId = pendingActivation.current;
        lastAttempt = targetId;
        pendingActivation.current = null;
        const target = useIdeProjectsStore.getState().projects.flatMap((project) => project.workspaces).find((workspace) => workspace.id === targetId);
        try {
          const next = target?.status === "closed" ? await restoreIdeWorkspace(targetId) : await activateWorkspace(targetId);
          // A newer click is waiting: finish the switch in order, but never
          // paint the superseded workspace or publish it as current context.
          if (pendingActivation.current) continue;
          setState(next);
          await refresh(true);
        } catch (error) {
          pushToast("error", (error as Error).message);
          // A superseded activation may already have changed the backend.
          // Reconcile before clearing the pending marker; refresh's guards
          // discard this snapshot if another workspace click arrives meanwhile.
          if (!pendingActivation.current) {
            try { await refresh(true); }
            catch (refreshError) { pushToast("error", (refreshError as Error).message); }
          }
        }
      }
    } finally {
      activationRunning.current = false;
      setBusy(false);
      if (useIdeProjectsStore.getState().pendingWorkspaceId === lastAttempt) useIdeProjectsStore.getState().setPendingWorkspaceId(null);
    }
  }, [pushToast, refresh]);

  useEffect(() => {
    if (!action || handledAction.current === action.nonce) return;
    handledAction.current = action.nonce;
    if (action.kind === "connect-project") { setProjectDialog(true); return; }
    if (action.kind === "workspace-options") { if (action.workspaceId === session?.id) setOptionsOpen(true); return; }
    if (action.kind === "toggle-voice") {
      setVoiceOpen((current) => { const next = !current; storeVoiceBubbleOpen(next); return next; });
      return;
    }
    if (action.kind === "new-workspace") {
      const project = projects.find((entry) => entry.id === action.projectId);
      if (project) { setWorkspaceProject(project); setWorkspaceName(""); setWorkspaceAgents([installed[0]?.name ?? ""]); }
      return;
    }
    void activateFromTree(action.workspaceId);
  }, [action, activateFromTree, installed, projects, session?.id]);

  const connect = () => void run(async () => {
    if (!projectPath) throw new Error("Choose a folder for this project.");
    const project = await openProject(projectPath, projectName.trim() || undefined);
    setProjectDialog(false); setProjectPath(null); setProjectName("");
    const listing = await fetchIdeProjects();
    setProjects(listing.projects);
    publishProjects(listing.projects, listing.active_workspace_id);
    const found = listing.projects.find((entry) => entry.id === project.id);
    if (found) { setWorkspaceProject(found); setWorkspaceAgents([installed[0]?.name ?? ""]); }
  });

  const createWorkspace = () => void run(async () => {
    if (!workspaceProject || workspaceAgents.length === 0 || workspaceAgents.some((agent) => !agent)) throw new Error("Choose an installed coding agent for every session.");
    const next = await startIdeSession(workspaceProject.path, workspaceAgents.map((agent) => ({ agent })), {
      projectId: workspaceProject.id, name: workspaceName.trim() || undefined,
    });
    setState(next);
    setWorkspaceProject(null);
  });

  const addAgent = (
    agentName: string,
    workspaceId: string,
    anchorName?: string,
    direction: PaneSplitDirection = "down",
  ) => void run(async () => {
    const wasBalanced = session?.id === workspaceId && isBalancedWorkspace(session.layout, session.terminals);
    const hasExplicitAnchor = Boolean(anchorName);
    let next = await addTerminal({
      workspace_id: workspaceId,
      agent: agentName,
      anchor: anchorName || undefined,
      direction,
    });
    // Grow an automatic grid evenly when adding without an explicit anchor;
    // keep a custom arrangement until another pane would exceed the workspace bounds.
    if (next.id === workspaceId && next.terminals.length > 1 && ((wasBalanced && !hasExplicitAnchor) || (next.layout && !fitsWorkspace(next.layout)))) {
      const balanced = await reorderIdeTerminals(next.id, next.terminals.map((terminal) => terminal.history_id ?? terminal.key));
      next = balanced.session ?? next;
    }
    setState((current) => current?.session?.id === next.id ? { ...current, session: next } : current);
  });

  const closeAgent = (terminal: TerminalState) => void run(async () => {
    if (!session || !window.confirm(`Close ${terminal.name}? Its coding agent will stop.`)) return;
    await closeTerminal(terminal.history_id ? `pane:${terminal.history_id}` : terminal.name, session.id);
  });

  const saveFont = (size: number) => { const next = Math.max(9, Math.min(22, size)); setFontSize(next); localStorage.setItem(FONT_KEY, String(next)); };
  const saveAppearance = (next: "light" | "dark" | null) => { setAppearance(next); if (next) localStorage.setItem(APPEARANCE_KEY, next); else localStorage.removeItem(APPEARANCE_KEY); };
  const balanceLayout = () => void run(async () => {
    if (!session) return;
    const next = await reorderIdeTerminals(session.id, session.terminals.map((terminal) => terminal.history_id ?? terminal.key));
    setState((current) => current?.session?.id === next.session?.id ? next : current);
  });
  // A pane's own menu names the pane and direction. The workspace "+" names
  // neither (and may pass a click event): an automatic even grid then stays
  // automatic, a hand-arranged one splits the selected pane in the last
  // direction the user picked. The dialog shows both and lets the user change it.
  const openAgentPicker = useCallback((anchor?: unknown, direction?: PaneSplitDirection) => {
    if (!session) return;
    const named = typeof anchor === "string" ? anchor.trim() : "";
    const pane = named || (isBalancedWorkspace(session.layout, session.terminals) ? "" : selected);
    setSplitAnchor(session.terminals.some((terminal) => terminal.name === pane) ? pane : "");
    setSplitDirection(isSplitDirection(direction) ? direction : storedSplitDirection());
    setAgentPicker({ id: session.id, name: session.name ?? session.project.name });
  }, [session, selected]);
  // The chosen direction when it fits; otherwise the first that does; null
  // when the anchor has no room at all (the agent then joins the even grid).
  const pickerAnchorKey = session?.terminals.find((terminal) => terminal.name === splitAnchor)?.key;
  const directionFits = (direction: PaneSplitDirection) =>
    Boolean(session && pickerAnchorKey && canSplitFit(session.layout, session.terminals, pickerAnchorKey, direction));
  const effectiveDirection = !pickerAnchorKey ? null
    : directionFits(splitDirection) ? splitDirection
    : SPLIT_DIRECTIONS.find((item) => directionFits(item.id))?.id ?? null;
  const closeVoice = () => { setVoiceOpen(false); storeVoiceBubbleOpen(false); };
  const jumpToPane = (workspaceId: string, pane: string) => void run(async () => {
    if (workspaceId !== session?.id) setState(await activateWorkspace(workspaceId));
    setSelected(pane);
  });
  useEffect(() => {
    if (!paneRequest || handledPaneRequest.current === paneRequest.nonce) return;
    handledPaneRequest.current = paneRequest.nonce;
    jumpToPane(paneRequest.workspaceId, paneRequest.pane);
  }, [paneRequest, jumpToPane]);
  useEffect(() => {
    setStagedPane(session ? selected : null);
  }, [session, selected, setStagedPane]);
  const saveWorkspaceName = () => void run(async () => {
    if (!session || !renameValue.trim()) return;
    setState(await renameWorkspace(session.id, renameValue.trim()));
    setRenameOpen(false);
  });
  const stopWorkspace = () => void run(async () => {
    if (!session || !window.confirm(`Close ${session.name ?? session.project.name}? Its coding agents will stop.`)) return;
    setState(await closeWorkspace(session.id));
  });

  if (state === null) return <div data-testid="agentic-ide-loading" className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading projects…</div>;

  return <div className="relative flex h-full min-h-0 flex-col bg-background text-foreground" data-testid="igentic-ide">
    <WorkspaceOptionsDialog open={optionsOpen && !!session} onOpenChange={setOptionsOpen} workspace={session?.name ?? session?.project.name ?? ""}
      count={session?.terminals.length ?? 0} busy={busy} canAdd={installed.length > 0}
      onAdd={openAgentPicker} onBalance={balanceLayout} onRename={() => { setRenameValue(session?.name ?? session?.project.name ?? ""); setRenameOpen(true); }}
      onClose={stopWorkspace} fontSize={fontSize} onFontSize={saveFont} appearance={appearance} onAppearance={saveAppearance}
      />

    <main className="min-h-0 flex-1">
      <IdeSidePanelFrame>
      {session ? <WorkspaceTerminalGrid key={session.id} session={session} onChanged={(next) => setState((current) => current?.session?.id === next.id ? { ...current, session: next } : current)}
        onAdd={openAgentPicker} onClose={closeAgent} onSelect={setSelected} selected={selected} fontSize={fontSize} appearance={appearance} disabled={busy}
        onMutationStart={beginGridMutation} onMutationEnd={endGridMutation} />
      : <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <FolderPlus className="h-8 w-8 text-muted-foreground/70" />
        <h1 className="text-lg font-medium">{projects.some((project) => !project.scratch && !project.archived) ? "Choose a workspace" : "Connect a project"}</h1>
        <p className="max-w-md text-sm text-muted-foreground">{projects.some((project) => !project.scratch && !project.archived) ? "Select a workspace from Projects, or create one with + beside its project." : "Connect a folder to bring its coding agents and Jarvis into one workspace."}</p>
        <button type="button" onClick={() => setProjectDialog(true)} className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-accent">Connect folder</button>
      </div>}
      </IdeSidePanelFrame>
    </main>

    <VoiceBubble open={voiceOpen} onClose={closeVoice} onScreen={onScreen} onJumpToPane={jumpToPane} promptTarget={selected} />

    {agentPicker && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm"
      role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setAgentPicker(null); }}>
      <section data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label="Add coding agent"
        className="w-full max-w-lg rounded-2xl border border-border bg-card p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div><h2 className="text-base font-semibold">Add coding agent</h2><p className="mt-1 text-xs text-muted-foreground">{agentPicker.name}</p></div>
          <button type="button" aria-label="Close" onClick={() => setAgentPicker(null)} className="rounded-md p-1.5 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>

        {session && session.terminals.length > 0 && (() => {
          const anchorTerminal = session.terminals.find((terminal) => terminal.name === splitAnchor);
          return <div className="mb-4 space-y-2.5 rounded-xl border border-border bg-muted/40 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium text-foreground">Where should it open?</span>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                Next to
                <select aria-label="Split next to" value={splitAnchor} disabled={busy} onChange={(event) => setSplitAnchor(event.target.value)}
                  className="h-7 rounded-md border border-input bg-background px-2 text-xs font-medium text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring">
                  {session.terminals.map((terminal) => <option key={terminal.name} value={terminal.name}>{terminal.name} · {terminal.display_name}</option>)}
                  <option value="">Automatic even grid</option>
                </select>
              </label>
            </div>
            {anchorTerminal ? <div className="grid grid-cols-4 gap-2" role="radiogroup" aria-label="Split direction">
              {SPLIT_DIRECTIONS.map((item) => {
                const fits = directionFits(item.id);
                const checked = effectiveDirection === item.id;
                return <button key={item.id} type="button" role="radio" aria-checked={checked} aria-label={`Split ${item.label.toLowerCase()}`}
                  disabled={busy || !fits} title={fits ? item.hint : "No room: a workspace holds at most four columns and two rows."}
                  onClick={() => { setSplitDirection(item.id); storeSplitDirection(item.id); }}
                  className={cn("flex flex-col items-center justify-center gap-1.5 rounded-lg border py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40",
                    checked ? "border-primary bg-primary/10 text-foreground ring-1 ring-primary/40" : "border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground")}>
                  <item.Icon className="h-4 w-4" /><span>{item.label}</span>
                </button>;
              })}
            </div> : <p className="text-xs text-muted-foreground">The new agent joins the grid and every pane gets an equal share.</p>}
            {anchorTerminal && !effectiveDirection && <p className="text-xs text-muted-foreground">No room beside {anchorTerminal.name}; the new agent joins the automatic grid instead.</p>}
          </div>;
        })()}

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {installed.map((agent) => <button key={agent.name} type="button" disabled={busy}
            onClick={() => { const owner = agentPicker.id; setAgentPicker(null); addAgent(agent.name, owner, effectiveDirection ? splitAnchor : undefined, effectiveDirection ?? "down"); }}
            className="flex min-h-12 items-center gap-3 rounded-lg border border-border px-3 py-2 text-left text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <AgentMark agent={agent.name} label={agent.display_name} logoUrl={agent.logo_url} variant="plain" />{agent.display_name}
          </button>)}
        </div>
        {installed.length === 0 && <p className="text-sm text-muted-foreground">Connect a coding agent in CLIs to continue.</p>}
      </section>
    </div>}

    {projectDialog && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setProjectDialog(false); }}>
      <section data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label="Connect project" className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-3"><h2 className="text-sm font-semibold">Connect project folder</h2><button type="button" aria-label="Close" onClick={() => setProjectDialog(false)}><X className="h-4 w-4" /></button></div>
        <div className="min-h-0 flex-1 overflow-auto px-5 py-3"><FolderPicker selected={projectPath} onSelect={setProjectPath} /></div>
        <div className="border-t border-border px-5 py-3"><label className="block text-xs text-muted-foreground">Project name (optional)<input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder={projectPath?.split(/[\\/]/).filter(Boolean).at(-1) ?? "Project name"} className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground" /></label>
          <div className="mt-3 flex justify-end gap-2"><button type="button" onClick={() => setProjectDialog(false)} className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent">Cancel</button><button type="button" disabled={busy || !projectPath} onClick={connect} className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50">Connect project</button></div>
        </div>
      </section>
    </div>}

    {workspaceProject && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) setWorkspaceProject(null); }}>
      <section data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label="New workspace" aria-busy={busy}
        className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl">
        <header className="shrink-0 px-6 pb-5 pt-6 sm:px-8 sm:pt-7">
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-xl font-semibold tracking-tight">New workspace</h2>
            <button type="button" aria-label="Close" disabled={busy} onClick={() => setWorkspaceProject(null)}
              className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><X className="h-5 w-5" /></button>
          </div>
          <p className="mt-1 truncate text-sm text-muted-foreground" title={workspaceProject.path}>
            <span className="text-foreground">{workspaceProject.name}</span><span className="mx-2 opacity-50">/</span>{workspaceProject.path}
          </p>
        </header>
        <div className="min-h-0 space-y-6 overflow-y-auto px-6 pb-6 sm:px-8">
          <label className="block text-xs font-medium text-muted-foreground">Name (optional)
            <input value={workspaceName} disabled={busy} onChange={(event) => setWorkspaceName(event.target.value)}
              placeholder="Workspace" className="mt-2 h-11 w-full rounded-lg border border-input bg-background/60 px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring/30" />
          </label>
          <WorkspaceAgentSetup agents={installed} sessions={workspaceAgents} onChange={setWorkspaceAgents} disabled={busy} />
        </div>
        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border bg-muted/20 px-6 py-4 sm:px-8">
          <span className="text-xs text-muted-foreground" aria-live="polite">{workspaceAgents.length} {workspaceAgents.length === 1 ? "session" : "sessions"} · {new Set(workspaceAgents.filter(Boolean)).size} {new Set(workspaceAgents.filter(Boolean)).size === 1 ? "agent" : "agents"}</span>
          <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={() => setWorkspaceProject(null)} className="rounded-lg px-4 py-2.5 text-sm text-muted-foreground hover:bg-muted disabled:opacity-50">Cancel</button>
            <button type="button" disabled={busy || workspaceAgents.length === 0 || workspaceAgents.some((name) => !installed.some((agent) => agent.name === name))}
              onClick={createWorkspace} className="rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-50">{busy ? "Starting…" : "Create workspace"}</button>
          </div>
        </footer>
      </section>
    </div>}
    {renameOpen && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setRenameOpen(false); }}>
      <form data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label="Rename workspace" onSubmit={(event) => { event.preventDefault(); saveWorkspaceName(); }} className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-2xl">
        <h2 className="mb-4 text-sm font-semibold">Rename workspace</h2>
        <label className="block text-xs text-muted-foreground">Workspace name<input autoFocus value={renameValue} onChange={(event) => setRenameValue(event.target.value)} className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground" /></label>
        <div className="mt-4 flex justify-end gap-2"><button type="button" onClick={() => setRenameOpen(false)} className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent">Cancel</button><button type="submit" disabled={busy || !renameValue.trim()} className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50">Save</button></div>
      </form>
    </div>}
  </div>;
}
