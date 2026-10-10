import { useCallback, useEffect, useRef, useState } from "react";
import { FolderPlus, Loader2, Plus, X } from "lucide-react";
import { fill, useT } from "@/i18n";
import { CustomCliDialog } from "@/components/agentic/CustomCliDialog";
import { ProjectConnectDialog } from "@/components/agentic/ProjectConnectDialog";
import { VoiceBubble, storedVoiceBubbleOpen, storeVoiceBubbleOpen } from "@/components/agentic/VoiceBubble";
import { RetainedWorkspaceGrid } from "@/components/agentic/RetainedWorkspaceGrid";
import { WorkspaceAgentSetup } from "@/components/agentic/WorkspaceAgentSetup";
import { RUN_ON_AUTO, RunOnPicker, storeRunOn, storedRunOn } from "@/components/agentic/RunOnPicker";
import { WorkspaceOptionsDialog } from "@/components/agentic/WorkspaceOptionsDialog";
import { FONT_DEFAULT } from "@/components/agentic/paneFont";
import { storePaneStyle, storedPaneStyle, type PaneStyle } from "@/components/agentic/terminalThemes";
import { IdeSidePanelFrame } from "@/components/agentic/sidePanel/IdeSidePanel";
import { isBalancedWorkspace } from "@/components/agentic/workspaceDocking";
import { AgentMark } from "@/components/agentic/AgentMark";
import { CloseAgentDialog, type CloseTarget } from "@/components/agentic/CloseAgentDialog";
import { IdeHotkeyMenu } from "@/components/agentic/IdeHotkeyMenu";
import { leaderPassthrough, PANE_COMMAND_EVENT, PANE_INPUT_EVENT, type IdeHotkeyAction, type PaneCommand, type PaneCommandDetail, type PaneInputDetail } from "@/components/agentic/ideHotkeys";
import { appChord } from "@/store/appChordSettings";
import { GitCheckoutPicker } from "@/components/agentic/git/GitCheckoutPicker";
import { GitPanelDialog } from "@/components/agentic/git/GitPanelDialog";
import { computersApi } from "@/lib/computersApi";
import { KEEP_CHECKOUT, prepareGit, type GitPlan } from "@/lib/gitApi";
import { SplitRightIcon, SplitBelowIcon, SplitLeftIcon, SplitAboveIcon } from "@/components/agentic/splitIcons";
import type { PaneSplitDirection } from "@/components/agentic/WorkspaceTerminalHeader";
import { cn } from "@/lib/utils";
import { BrandedSelect } from "@/components/ui/select";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useIdeSkillsStore } from "@/store/ideSkills";
import { useIdeThreadsStore } from "@/store/ideThreads";
import { ThreadView } from "@/components/agentic/threads/ThreadView";
import { ThreadTerminalDrawer } from "@/components/agentic/threads/ThreadTerminalDrawer";
import { CodeEditorStage } from "@/components/agentic/editor/CodeEditorStage";
import { useCodeEditorStore } from "@/store/codeEditor";
import { openProject } from "@/lib/chatLibraryApi";
import {
  activateWorkspace, addTerminal, closeTerminal, closeWorkspace, fetchIdeAgents, fetchIdeProjects, fetchIdeState, renameWorkspace,
  reorderIdeTerminals, restoreIdeWorkspace, startIdeSession, syncAgenticIdeSurface,
  type AgentStatus, type IdeProject, type IdeState, type TerminalState,
} from "@/lib/agenticIdeApi";

const APPEARANCE_KEY = "jarvis.agenticIde.terminalAppearance";
const SPLIT_DIRECTION_KEY = "jarvis.agenticIde.splitDirection";

/** Each direction's words are locale keys (`ide_view.split_<key>…`), translated where rendered. */
const SPLIT_DIRECTIONS: { id: PaneSplitDirection; key: string; Icon: typeof SplitRightIcon }[] = [
  { id: "right", key: "right", Icon: SplitRightIcon },
  { id: "down", key: "down", Icon: SplitBelowIcon },
  { id: "left", key: "left", Icon: SplitLeftIcon },
  { id: "above", key: "up", Icon: SplitAboveIcon },
];

const isSplitDirection = (value: unknown): value is PaneSplitDirection =>
  SPLIT_DIRECTIONS.some((item) => item.id === value);

