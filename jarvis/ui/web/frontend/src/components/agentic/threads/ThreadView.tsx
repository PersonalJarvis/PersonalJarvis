import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, FolderPlus } from "lucide-react";
import { prepareGit } from "@/lib/gitApi";
import type { AgentChatSession } from "@/lib/agentChatApi";
import { useEventStore } from "@/store/events";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeThreadsStore } from "@/store/ideThreads";
import { useThreadTerminalsStore } from "@/store/threadTerminals";
import { ThreadBranchBar, type ThreadCheckout } from "./ThreadBranchBar";
import { ThreadComposer } from "./ThreadComposer";
import { ThreadMenuItem, ThreadPopover } from "./ThreadPopover";
import { ThreadTimeline } from "./ThreadTimeline";
import { OpenSubagent, SubagentHeader, SubagentSwitcher } from "./ThreadSubagents";
import { useRecalledMessages, withoutRecalled } from "./recalledMessages";
import { fetchDiscoveredAgents, listSubagents, mergeDiscovered, subagentPath, subagentTurn, type DiscoveredAgent } from "./subagents";
import { projectIdFor, rememberedSeat, useThreadChatStore } from "./threadModel";

/**
 * The IDE's thread layout: one conversation with a coding agent at a time.
 *
 * A thread is the agent's own CLI session driven through its binary, so its
 * tools, skills, MCP servers, permissions and login are the CLI's. The view
 * opens the thread picked in the sidebar, or a draft in a project whose first
 * message starts the thread — in the project's checkout or a fresh worktree.
 */
