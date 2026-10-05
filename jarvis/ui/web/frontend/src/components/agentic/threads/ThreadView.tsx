import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, FolderPlus } from "lucide-react";
import { prepareGit } from "@/lib/gitApi";
import { useEventStore } from "@/store/events";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeThreadsStore } from "@/store/ideThreads";
import { ThreadBranchBar, type ThreadCheckout } from "./ThreadBranchBar";
import { ThreadComposer } from "./ThreadComposer";
import { ThreadDiffPanel } from "./ThreadDiffPanel";
import { ThreadHeader } from "./ThreadHeader";
import { ThreadMenuItem, ThreadPopover } from "./ThreadPopover";
import { ThreadTerminalDrawer, type DrawerTerminal } from "./ThreadTerminalDrawer";
import { ThreadTimeline } from "./ThreadTimeline";
import { actionInput, type ProjectAction } from "./projectActions";
import { projectIdFor, threadTitle, useThreadChatStore } from "./threadModel";

const DRAWER_HEIGHT_KEY = "jarvis.ide.threadDrawerHeight.v1";
const DIFF_OPEN_KEY = "jarvis.ide.threadDiffOpen.v1";

function storedNumber(key: string, fallback: number): number {
  try {
    const value = Number(localStorage.getItem(key));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch { return fallback; }
}

function storedFlag(key: string): boolean {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}

function storeValue(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* a convenience only */ }
}

let terminalSerial = 0;

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
  const draft = useThreadChatStore((state) => state.draft);
  const lastError = useThreadChatStore((state) => state.lastError);
  const [checkout, setCheckout] = useState<ThreadCheckout>("current");
  const [base, setBase] = useState("");
  const [projectMenu, setProjectMenu] = useState(false);
  const [terminals, setTerminals] = useState<DrawerTerminal[]>([]);
  const [activeTerminal, setActiveTerminal] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerHeight, setDrawerHeight] = useState(() => storedNumber(DRAWER_HEIGHT_KEY, 280));
  const [diffOpen, setDiffOpen] = useState(() => storedFlag(DIFF_OPEN_KEY));
  const [composerHeight, setComposerHeight] = useState(160);
  const composerBox = useRef<HTMLDivElement | null>(null);
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

  // A new draft starts in its project's own checkout.
  useEffect(() => {
    if (!isDraft) return;
    setCheckout("current");
    setBase("");
  }, [isDraft, selection.projectId]);

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

  // How tall the composer is, so the conversation scrolls clear of it.
  useEffect(() => {
    const box = composerBox.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setComposerHeight(box.offsetHeight));
    observer.observe(box);
    return () => observer.disconnect();
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

  const finishedTurns = useMemo(() => timeline.items.filter((item) => item.type === "turn" && item.status !== "running").length, [timeline.items]);
  const running = timeline.items.some((item) => item.type === "turn" && item.status === "running");

  const addTerminal = useCallback((title: string, input?: string) => {
    if (!folder) return;
    const id = `thread-term-${Date.now().toString(36)}-${++terminalSerial}`;
    setTerminals((current) => [...current, { id, title, folder, ...(input ? { input } : {}) }]);
    setActiveTerminal(id);
    setDrawerOpen(true);
  }, [folder]);

  const toggleTerminal = () => {
    if (drawerOpen) { setDrawerOpen(false); return; }
    if (!terminals.some((terminal) => terminal.folder === folder)) addTerminal("Terminal");
    else setDrawerOpen(true);
  };

  const runAction = (action: ProjectAction) => addTerminal(action.name, actionInput(action.command));

  const closeTerminal = (id: string) => {
    setTerminals((current) => {
      const next = current.filter((terminal) => terminal.id !== id);
      if (id === activeTerminal) setActiveTerminal(next[next.length - 1]?.id ?? "");
      if (!next.some((terminal) => terminal.folder === folder)) setDrawerOpen(false);
      return next;
    });
  };

  if (!project) {
    return <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center" data-testid="thread-view-empty">
      <FolderPlus className="h-8 w-8 text-muted-foreground/70" />
      <h1 className="text-lg font-medium">Connect a project</h1>
      <p className="max-w-md text-sm text-muted-foreground">Threads run a coding agent in a project folder. Connect one to start.</p>
      <button type="button" onClick={connectProject} className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-secondary">Connect folder</button>
    </div>;
  }

  // The list row carries the newest title (the CLI's own, or the first
  // message's); the open session was read before its first message named it.
  const listed = sessions.find((row) => row.session_id === selection.sessionId);
  const title = isDraft ? "New thread" : threadTitle(listed ?? session);
  const threadKey = selection.sessionId ?? `draft:${project.id}`;
  const empty = isDraft && timeline.items.length === 0;

  return <div className="flex h-full min-h-0" data-testid="thread-view">
    <div className="flex min-w-0 flex-1 flex-col">
      <ThreadHeader projectId={project.id} projectName={project.name} folder={folder} title={title}
        sessionId={selection.sessionId} terminalOpen={drawerOpen && terminals.some((terminal) => terminal.folder === folder)} onToggleTerminal={toggleTerminal}
        diffOpen={diffOpen} onToggleDiff={() => { const next = !diffOpen; setDiffOpen(next); storeValue(DIFF_OPEN_KEY, next ? "1" : "0"); }}
        gitVersion={finishedTurns} onRunAction={runAction} />
      <div className="relative flex min-h-0 flex-1 flex-col">
        {empty
          ? <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-5 pb-6">
            <h2 className="text-center text-2xl font-normal tracking-tight text-foreground">
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
            <div className="mt-6 w-full">
              <ThreadComposer threadKey={threadKey} prepareDraft={prepareDraft} autoFocusNonce={focusNonce + (onScreen ? 1 : 0)} />
              <ThreadBranchBar folder={folder} draft checkout={checkout} onCheckout={setCheckout} base={base} onBase={setBase} locked={false} />
            </div>
          </div>
          : <>
            <ThreadTimeline items={timeline.items} sessionId={selection.sessionId} bottomInset={composerHeight} />
            <div ref={composerBox} className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-background from-70% to-transparent px-5 pb-3 pt-6">
              <div className="pointer-events-auto">
                <ThreadComposer threadKey={threadKey} prepareDraft={prepareDraft} autoFocusNonce={focusNonce} />
                <ThreadBranchBar folder={folder} draft={isDraft} checkout={checkout} onCheckout={setCheckout} base={base} onBase={setBase} locked={running} />
              </div>
            </div>
          </>}
      </div>
      {terminals.length > 0 && <ThreadTerminalDrawer terminals={terminals} folder={folder} open={drawerOpen} active={activeTerminal} height={drawerHeight}
        onSelect={setActiveTerminal} onAdd={() => addTerminal("Terminal")} onCloseTab={closeTerminal} onClose={() => setDrawerOpen(false)}
        onResize={(next) => { setDrawerHeight(next); storeValue(DRAWER_HEIGHT_KEY, String(next)); }} />}
    </div>
    {diffOpen && <div className="w-[min(480px,40%)] shrink-0">
      <ThreadDiffPanel folder={folder} version={finishedTurns} onClose={() => { setDiffOpen(false); storeValue(DIFF_OPEN_KEY, "0"); }} />
    </div>}
  </div>;
}