/** The computer every pane of a workspace runs on, or null when it runs here (or mixed). */
function workspaceRunsOn(terminals: readonly TerminalState[]): string | null {
  const places = new Set(terminals.map((terminal) => terminal.computer_id || ""));
  const [only] = [...places];
  return places.size === 1 && only ? only : null;
}

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
  // Grid of terminals, or one thread at a time (see store/ideThreads).
  const layout = useIdeThreadsStore((state) => state.layout);
  const threads = layout === "threads";
  // The code editor lies over the grid or the thread; both stay mounted underneath.
  const ideWorkspace = useIdeChatStore((state) => state.workspace);
  const stagedPane = useIdeChatStore((state) => state.stagedPane);
  const editorShown = useCodeEditorStore((state) => state.visible && state.tabs.some((tab) => tab.workspaceId === ideWorkspace?.id));
  // A maximized side panel (the Verse) covers the grid, which only turns invisible
  // and keeps a narrowed tile. Its panes leave the stage so they neither paint
  // nor size their agents to a strip nobody is reading.
  const panelCoversGrid = useIdeSidePanelStore((state) => state.open && state.maximized);
  const [state, setState] = useState<IdeState | null>(null);
  const [projects, setProjects] = useState<IdeProject[]>([]);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [projectDialog, setProjectDialog] = useState(false);
  const [workspaceProject, setWorkspaceProject] = useState<IdeProject | null>(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceAgents, setWorkspaceAgents] = useState<string[]>([]);
  // The git half of "New workspace" and "Add coding agent": keep the checkout,
  // branch, or give the agents a worktree of their own.
  const [workspaceGit, setWorkspaceGit] = useState<GitPlan>(KEEP_CHECKOUT);
  // Where the new workspace's agents run: null = this computer (see RunOnPicker).
  const [workspaceComputer, setWorkspaceComputer] = useState<string | null>(null);
  // Where an added agent runs; preset to where its neighbour runs.
  const [agentComputer, setAgentComputer] = useState<string | null>(null);
  const [agentGit, setAgentGit] = useState<GitPlan>(KEEP_CHECKOUT);
  const [gitOpen, setGitOpen] = useState(false);
  const [selected, setSelected] = useState("");
  const [agentPicker, setAgentPicker] = useState<{ id: string; name: string } | null>(null);
  // "Add your own command" from inside the agent picker: the CLI the user wants
  // is usually missing exactly when they are looking at this list.
  const [customCliOpen, setCustomCliOpen] = useState(false);
  const t = useT();
  // Where the next agent opens: split off `splitAnchor` (a pane call-sign) in
  // `splitDirection`, or — with no anchor — the automatic even grid.
  const [splitDirection, setSplitDirection] = useState<PaneSplitDirection>(storedSplitDirection);
  const [splitAnchor, setSplitAnchor] = useState("");
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [closeRequest, setCloseRequest] = useState<
    { kind: "terminal"; terminal: TerminalState; workspaceId: string } | { kind: "workspace"; workspaceId: string } | null
  >(null);
  const [voiceOpen, setVoiceOpen] = useState(storedVoiceBubbleOpen);
  // One fixed, dense text size (the look of a standalone terminal); the
  // maintainer does not want a per-user zoom for the workspace terminals.
  const fontSize = FONT_DEFAULT;
  const [appearance, setAppearance] = useState<"light" | "dark" | null>(() => {
    const stored = localStorage.getItem(APPEARANCE_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  });
  const [paneStyle, setPaneStyle] = useState<PaneStyle>(storedPaneStyle);
  const handledAction = useRef(0);
  const handledPaneRequest = useRef(0);
  const refreshEpoch = useRef(0);
  const activationRunning = useRef(false);
  const pendingActivation = useRef<string | null>(null);
  const gridMutations = useRef(0);
  const session = state?.session ?? null;
  const codingAgents = agents.filter((agent) => agent.kind !== "shell" && agent.accepts_prompts !== false);
  const installed = codingAgents.filter((agent) => agent.installed);
  // On a connected computer the CLI has to be installed THERE, not here: the
  // server is checked before anything is copied, so every coding CLI is offered.
  // "Automatic" may land on this PC, so it offers only what is installed here.
  const workspaceChoices = workspaceComputer && workspaceComputer !== RUN_ON_AUTO ? codingAgents : installed;
  const agentChoices = agents.filter((agent) =>
    (agent.kind === "shell" || agent.accepts_prompts !== false) && (agentComputer || agent.installed));
  const dialogOpen = projectDialog || workspaceProject !== null || renameOpen || agentPicker !== null;

  useEffect(() => {
    setOptionsOpen(false);
  }, [session?.id]);

  useEffect(() => {
    // The custom-CLI form sits above the agent picker and owns its own focus and
    // Escape; trapping keys for the picker underneath would steal them.
    if (!dialogOpen || projectDialog || customCliOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = document.querySelector<HTMLElement>("[data-ide-dialog]");
    if (!dialog) return;
    const focusable = () => [...dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])')];
    (dialog.querySelector<HTMLElement>("[data-autofocus]") ?? focusable()[0] ?? dialog).focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // A field that backs out of itself on Escape (the folder picker's path
        // and new-folder fields) gets the key instead of the whole dialog closing.
        if (event.target instanceof Element && event.target.closest("[data-escape-local]")) return;
        event.preventDefault();
        if ((workspaceProject || projectDialog) && busy) { event.stopPropagation(); return; }
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
  }, [dialogOpen, projectDialog, workspaceProject, renameOpen, agentPicker, busy, customCliOpen]);

  const refresh = useCallback(async (allowDuringActivation = false) => {
    if (((activationRunning.current || gridMutations.current > 0) && !allowDuringActivation)) return;
    const epoch = ++refreshEpoch.current;
    let [nextState, listing] = await Promise.all([fetchIdeState(), fetchIdeProjects()]);
    // A workspace switch can fall between the two reads. Never publish a tree
    // whose active row disagrees with the grid it is paired with.
    if (nextState.active_id !== listing.active_workspace_id) {
      [nextState, listing] = await Promise.all([fetchIdeState(), fetchIdeProjects()]);
    }
    if (epoch !== refreshEpoch.current || pendingActivation.current || ((activationRunning.current || gridMutations.current > 0) && !allowDuringActivation) || nextState.active_id !== listing.active_workspace_id) return;
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
  // The Skills tab pastes into the selected pane when a card's button is used.
  useEffect(() => {
    useIdeSkillsStore.getState().setTarget(sessionId && selected ? { workspaceId: sessionId, pane: selected } : null);
  }, [sessionId, selected]);
  useEffect(() => () => useIdeSkillsStore.getState().setTarget(null), []);
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
    if (action.kind === "git-panel") {
      // The panel follows the ACTIVE workspace: bring the asked one to the front first.
      if (action.workspaceId !== session?.id) void activateFromTree(action.workspaceId);
      setGitOpen(true);
      return;
    }
    if (action.kind === "toggle-voice") {
      setVoiceOpen((current) => { const next = !current; storeVoiceBubbleOpen(next); return next; });
      return;
    }
    if (action.kind === "new-workspace") {
      const project = projects.find((entry) => entry.id === action.projectId);
      if (project) {
        setWorkspaceProject(project); setWorkspaceName(""); setWorkspaceAgents([installed[0]?.name ?? ""]);
        setWorkspaceGit(action.worktree ? { mode: "new_worktree", branch: "", base: "" } : KEEP_CHECKOUT);
        setWorkspaceComputer(storedRunOn(project.id));
      }
      return;
    }
    void activateFromTree(action.workspaceId);
  }, [action, activateFromTree, installed, projects, session?.id]);

  const connect = async (projectPath: string, projectName?: string) => {
    if (!projectPath) throw new Error(t("ide_view.choose_folder"));
    const project = await openProject(projectPath, projectName);
    const listing = await fetchIdeProjects();
    setProjects(listing.projects);
    publishProjects(listing.projects, listing.active_workspace_id);
    const found = listing.projects.find((entry) => entry.id === project.id);
    // In the thread layout a new project starts a thread, not a workspace.
    if (found && useIdeThreadsStore.getState().layout === "threads") { useIdeThreadsStore.getState().newThread(found.id); setProjectDialog(false); return; }
    if (found) { setWorkspaceProject(found); setWorkspaceAgents([installed[0]?.name ?? ""]); setWorkspaceGit(KEEP_CHECKOUT); setWorkspaceComputer(storedRunOn(found.id)); }
    setProjectDialog(false);
  };

  const notify = useCallback((message: string) => pushToast("success", message), [pushToast]);

  const createWorkspace = () => void run(async () => {
    if (!workspaceProject || workspaceAgents.length === 0 || workspaceAgents.some((agent) => !agent)) throw new Error(t("ide_view.choose_agent_every"));
    storeRunOn(workspaceProject.id, workspaceComputer);
    // "Automatic" settles on one machine now, by the shares set under Settings, Computers.
    const runsOn = workspaceComputer === RUN_ON_AUTO ? await computersApi.pickPlacement() : workspaceComputer;
    // Git first: a new branch or worktree decides WHICH folder the agents start in.
    const prepared = workspaceGit.mode === "current" ? null : await prepareGit(workspaceProject.path, workspaceGit);
    const ownCheckout = workspaceGit.mode === "new_worktree" || workspaceGit.mode === "open_worktree";
    const next = await startIdeSession(prepared?.folder ?? workspaceProject.path, workspaceAgents.map((agent) => ({ agent })), {
      projectId: workspaceProject.id, name: workspaceName.trim() || (ownCheckout && prepared?.branch ? prepared.branch : undefined),
      computerId: runsOn ?? undefined,
      onMessage: notify,
    });
    setState(next);
    setWorkspaceProject(null);
    if (prepared?.message) pushToast("success", prepared.message);
  });

  // An agent with a worktree of its own opens as its own workspace tab in that
  // worktree, grouped under the same project, so its files never collide with
  // the agents sharing the original checkout.
  const addAgentInWorktree = (agentName: string, plan: GitPlan, computerId: string | null) => void run(async () => {
    if (!session) return;
    const prepared = await prepareGit(session.folder, plan);
    setState(await startIdeSession(prepared.folder, [{ agent: agentName }], {
      projectId: session.project_id ?? undefined, name: prepared.branch || undefined,
      computerId: computerId ?? undefined, onMessage: notify,
    }));
    if (prepared.message) pushToast("success", prepared.message);
  });
  // A worktree opened from the Git panel runs where the workspace it came from runs.
  const openWorktreeWorkspace = (path: string, branch: string) => void run(async () => {
    const computerId = session ? workspaceRunsOn(session.terminals) : null;
    const agent = (computerId ? codingAgents : installed)[0]?.name;
    if (!agent) throw new Error(t("ide_view.connect_agent_clis"));
    setState(await startIdeSession(path, [{ agent }], {
      projectId: session?.project_id ?? undefined, name: branch || undefined,
      computerId: computerId ?? undefined, onMessage: notify,
    }));
  });
  const newWorktreeWorkspace = () => {
    const project = projects.find((entry) => entry.id === session?.project_id);
    if (!project) { pushToast("error", t("ide_view.no_project")); return; }
    setWorkspaceProject(project); setWorkspaceName(""); setWorkspaceAgents([installed[0]?.name ?? ""]);
    setWorkspaceGit({ mode: "new_worktree", branch: "", base: "" });
    setWorkspaceComputer(session ? workspaceRunsOn(session.terminals) : storedRunOn(project.id));
  };

  const addAgent = (
    agentName: string,
    workspaceId: string,
    anchorName?: string,
    direction: PaneSplitDirection = "down",
    computerId: string | null = null,
    onDone?: () => void,
  ) => void run(async () => {
    const wasBalanced = session?.id === workspaceId && isBalancedWorkspace(session.layout, session.terminals);
    const hasExplicitAnchor = Boolean(anchorName);
    // "This PC" is said out loud only where it differs from the default: in a
    // workspace with remote panes, an omitted choice would follow them.
    const anyRemote = Boolean(session?.terminals.some((terminal) => terminal.computer_id));
    let next = await addTerminal({
      workspace_id: workspaceId,
      agent: agentName,
      anchor: anchorName || undefined,
      direction,
      ...(computerId ? { computer_id: computerId } : anyRemote ? { computer_id: null } : {}),
    }, { onMessage: notify });
    onDone?.();
    // Grow an automatic grid evenly when adding without an explicit anchor;
    // a custom arrangement is always kept — it has no size limit.
    if (next.id === workspaceId && next.terminals.length > 1 && wasBalanced && !hasExplicitAnchor) {
      const balanced = await reorderIdeTerminals(next.id, next.terminals.map((terminal) => terminal.history_id ?? terminal.key));
      next = balanced.session ?? next;
    }
    setState((current) => current?.session?.id === next.id ? { ...current, session: next } : current);
  });

  // Closing asks first in an in-app dialog, never window.confirm (the shell
  // shows that as an unstyled host-named box in the OS language).
  const closeAgent = (terminal: TerminalState) => {
    if (session) setCloseRequest({ kind: "terminal", terminal, workspaceId: session.id });
  };
  const confirmClose = () => void run(async () => {
    const request = closeRequest;
    if (!request) return;
    try {
      if (request.kind === "terminal") {
        await closeTerminal(request.terminal.history_id ? `pane:${request.terminal.history_id}` : request.terminal.name, request.workspaceId);
      } else {
        setState(await closeWorkspace(request.workspaceId));
      }
    } finally { setCloseRequest(null); }
  });

  const saveAppearance = (next: "light" | "dark" | null) => { setAppearance(next); if (next) localStorage.setItem(APPEARANCE_KEY, next); else localStorage.removeItem(APPEARANCE_KEY); };
  const savePaneStyle = (next: PaneStyle) => { setPaneStyle(next); storePaneStyle(next); };
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
    setAgentGit(KEEP_CHECKOUT);
    // A new agent runs where the pane it opens beside runs, else where the
    // whole workspace runs; the dialog lets the user pick another place.
    const anchorPane = session.terminals.find((terminal) => terminal.name === pane);
    setAgentComputer(anchorPane ? anchorPane.computer_id || null : workspaceRunsOn(session.terminals));
    setAgentPicker({ id: session.id, name: session.name ?? session.project.name });
  }, [session, selected]);
  // The chosen direction beside the chosen pane; null without an anchor (the
  // agent then joins the grid). Every direction fits: a workspace has no size limit.
  const pickerAnchorKey = session?.terminals.find((terminal) => terminal.name === splitAnchor)?.key;
  const effectiveDirection = pickerAnchorKey ? splitDirection : null;
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
  const stopWorkspace = () => {
    if (session) setCloseRequest({ kind: "workspace", workspaceId: session.id });
  };
  // Ctrl+B, then a key (see components/agentic/ideHotkeys): the IDE's
  // key menu. Pane commands go to the grid that owns the pane; the rest are
  // the same calls the buttons and the sidebar make.
  const hotkeysEnabled = onScreen && !threads && !dialogOpen && !optionsOpen && !gitOpen && !closeRequest;
  const hotkeyAgents = installed.map((agent) => ({ name: agent.name, label: agent.display_name }));
  const sendPaneCommand = (command: PaneCommand) => {
    if (!session || !selected) return;
    const detail: PaneCommandDetail = { workspaceId: session.id, pane: selected, command };
    window.dispatchEvent(new CustomEvent(PANE_COMMAND_EVENT, { detail }));
  };
  const openWorkspaces = state?.workspaces ?? [];
  const goToWorkspace = (index: number) => {
    const target = openWorkspaces[index];
    if (!target) { pushToast("info", fill(t("ide_view.no_workspace_n"), { number: index + 1 })); return; }
    if (target.id !== session?.id) void activateFromTree(target.id);
  };
  const runHotkey = (hotkey: IdeHotkeyAction): void => {
    switch (hotkey.kind) {
      case "split": {
        // tmux/herdr split: the new pane runs what the focused pane runs.
        const focused = session?.terminals.find((terminal) => terminal.name === selected);
        const agent = focused?.agent ?? installed[0]?.name;
        if (!agent) { openAgentPicker(); return; }
        runHotkey({ kind: "spawn", agent, direction: hotkey.direction });
        return;
      }
      case "spawn": {
        if (!session) { setProjectDialog(true); return; }
        const anchor = session.terminals.find((terminal) => terminal.name === selected);
        // A workspace has no size limit: beside the selected pane always fits.
        const fits = Boolean(anchor && hotkey.direction);
        addAgent(hotkey.agent, session.id, fits ? anchor!.name : undefined, fits ? hotkey.direction! : "down",
          anchor ? anchor.computer_id || null : workspaceRunsOn(session.terminals));
        return;
      }
      case "agent-picker": openAgentPicker(); return;
      case "focus-pane": sendPaneCommand({ kind: "focus", direction: hotkey.direction }); return;
      case "swap-pane": sendPaneCommand({ kind: "swap", direction: hotkey.direction }); return;
      case "maximize-pane": sendPaneCommand({ kind: "maximize" }); return;
      case "fork-pane": sendPaneCommand({ kind: "fork" }); return;
      case "rename-pane": return; // the menu asks for the name itself
      case "close-pane": {
        const terminal = session?.terminals.find((entry) => entry.name === selected);
        if (terminal) closeAgent(terminal);
        return;
      }
      case "balance": balanceLayout(); return;
      case "toggle-voice": setVoiceOpen((current) => { const next = !current; storeVoiceBubbleOpen(next); return next; }); return;
      case "workspace-index": goToWorkspace(hotkey.index); return;
      case "workspace-step": {
        if (openWorkspaces.length < 2) return;
        const at = Math.max(0, openWorkspaces.findIndex((workspace) => workspace.id === session?.id));
        goToWorkspace((at + hotkey.step + openWorkspaces.length) % openWorkspaces.length);
        return;
      }
      case "new-workspace": {
        const project = projects.find((entry) => entry.id === session?.project_id);
        if (!project) { setProjectDialog(true); return; }
        setWorkspaceProject(project); setWorkspaceName(""); setWorkspaceAgents([installed[0]?.name ?? ""]);
        setWorkspaceGit(KEEP_CHECKOUT); setWorkspaceComputer(storedRunOn(project.id));
        return;
      }
      case "new-worktree-workspace": newWorktreeWorkspace(); return;
      case "rename-workspace":
        if (session) { setRenameValue(session.name ?? session.project.name); setRenameOpen(true); }
        return;
      case "close-workspace": stopWorkspace(); return;
      case "git-panel": if (session) setGitOpen(true); return;
      case "workspace-options": if (session) setOptionsOpen(true); return;
      case "connect-project": setProjectDialog(true); return;
    }
  };
  const closeTarget: CloseTarget | null = !closeRequest ? null
    : closeRequest.kind === "terminal"
      ? { kind: "terminal", name: closeRequest.terminal.name, agent: closeRequest.terminal.agent, displayName: closeRequest.terminal.display_name }
      : { kind: "workspace", name: session?.name ?? session?.project.name ?? t("ide_view.workspace_fallback"),
        agents: (session?.terminals ?? []).map((terminal) => ({ agent: terminal.agent, displayName: terminal.display_name })) };

  if (state === null) return <div data-testid="agentic-ide-loading" className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{t("ide_view.loading_projects")}</div>;

  return <div className="relative flex h-full min-h-0 flex-col bg-background text-foreground" data-testid="igentic-ide">
    <WorkspaceOptionsDialog open={optionsOpen && !!session} onOpenChange={setOptionsOpen} workspace={session?.name ?? session?.project.name ?? ""}
      count={session?.terminals.length ?? 0} busy={busy} canAdd={agents.some((agent) => agent.installed && (agent.kind === "shell" || agent.accepts_prompts !== false))}
      onAdd={openAgentPicker} onBalance={balanceLayout} onRename={() => { setRenameValue(session?.name ?? session?.project.name ?? ""); setRenameOpen(true); }}
      onClose={stopWorkspace} onGit={() => setGitOpen(true)} appearance={appearance} onAppearance={saveAppearance}
      paneStyle={paneStyle} onPaneStyle={savePaneStyle}
      />
    {session && <GitPanelDialog open={gitOpen} onOpenChange={setGitOpen} folder={session.folder} workspace={session.name ?? session.project.name}
      onOpenWorktree={(tree) => openWorktreeWorkspace(tree.path, tree.branch)} onNewWorktree={newWorktreeWorkspace} />}
    <CloseAgentDialog target={closeTarget} busy={busy} onCancel={() => setCloseRequest(null)} onConfirm={confirmClose} />

    <main className="min-h-0 flex-1">
      <IdeSidePanelFrame markInUse={paneStyle === "minimal"} appearance={appearance ?? undefined} onScreen={onScreen}>
      <div className="relative h-full min-h-0">
      {/* The thread over its terminal drawer. The drawer stays mounted in the
          grid too, so its shells keep running while the layout switches. */}
      <div className={cn("h-full min-h-0 flex-col", threads ? "flex" : "hidden")}>
        {threads && <div className="min-h-0 flex-1"><ThreadView onScreen={onScreen && !editorShown} /></div>}
        <ThreadTerminalDrawer onScreen={onScreen && threads && !editorShown && !panelCoversGrid} />
      </div>
      {/* The grid stays mounted behind the threads: its terminals keep running
          and come back exactly as they were when the layout switches back. */}
      <div hidden={threads} className="h-full min-h-0">
      {session ? <RetainedWorkspaceGrid session={session} onScreen={onScreen && !threads && !editorShown && !panelCoversGrid} workspaceIds={state.workspaces?.map((workspace) => workspace.id)} onChanged={(next) => setState((current) => current?.session?.id === next.id ? { ...current, session: next } : current)}
        onAdd={openAgentPicker} onClose={closeAgent} onSelect={setSelected} selected={selected} fontSize={fontSize} appearance={appearance} disabled={busy}
        onMutationStart={beginGridMutation} onMutationEnd={endGridMutation} paneStyle={paneStyle} workspaces={state.workspaces ?? []} />
      : <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <FolderPlus className="h-8 w-8 text-muted-foreground/70" />
        <h1 className="text-lg font-medium">{projects.some((project) => !project.scratch && !project.archived) ? t("ide_view.choose_workspace") : t("ide_threads.connect_a_project")}</h1>
        <p className="max-w-md text-sm text-muted-foreground">{projects.some((project) => !project.scratch && !project.archived) ? t("ide_view.select_workspace_hint") : t("ide_view.connect_folder_hint")}</p>
        <button type="button" onClick={() => setProjectDialog(true)} className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-accent">{t("ide_threads.connect_folder")}</button>
      </div>}
      </div>
      <CodeEditorStage workspaceId={ideWorkspace?.id ?? null} workspacePath={ideWorkspace?.path ?? ""} stagedPane={stagedPane} />
      </div>
      </IdeSidePanelFrame>
    </main>

    <IdeHotkeyMenu enabled={hotkeysEnabled} agents={hotkeyAgents} pane={session ? selected : ""} onAction={runHotkey}
      onRenamePane={(name) => sendPaneCommand({ kind: "rename", name })}
      onPassThrough={() => {
        if (!session || !selected) return;
        // The control code of the user's leader chord; a leader that is not
        // Ctrl+letter has none to pass through.
        const data = leaderPassthrough(appChord("ide_menu"));
        if (data === null) return;
        const detail: PaneInputDetail = { workspaceId: session.id, pane: selected, data };
        window.dispatchEvent(new CustomEvent(PANE_INPUT_EVENT, { detail }));
      }} />
    <VoiceBubble open={voiceOpen} onClose={closeVoice} onScreen={onScreen} onJumpToPane={jumpToPane} promptTarget={selected} />

    {agentPicker && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm"
      role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setAgentPicker(null); }}>
      <section data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("ide_view.add_agent")}
        className="w-full max-w-lg rounded-2xl border border-border bg-card p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div><h2 className="text-base font-semibold">{t("ide_view.add_agent")}</h2><p className="mt-1 text-xs text-muted-foreground">{agentPicker.name}{session && <> · {fill(t(session.terminals.length === 1 ? "ide_view.agents_one" : "ide_view.agents_other"), { count: session.terminals.length })}</>}</p></div>
          <button type="button" aria-label={t("common.close")} onClick={() => setAgentPicker(null)} className="rounded-md p-1.5 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>

        {session && <div className="mb-4"><GitCheckoutPicker folder={session.folder} value={agentGit} onChange={setAgentGit} disabled={busy} context="agent" /></div>}
        <div className="mb-4"><RunOnPicker value={agentComputer} onChange={setAgentComputer} disabled={busy} hideWhenNone /></div>

        {session && session.terminals.length > 0 && agentGit.mode === "current" && (() => {
          const anchorTerminal = session.terminals.find((terminal) => terminal.name === splitAnchor);
          return <div className="mb-4 space-y-2.5 rounded-xl border border-border bg-muted/40 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium text-foreground">{t("ide_view.where_open")}</span>
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span>{t("ide_view.next_to")}</span>
                <BrandedSelect ariaLabel={t("ide_view.split_next_to")} value={splitAnchor} disabled={busy} onValueChange={setSplitAnchor}
                  className="h-7 w-auto min-w-[11rem] px-2 py-1 text-xs font-medium"
                  options={[
                    ...session.terminals.map((terminal) => ({ value: terminal.name, label: `${terminal.name} · ${terminal.display_name}` })),
                    { value: "", label: t("ide_view.auto_grid") },
                  ]} />
              </div>
            </div>
            {anchorTerminal ? <div className="grid grid-cols-4 gap-2" role="radiogroup" aria-label={t("ide_view.split_direction")}>
              {SPLIT_DIRECTIONS.map((item) => {
                const checked = effectiveDirection === item.id;
                return <button key={item.id} type="button" role="radio" aria-checked={checked} aria-label={t(`ide_view.split_${item.key}_aria`)}
                  disabled={busy} title={t(`ide_view.split_${item.key}_hint`)}
                  onClick={() => { setSplitDirection(item.id); storeSplitDirection(item.id); }}
                  className={cn("flex flex-col items-center justify-center gap-1.5 rounded-lg border py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40",
                    checked ? "border-primary bg-primary/10 text-foreground ring-1 ring-primary/40" : "border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground")}>
                  <item.Icon className="h-4 w-4" /><span>{t(`ide_view.split_${item.key}`)}</span>
                </button>;
              })}
            </div> : <p className="text-xs text-muted-foreground">{t("ide_view.joins_grid")}</p>}
          </div>;
        })()}

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {agentChoices.map((agent) => <button key={agent.name} type="button" disabled={busy}
            onClick={() => {
              const owner = agentPicker.id;
              // Copying a folder to a computer takes a while: the dialog stays
              // open, saying so, until the pane exists there.
              const close = () => setAgentPicker(null);
              if (!agentComputer) close();
              if (agentGit.mode !== "current") { addAgentInWorktree(agent.name, agentGit, agentComputer); if (agentComputer) close(); }
              else addAgent(agent.name, owner, effectiveDirection ? splitAnchor : undefined, effectiveDirection ?? "down", agentComputer, agentComputer ? close : undefined);
            }}
            className="flex min-h-12 items-center gap-3 rounded-lg border border-border px-3 py-2 text-left text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <AgentMark agent={agent.name} label={agent.display_name} logoUrl={agent.logo_url} variant="plain" />{agent.display_name}
          </button>)}
          {/* A command of the user's own, named and marked by them. Once saved
              it is listed here, in every pane menu, and by its name to voice
              ("open five Cursor terminals"). */}
          <button type="button" disabled={busy} data-testid="add-agent-custom-cli" onClick={() => setCustomCliOpen(true)}
            className="flex min-h-12 items-center gap-3 rounded-lg border border-dashed border-border px-3 py-2 text-left text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Plus className="h-4 w-4" aria-hidden="true" />{t("custom_cli.add_button")}
          </button>
        </div>
        {busy && agentComputer && <p role="status" className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />{t("ide_view.copying_starting")}</p>}
        {agentChoices.length === 0 && <p className="text-sm text-muted-foreground">{t("ide_view.connect_agent_clis")}</p>}
      </section>
    </div>}

    {/* Outside the picker's backdrop on purpose: a portal still bubbles React
        events to its parent, and a click in this form must not read as a click
        on the backdrop that dismisses the picker. */}
    <CustomCliDialog open={customCliOpen} onOpenChange={setCustomCliOpen}
      onSaved={() => void fetchIdeAgents(true).then((response) => setAgents(response.agents)).catch((error) => pushToast("error", (error as Error).message))} />

    {projectDialog && <ProjectConnectDialog onClose={() => setProjectDialog(false)} onConnect={connect} />}

    {workspaceProject && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) setWorkspaceProject(null); }}>
      <section data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("ide_projects.new_workspace")} aria-busy={busy}
        className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl">
        <header className="shrink-0 px-6 pb-5 pt-6 sm:px-8 sm:pt-7">
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-xl font-semibold tracking-tight">{t("ide_projects.new_workspace")}</h2>
            <button type="button" aria-label={t("common.close")} disabled={busy} onClick={() => setWorkspaceProject(null)}
              className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><X className="h-5 w-5" /></button>
          </div>
          <p className="mt-1 truncate text-sm text-muted-foreground" title={workspaceProject.path}>
            <span className="text-foreground">{workspaceProject.name}</span><span className="mx-2 opacity-50">/</span>{workspaceProject.path}
          </p>
        </header>
        <div className="min-h-0 space-y-6 overflow-y-auto px-6 pb-6 sm:px-8">
          <label className="block text-xs font-medium text-muted-foreground">{t("ide_view.name_optional")}
            <input value={workspaceName} disabled={busy} onChange={(event) => setWorkspaceName(event.target.value)}
              placeholder={t("ide_view.workspace_placeholder")} className="mt-2 h-11 w-full rounded-lg border border-input bg-background/60 px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring/30" />
          </label>
          <WorkspaceAgentSetup agents={workspaceChoices} sessions={workspaceAgents} onChange={setWorkspaceAgents} disabled={busy} />
          <GitCheckoutPicker folder={workspaceProject.path} value={workspaceGit} onChange={setWorkspaceGit} disabled={busy} context="workspace" />
          <RunOnPicker value={workspaceComputer} onChange={setWorkspaceComputer} disabled={busy} allowAuto />
        </div>
        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border bg-muted/20 px-6 py-4 sm:px-8">
          <span className="text-xs text-muted-foreground" aria-live="polite">{fill(t(workspaceAgents.length === 1 ? "ide_view.sessions_one" : "ide_view.sessions_other"), { count: workspaceAgents.length })} · {fill(t(new Set(workspaceAgents.filter(Boolean)).size === 1 ? "ide_view.agents_one" : "ide_view.agents_other"), { count: new Set(workspaceAgents.filter(Boolean)).size })}</span>
          <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={() => setWorkspaceProject(null)} className="rounded-lg px-4 py-2.5 text-sm text-muted-foreground hover:bg-muted disabled:opacity-50">{t("common.cancel")}</button>
            <button type="button" disabled={busy || workspaceAgents.length === 0 || workspaceAgents.some((name) => !workspaceChoices.some((agent) => agent.name === name))}
              onClick={createWorkspace} className="rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-50">{busy ? (workspaceComputer ? t("ide_view.copying_folder") : t("ide_view.starting")) : t("ide_projects.create_workspace")}</button>
          </div>
        </footer>
      </section>
    </div>}
    {renameOpen && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setRenameOpen(false); }}>
      <form data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("ide_projects.rename_workspace")} onSubmit={(event) => { event.preventDefault(); saveWorkspaceName(); }} className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-2xl">
        <h2 className="mb-4 text-sm font-semibold">{t("ide_projects.rename_workspace")}</h2>
        <label className="block text-xs text-muted-foreground">{t("ide_view.workspace_name")}<input autoFocus value={renameValue} onChange={(event) => setRenameValue(event.target.value)} className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground" /></label>
        <div className="mt-4 flex justify-end gap-2"><button type="button" onClick={() => setRenameOpen(false)} className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent">{t("common.cancel")}</button><button type="submit" disabled={busy || !renameValue.trim()} className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50">{t("common.save")}</button></div>
      </form>
    </div>}
  </div>;
}