export function ThreadView({ onScreen }: { onScreen: boolean }) {
  const pushToast = useEventStore((state) => state.pushToast);
  const projects = useIdeProjectsStore((state) => state.projects);
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  const connectProject = useIdeProjectsStore((state) => state.connectProject);
  const selection = useIdeThreadsStore((state) => state.selection);
  const projectOf = useIdeThreadsStore((state) => state.projectOf);
  const focusNonce = useIdeThreadsStore((state) => state.focusNonce);
  const sessions = useThreadChatStore((state) => state.sessions);
  const activeSessionId = useThreadChatStore((state) => state.activeSessionId);
  const activeSession = useThreadChatStore((state) => state.activeSession);
  const timeline = useThreadChatStore((state) => state.timeline);
  const recalled = useRecalledMessages((state) => (activeSessionId ? state.bySession[activeSessionId] : undefined));
  const draft = useThreadChatStore((state) => state.draft);
  const catalog = useThreadChatStore((state) => state.catalog);
  const lastError = useThreadChatStore((state) => state.lastError);
  const [checkout, setCheckout] = useState<ThreadCheckout>("current");
  const [base, setBase] = useState("");
  const [projectMenu, setProjectMenu] = useState(false);
  const [composerHeight, setComposerHeight] = useState(160);
  // The sub-agent whose conversation shows instead of the main thread's, by its spawn call.
  const [openAgent, setOpenAgent] = useState<string | null>(null);
  // Sub-agents the CLI filed on its own (Codex), read for the open thread.
  const [discovered, setDiscovered] = useState<{ sessionId: string; agents: DiscoveredAgent[] } | null>(null);
  const projectAnchor = useRef<HTMLButtonElement | null>(null);
  // The draft whose first message is on its way: its session belongs to this project.
  const startingIn = useRef<string | null>(null);

  const visible = useMemo(() => projects.filter((project) => !project.archived && !project.scratch), [projects]);
  const session = selection.sessionId
    ? (activeSession?.session_id === selection.sessionId ? activeSession : sessions.find((row) => row.session_id === selection.sessionId) ?? null)
    : null;
  const fallbackProject = visible.find((project) => project.workspaces.some((workspace) => workspace.id === activeWorkspaceId)) ?? visible[0] ?? null;
  const project = visible.find((entry) => entry.id === selection.projectId)
    ?? (session ? visible.find((entry) => entry.id === projectIdFor(session, visible, projectOf)) : undefined)
    ?? fallbackProject;
  const folder = session?.cwd || project?.path || "";
  const isDraft = selection.sessionId === null;
  const originatingProject = project?.id;
  const rememberCreated = useCallback((created: AgentChatSession) => {
    if (originatingProject) useIdeThreadsStore.getState().rememberProject(created.session_id, originatingProject);
  }, [originatingProject]);

  // The terminal drawer and its caption toggle open shells in this folder.
  useEffect(() => { useThreadTerminalsStore.getState().setFolder(folder); }, [folder]);

  // Keep the store on what the sidebar picked. A draft's first message
  // creates its session: that is this project's thread now — unless the
  // person moved on while it was being created; then it only remembers where
  // it belongs, and the thread they picked is what shows.
  useEffect(() => {
    const store = useThreadChatStore.getState();
    const threads = useIdeThreadsStore.getState();
    const pending = startingIn.current;
    const created = pending && store.activeSessionId && store.activeSessionId !== selection.sessionId ? store.activeSessionId : null;
    if (pending && created) {
      startingIn.current = null;
      if (!selection.sessionId && selection.projectId === pending) {
        threads.adoptThread(created, pending);
        return;
      }
      threads.rememberProject(created, pending);
    }
    if (selection.sessionId) {
      if (store.activeSessionId !== selection.sessionId) store.openSession(selection.sessionId);
      return;
    }
    if (startingIn.current) return; // the draft's first message is on its way
    if (store.activeSessionId || store.timeline.items.length > 0) store.newChat();
  }, [selection.sessionId, selection.projectId, activeSessionId]);

  // The agents are listed the moment the view opens, not when a menu is.
  useEffect(() => {
    const store = useThreadChatStore.getState();
    if (!store.catalog || store.catalogStale) void store.loadCatalog();
  }, []);

  // A new draft starts in its project's own checkout, on the agent and model
  // the person picked last — never on whatever thread happened to be open.
  useEffect(() => {
    if (!isDraft) return;
    setCheckout("current");
    setBase("");
    // Never picked here yet: the seat of the newest thread is the last choice made.
    const store = useThreadChatStore.getState();
    const cli = new Set((store.catalog?.providers ?? []).filter((row) => row.agent).map((row) => row.id));
    const newest = [...store.sessions].filter((row) => cli.has(row.provider)).sort((x, y) => y.created_ms - x.created_ms)[0];
    const seat = rememberedSeat() ?? (newest
      ? { provider: newest.provider, model: newest.model, effort: newest.effort, permissionMode: newest.permission_mode }
      : null);
    const draftNow = useThreadChatStore.getState().draft;
    if (seat && (seat.provider !== draftNow.provider || seat.model !== draftNow.model
      || seat.effort !== draftNow.effort || seat.permissionMode !== draftNow.permissionMode)) {
      void useThreadChatStore.getState().setDraft(seat);
    }
  }, [isDraft, selection.projectId, sessions.length > 0, Boolean(catalog)]);

  // A draft runs in its project's folder.
  useEffect(() => {
    if (!isDraft || !project || activeSessionId || startingIn.current) return;
    if (useThreadChatStore.getState().draft.cwd !== project.path) void useThreadChatStore.getState().setDraft({ cwd: project.path });
  }, [isDraft, project, activeSessionId, draft.cwd]);

  // Keep the open thread marked as read.
  useEffect(() => {
    const row = sessions.find((entry) => entry.session_id === selection.sessionId);
    if (row && onScreen) useIdeThreadsStore.getState().markSeen(row.session_id, row.updated_ms);
  }, [sessions, selection.sessionId, onScreen]);

  // How tall the composer is, so the conversation scrolls clear of it. The
  // box only exists once the thread has messages — a draft's first send
  // mounts it later — so it is measured whenever it attaches, not on mount.
  const composerObserver = useRef<ResizeObserver | null>(null);
  const composerBox = useCallback((box: HTMLDivElement | null) => {
    composerObserver.current?.disconnect();
    composerObserver.current = null;
    if (!box) return;
    setComposerHeight(box.offsetHeight);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setComposerHeight(box.offsetHeight));
    observer.observe(box);
    composerObserver.current = observer;
  }, []);

  const prepareDraft = useCallback(async (): Promise<string | null> => {
    if (!project) {
      pushToast("error", "Connect a project folder first.");
      return null;
    }
    startingIn.current = project.id;
    if (checkout !== "worktree") return project.path;
    try {
      const prepared = await prepareGit(project.path, { mode: "new_worktree", branch: "", base });
      if (prepared.message) pushToast("success", prepared.message);
      return prepared.folder;
    } catch (error) {
      startingIn.current = null;
      throw error;
    }
  }, [project, checkout, base, pushToast]);

  // A failed first send leaves the draft a draft.
  useEffect(() => {
    if (isDraft && !activeSessionId && lastError) startingIn.current = null;
  }, [isDraft, activeSessionId, lastError]);

  const running = timeline.items.some((item) => item.type === "turn" && item.status === "running");
  // Codex streams the main agent only; its sub-agents' steps come from its own files.
  const codexThread = Boolean(session && catalog?.providers.find((row) => row.id === session.provider)?.runner === "codex-cli");
  useEffect(() => {
    const id = selection.sessionId;
    if (!codexThread || !id || !onScreen) return;
    let stopped = false;
    const load = () => {
      fetchDiscoveredAgents(id)
        .then((agents) => { if (!stopped) setDiscovered({ sessionId: id, agents }); })
        // A failed read leaves the cards the stream reported; the next poll or visit tries again.
        .catch((error: unknown) => console.debug("thread sub-agents unavailable", error));
    };
    load();
    const timer = running ? window.setInterval(load, 4000) : undefined;
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [codexThread, selection.sessionId, running, onScreen]);
  const items = useMemo(() => {
    const shown = withoutRecalled(timeline.items, recalled);
    return discovered && discovered.sessionId === selection.sessionId
      ? mergeDiscovered(shown, discovered.agents, session?.vendor_session ?? "")
      : shown;
  }, [timeline.items, recalled, discovered, selection.sessionId, session?.vendor_session]);
  const agents = useMemo(() => listSubagents(items), [items]);
  const agentPath = useMemo(() => (openAgent ? subagentPath(agents, openAgent) : []), [agents, openAgent]);
  const agent = agentPath.length ? agentPath[agentPath.length - 1] : null;
  const agentItems = useMemo(() => (agent ? [subagentTurn(agent)] : []), [agent]);
  const userCount = timeline.items.filter((item) => item.type === "user").length;

  // Another thread, or a new message to the main agent, brings the main conversation back.
  useEffect(() => { setOpenAgent(null); }, [selection.sessionId]);
  const sentCount = useRef(userCount);
  useEffect(() => {
    if (userCount > sentCount.current) setOpenAgent(null);
    sentCount.current = userCount;
  }, [userCount]);

  // Escape leaves a sub-agent's conversation for the one it came from.
  useEffect(() => {
    if (!openAgent || !onScreen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("textarea, input, [contenteditable=true], [role=dialog], [role=menu]")) return;
      const parent = agentPath.length > 1 ? agentPath[agentPath.length - 2].id : null;
      setOpenAgent(parent);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openAgent, onScreen, agentPath]);

  if (!project) {
    return <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center" data-testid="thread-view-empty">
      <FolderPlus className="h-8 w-8 text-muted-foreground/70" />
      <h1 className="text-lg font-medium">Connect a project</h1>
      <p className="max-w-md text-sm text-muted-foreground">Threads run a coding agent in a project folder. Connect one to start.</p>
      <button type="button" onClick={connectProject} className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-secondary">Connect folder</button>
    </div>;
  }

  const threadKey = selection.sessionId ?? `draft:${project.id}`;
  const empty = isDraft && timeline.items.length === 0;

  const strip = <ThreadBranchBar folder={folder} draft={isDraft} checkout={checkout} onCheckout={setCheckout}
    base={base} onBase={setBase} locked={running} />;

  return <div className="relative flex h-full min-h-0 flex-col" data-testid="thread-view">
    {empty
      ? <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-5 pb-10">
        <h2 className="text-center text-2xl font-normal tracking-tight text-foreground sm:text-3xl">
          What should we build in{" "}
          <button ref={projectAnchor} type="button" aria-haspopup="menu" aria-expanded={projectMenu} onClick={() => setProjectMenu(!projectMenu)}
            className="inline-flex items-center gap-1 rounded-md font-semibold text-foreground-strong hover:bg-secondary/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            data-testid="thread-project-picker">
            {project.name}<ChevronDown aria-hidden className="h-4 w-4 text-muted-foreground" />
          </button>?
        </h2>
        <ThreadPopover anchor={projectAnchor} open={projectMenu} onClose={() => setProjectMenu(false)} label="Projects" width={260}>
          <div role="menu">
            {visible.map((entry) => <ThreadMenuItem key={entry.id} label={entry.name} selected={entry.id === project.id}
              hint={entry.id === project.id ? <Check className="h-3.5 w-3.5" /> : undefined}
              onSelect={() => { setProjectMenu(false); useIdeThreadsStore.getState().newThread(entry.id); }} />)}
            <ThreadMenuItem icon={<FolderPlus className="h-3.5 w-3.5" />} label="Connect another folder" onSelect={() => { setProjectMenu(false); connectProject(); }} />
          </div>
        </ThreadPopover>
        <div className="mt-7 w-full">
          <ThreadComposer threadKey={threadKey} prepareDraft={prepareDraft} onSessionCreated={rememberCreated} autoFocusNonce={focusNonce + (onScreen ? 1 : 0)} strip={strip} onScreen={onScreen} />
        </div>
      </div>
      : <OpenSubagent.Provider value={setOpenAgent}>
        <SubagentSwitcher entries={agents} openId={agent ? agent.id : null} onOpen={setOpenAgent} />
        {agent
          ? <ThreadTimeline items={agentItems} sessionId={`agent:${agent.id}`} bottomInset={composerHeight} folder={folder}
            lead={<SubagentHeader entry={agent} path={agentPath} onOpen={setOpenAgent} />} />
          : <ThreadTimeline items={items} sessionId={selection.sessionId} bottomInset={composerHeight} folder={folder} />}
        <div ref={composerBox} className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-background from-70% to-transparent px-5 pb-4 pt-6">
          <div className="pointer-events-auto">
            <ThreadComposer threadKey={threadKey} prepareDraft={prepareDraft} onSessionCreated={rememberCreated} autoFocusNonce={focusNonce} strip={strip} onScreen={onScreen} />
          </div>
        </div>
      </OpenSubagent.Provider>}
  </div>;
}
