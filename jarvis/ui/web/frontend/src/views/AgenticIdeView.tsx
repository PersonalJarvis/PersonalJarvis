import { useCallback, useEffect, useRef, useState } from "react";
import { FolderPlus, Loader2, Mic, Moon, MoreHorizontal, Plus, Sun, X, ZoomIn, ZoomOut } from "lucide-react";
import { FolderPicker } from "@/components/agentic/FolderPicker";
import { VoiceBubble, storedVoiceBubbleOpen, storeVoiceBubbleOpen } from "@/components/agentic/VoiceBubble";
import { WorkspaceTerminalGrid } from "@/components/agentic/WorkspaceTerminalGrid";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { openProject } from "@/lib/chatLibraryApi";
import {
  activateWorkspace, addTerminal, closeTerminal, closeWorkspace, fetchIdeAgents, fetchIdeProjects, fetchIdeState, renameWorkspace,
  restoreIdeWorkspace, startIdeSession,
  type AgentStatus, type IdeProject, type IdeState, type TerminalState,
} from "@/lib/agenticIdeApi";

const FONT_KEY = "jarvis.agenticIde.terminalFontSize";
const APPEARANCE_KEY = "jarvis.agenticIde.terminalAppearance";

export interface AgenticIdeViewProps { onScreen?: boolean }

/** Project and workspace navigation is shared with Sidebar; PTY tiles stay mounted by stable session ID. */
export function AgenticIdeView({ onScreen = true }: AgenticIdeViewProps) {
  const pushToast = useEventStore((state) => state.pushToast);
  const action = useIdeProjectsStore((state) => state.action);
  const publishProjects = useIdeProjectsStore((state) => state.publish);
  const setWorkspace = useIdeChatStore((state) => state.setWorkspace);
  const setWorkspaces = useIdeChatStore((state) => state.setWorkspaces);
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
  const [agentMenu, setAgentMenu] = useState(false);
  const [workspaceMenu, setWorkspaceMenu] = useState(false);
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
  const refreshEpoch = useRef(0);
  const activationRunning = useRef(false);
  const pendingActivation = useRef<string | null>(null);
  const session = state?.session ?? null;
  const activeProject = projects.find((project) => project.id === session?.project_id || project.workspaces.some((workspace) => workspace.id === session?.id));
  const installed = agents.filter((agent) => agent.installed && agent.kind !== "shell" && agent.accepts_prompts !== false);
  const dialogOpen = projectDialog || workspaceProject !== null || renameOpen;

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
        setProjectDialog(false); setWorkspaceProject(null); setRenameOpen(false);
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
  }, [dialogOpen, projectDialog, workspaceProject, renameOpen]);

  const refresh = useCallback(async (allowDuringActivation = false) => {
    if (activationRunning.current && !allowDuringActivation) return;
    const epoch = ++refreshEpoch.current;
    let [nextState, listing] = await Promise.all([fetchIdeState(), fetchIdeProjects()]);
    // A workspace switch can fall between the two reads. Never publish a tree
    // whose active row disagrees with the grid it is paired with.
    if (nextState.active_id !== listing.active_workspace_id) {
      [nextState, listing] = await Promise.all([fetchIdeState(), fetchIdeProjects()]);
    }
    if (epoch !== refreshEpoch.current || pendingActivation.current || (activationRunning.current && !allowDuringActivation) || nextState.active_id !== listing.active_workspace_id) return;
    setState(nextState);
    setProjects(listing.projects);
    publishProjects(listing.projects, listing.active_workspace_id);
  }, [publishProjects]);

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
    if (session && !session.terminals.some((terminal) => terminal.name === selected)) setSelected(session.terminals[0]?.name ?? "");
  }, [session, state?.workspaces, selected, setWorkspace, setWorkspaces]);

  const run = useCallback(async (work: () => Promise<void>) => {
    ++refreshEpoch.current; // invalidate reads started before this mutation
    setBusy(true);
    try { await work(); await refresh(); }
    catch (error) { pushToast("error", (error as Error).message); }
    finally { setBusy(false); }
  }, [pushToast, refresh]);

  const activateFromTree = useCallback(async (workspaceId: string) => {
    pendingActivation.current = workspaceId;
    if (activationRunning.current) return;
    activationRunning.current = true;
    ++refreshEpoch.current;
    setBusy(true);
    try {
      while (pendingActivation.current) {
        const targetId = pendingActivation.current;
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
        }
      }
    } finally {
      activationRunning.current = false;
      setBusy(false);
    }
  }, [pushToast, refresh]);

  useEffect(() => {
    if (!action || handledAction.current === action.nonce) return;
    handledAction.current = action.nonce;
    if (action.kind === "connect-project") { setProjectDialog(true); return; }
    if (action.kind === "new-workspace") {
      const project = projects.find((entry) => entry.id === action.projectId);
      if (project) { setWorkspaceProject(project); setWorkspaceName(""); setWorkspaceAgents([installed[0]?.name ?? ""]); }
      return;
    }
    void activateFromTree(action.workspaceId);
  }, [action, activateFromTree, installed, projects]);

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

  const addAgent = (agentName?: string) => void run(async () => {
    if (!session) return;
    if (session.terminals.length >= 8) throw new Error("This workspace already has eight sessions.");
    const next = await addTerminal({ workspace_id: session.id, agent: agentName ?? installed[0]?.name, direction: "down" });
    setState((current) => current ? { ...current, session: next } : current);
  });

  const closeAgent = (terminal: TerminalState) => void run(async () => {
    if (!session || !window.confirm(`Close ${terminal.name}? Its coding agent will stop.`)) return;
    await closeTerminal(terminal.name, session.id);
  });

  const saveFont = (size: number) => { const next = Math.max(9, Math.min(22, size)); setFontSize(next); localStorage.setItem(FONT_KEY, String(next)); };
  const saveAppearance = (next: "light" | "dark" | null) => { setAppearance(next); if (next) localStorage.setItem(APPEARANCE_KEY, next); else localStorage.removeItem(APPEARANCE_KEY); };
  const closeVoice = () => { setVoiceOpen(false); storeVoiceBubbleOpen(false); };
  const jumpToPane = (workspaceId: string, pane: string) => void run(async () => {
    if (workspaceId !== session?.id) setState(await activateWorkspace(workspaceId));
    setSelected(pane);
  });
  const saveWorkspaceName = () => void run(async () => {
    if (!session || !renameValue.trim()) return;
    setState(await renameWorkspace(session.id, renameValue.trim()));
    setRenameOpen(false);
  });
  const stopWorkspace = () => void run(async () => {
    if (!session || !window.confirm(`Close ${session.name ?? session.project.name}? Its coding agents will stop.`)) return;
    setState(await closeWorkspace(session.id));
    setWorkspaceMenu(false);
  });

  if (state === null) return <div data-testid="agentic-ide-loading" className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading projects…</div>;

  return <div className="relative flex h-full min-h-0 flex-col bg-background text-foreground" data-testid="igentic-ide">
    <header className="flex min-h-14 shrink-0 items-center gap-3 border-b border-border/70 px-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-sm font-medium">
          <span className="truncate">{activeProject?.name ?? "Projects"}</span>
          {session && <><span className="text-muted-foreground">/</span><span className="truncate text-muted-foreground">{session.name ?? session.project.name}</span></>}
        </div>
        {session && <div className="truncate text-[11px] text-muted-foreground" title={session.folder}>{session.folder}</div>}
      </div>
      {session && <>
        <span className="hidden text-xs tabular-nums text-muted-foreground sm:inline">{session.terminals.length} / 8 agents</span>
        <div className="relative"><button type="button" aria-label="Add coding agent" title="Add coding agent" aria-expanded={agentMenu} disabled={busy || session.terminals.length >= 8 || installed.length === 0} onClick={() => setAgentMenu((value) => !value)} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"><Plus className="h-4 w-4" /></button>
          {agentMenu && <div className="absolute right-0 top-full z-30 mt-1 min-w-40 rounded-lg border border-border bg-popover p-1 shadow-lg">{installed.map((agent) => <button key={agent.name} type="button" onClick={() => { setAgentMenu(false); addAgent(agent.name); }} className="block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-accent">{agent.display_name}</button>)}</div>}
        </div>
        <div className="mx-1 h-4 w-px bg-border" />
        <button type="button" aria-label="Decrease terminal text size" onClick={() => saveFont(fontSize - 1)} className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><ZoomOut className="h-4 w-4" /></button>
        <span className="w-5 text-center text-xs tabular-nums text-muted-foreground">{fontSize}</span>
        <button type="button" aria-label="Increase terminal text size" onClick={() => saveFont(fontSize + 1)} className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><ZoomIn className="h-4 w-4" /></button>
        <button type="button" aria-label="Toggle terminal appearance" title="Toggle terminal light and dark appearance" onClick={() => saveAppearance((appearance ?? "dark") === "dark" ? "light" : "dark")} className="ml-1 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">{appearance === "light" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}</button>
        <div className="relative"><button type="button" aria-label="Workspace actions" aria-expanded={workspaceMenu} onClick={() => setWorkspaceMenu((value) => !value)} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><MoreHorizontal className="h-4 w-4" /></button>
          {workspaceMenu && <div className="absolute right-0 top-full z-30 mt-1 min-w-40 rounded-lg border border-border bg-popover p-1 shadow-lg">
            <button type="button" onClick={() => { setWorkspaceMenu(false); setRenameValue(session.name ?? session.project.name); setRenameOpen(true); }} className="block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-accent">Rename workspace</button>
            <button type="button" onClick={stopWorkspace} className="block w-full rounded px-2 py-1.5 text-left text-xs text-destructive hover:bg-accent">Close workspace</button>
          </div>}
        </div>
      </>}
      <button type="button" aria-label="Jarvis Live" title="Jarvis Live" onClick={() => { const next = !voiceOpen; setVoiceOpen(next); storeVoiceBubbleOpen(next); }} className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"><Mic className="h-4 w-4" /></button>
    </header>

    <main className="min-h-0 flex-1">
      {session ? <WorkspaceTerminalGrid key={session.id} session={session} onChanged={(next) => setState((current) => current?.session?.id === next.id ? { ...current, session: next } : current)}
        onAdd={() => setAgentMenu(true)} onClose={closeAgent} onSelect={setSelected} selected={selected} fontSize={fontSize} appearance={appearance} />
      : <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <FolderPlus className="h-8 w-8 text-muted-foreground/70" />
        <h1 className="text-lg font-medium">{projects.some((project) => !project.scratch && !project.archived) ? "Choose a workspace" : "Connect a project"}</h1>
        <p className="max-w-md text-sm text-muted-foreground">{projects.some((project) => !project.scratch && !project.archived) ? "Select a workspace from Projects, or create one with + beside its project." : "Connect a folder to bring its coding agents and Jarvis into one workspace."}</p>
        <button type="button" onClick={() => setProjectDialog(true)} className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-accent">Connect folder</button>
      </div>}
    </main>

    <VoiceBubble open={voiceOpen} onClose={closeVoice} onScreen={onScreen} onJumpToPane={jumpToPane} promptTarget={selected} />

    {projectDialog && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setProjectDialog(false); }}>
      <section data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label="Connect project" className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-3"><h2 className="text-sm font-semibold">Connect project folder</h2><button type="button" aria-label="Close" onClick={() => setProjectDialog(false)}><X className="h-4 w-4" /></button></div>
        <div className="min-h-0 flex-1 overflow-auto px-5 py-3"><FolderPicker selected={projectPath} onSelect={setProjectPath} /></div>
        <div className="border-t border-border px-5 py-3"><label className="block text-xs text-muted-foreground">Project name (optional)<input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder={projectPath?.split(/[\\/]/).filter(Boolean).at(-1) ?? "Project name"} className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground" /></label>
          <div className="mt-3 flex justify-end gap-2"><button type="button" onClick={() => setProjectDialog(false)} className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent">Cancel</button><button type="button" disabled={busy || !projectPath} onClick={connect} className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50">Connect project</button></div>
        </div>
      </section>
    </div>}

    {workspaceProject && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/75 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setWorkspaceProject(null); }}>
      <section data-ide-dialog tabIndex={-1} role="dialog" aria-modal="true" aria-label="New workspace" className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between"><h2 className="text-base font-semibold">New workspace</h2><button type="button" aria-label="Close" onClick={() => setWorkspaceProject(null)}><X className="h-4 w-4" /></button></div>
        <p className="mb-4 truncate text-xs text-muted-foreground" title={workspaceProject.path}>{workspaceProject.name} · {workspaceProject.path}</p>
        <label className="block text-xs text-muted-foreground">Name (optional)<input value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder="Workspace" className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground" /></label>
        <label className="mt-3 block text-xs text-muted-foreground">Sessions<select value={workspaceAgents.length} onChange={(event) => setWorkspaceAgents((old) => Array.from({ length: Number(event.target.value) }, (_, index) => old[index] ?? installed[0]?.name ?? ""))} className="mt-1 w-full rounded-md border border-input bg-background px-2 py-2 text-sm text-foreground">{[1, 2, 3, 4, 5, 6, 7, 8].map((count) => <option value={count} key={count}>{count}</option>)}</select></label>
        <div className="mt-3 max-h-48 space-y-2 overflow-auto">{workspaceAgents.map((agent, index) => <label key={index} className="flex items-center gap-3 text-xs text-muted-foreground"><span className="w-16 shrink-0">Agent {index + 1}</span><select value={agent} onChange={(event) => setWorkspaceAgents((old) => old.map((value, at) => at === index ? event.target.value : value))} className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-2 text-sm text-foreground">{installed.map((choice) => <option value={choice.name} key={choice.name}>{choice.display_name}</option>)}</select></label>)}</div>
        {installed.length === 0 && <p className="mt-3 text-xs text-destructive">Install a coding agent from CLIs before creating a workspace.</p>}
        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setWorkspaceProject(null)} className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent">Cancel</button><button type="button" disabled={busy || installed.length === 0} onClick={createWorkspace} className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50">Create workspace</button></div>
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
