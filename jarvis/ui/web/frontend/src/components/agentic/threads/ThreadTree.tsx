import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Folder, FolderOpen, Loader2, Mic, MoreHorizontal, Pencil, Plus, SquarePlus, Trash2, X } from "lucide-react";
import { AgentMark } from "@/components/agentic/AgentMark";
import { patchAgentChatSession, type AgentChatSession } from "@/lib/agentChatApi";
import type { IdeProject } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeThreadsStore } from "@/store/ideThreads";
import { ThreadMenuItem, ThreadMenuSeparator, ThreadPopover } from "./ThreadPopover";
import { shortAge, threadStatus, threadTitle, threadsByProject, useThreadChatStore, type ThreadStatus } from "./threadModel";

const EXPANSION_KEY = "jarvis.ide.threadExpansion.v1";
/** Threads a project shows before "Show more". */
const FOLDED_COUNT = 6;
/** How often the list asks for fresh session rows while it is on screen. */
const POLL_MS = 4000;

function readExpansion(): Record<string, boolean> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(EXPANSION_KEY) ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([, open]) => typeof open === "boolean")) as Record<string, boolean>;
  } catch { return {}; /* an optional preference: the defaults stay usable */ }
}

function saveExpansion(value: Record<string, boolean>): void {
  try { localStorage.setItem(EXPANSION_KEY, JSON.stringify(value)); }
  catch { /* storage blocked — the choice holds for this session only */ }
}

/** The quiet mark at a row's end: what the thread is doing, else whose it is. */
function StatusMark({ status }: { status: ThreadStatus }) {
  if (status === "approval") {
    return <span role="img" aria-label="Waiting for your approval" title="Waiting for your approval"
      className="h-2 w-2 shrink-0 rounded-full bg-warning" />;
  }
  if (status === "running") {
    return <Loader2 role="img" aria-label="Working" className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />;
  }
  if (status === "unseen") {
    return <span role="img" aria-label="New activity" title="New activity" className="h-2 w-2 shrink-0 rounded-full bg-accent" />;
  }
  return null;
}

/**
 * The sidebar while the IDE shows threads: every project with its threads,
 * each named by the title its coding CLI gave the conversation and marked
 * with that CLI's logo. A project row opens and folds; its "+" starts a new
 * thread in it.
 */
export function ThreadTree() {
  const projects = useIdeProjectsStore((state) => state.projects);
  const connectProject = useIdeProjectsStore((state) => state.connectProject);
  const toggleVoice = useIdeProjectsStore((state) => state.toggleVoice);
  const pushToast = useEventStore((state) => state.pushToast);
  const sessions = useThreadChatStore((state) => state.sessions);
  const catalog = useThreadChatStore((state) => state.catalog);
  const activeSessionId = useThreadChatStore((state) => state.activeSessionId);
  const selection = useIdeThreadsStore((state) => state.selection);
  const projectOf = useIdeThreadsStore((state) => state.projectOf);
  const seen = useIdeThreadsStore((state) => state.seen);
  const openThread = useIdeThreadsStore((state) => state.openThread);
  const newThread = useIdeThreadsStore((state) => state.newThread);
  const forgetThread = useIdeThreadsStore((state) => state.forgetThread);
  const [expansion, setExpansion] = useState(readExpansion);
  const [showAll, setShowAll] = useState<Record<string, boolean>>({});
  const [renaming, setRenaming] = useState<{ sessionId: string; title: string } | null>(null);
  const [menu, setMenu] = useState<{ sessionId: string; projectId: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const menuAnchor = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const store = useThreadChatStore.getState();
    if (!store.catalog || store.catalogStale) void store.loadCatalog();
    void store.loadSessions();
    const timer = window.setInterval(() => { void useThreadChatStore.getState().loadSessions(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const visible = useMemo(() => projects.filter((project) => !project.archived && !project.scratch), [projects]);
  const grouped = useMemo(() => threadsByProject(sessions, visible, projectOf), [sessions, visible, projectOf]);
  const agentOf = useMemo(() => {
    const map = new Map<string, { agent: string; label: string }>();
    for (const provider of catalog?.providers ?? []) {
      if (provider.agent) map.set(provider.id, { agent: provider.agent, label: provider.label });
    }
    return map;
  }, [catalog]);
  const openSessionId = selection.sessionId ?? activeSessionId;

  const setProjectOpen = (id: string, open: boolean) => setExpansion((previous) => {
    const next = { ...previous, [id]: open };
    saveExpansion(next);
    return next;
  });

  const rename = async (session: AgentChatSession, title: string) => {
    const clean = title.trim();
    setRenaming(null);
    if (!clean || clean === threadTitle(session)) return;
    setBusy(session.session_id);
    try {
      await patchAgentChatSession(session.session_id, { title: clean });
      await useThreadChatStore.getState().loadSessions();
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (sessionId: string) => {
    setConfirmDelete(null);
    setBusy(sessionId);
    try {
      await useThreadChatStore.getState().removeSession(sessionId);
      forgetThread(sessionId);
    } finally {
      setBusy(null);
    }
  };

  const projectRow = (project: IdeProject) => {
    const threads = grouped.get(project.id) ?? [];
    const draftHere = selection.sessionId === null && selection.projectId === project.id;
    const open = expansion[project.id] ?? (threads.length > 0 || draftHere);
    const shown = showAll[project.id] ? threads : threads.slice(0, FOLDED_COUNT);
    const running = threads.filter((thread) => thread.running).length;
    return <div key={project.id} className="mb-px" data-testid={`thread-project-${project.id}`}>
      <div className="group relative flex min-h-8 items-center rounded-md transition-colors hover:bg-muted">
        <button type="button" aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${project.name}`}
          onClick={() => setProjectOpen(project.id, !open)} title={project.path}
          className="flex min-h-8 min-w-0 flex-1 items-center gap-2.5 rounded-md px-2 text-left text-[15px] text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {open
            ? <FolderOpen aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
            : <Folder aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />}
          <span className="min-w-0 flex-1 truncate">{project.name}</span>
          {!open && running > 0 && <Loader2 aria-label={`${running} working`} className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />}
        </button>
        <button type="button" aria-label={`New thread in ${project.name}`} title="New thread"
          data-testid={`thread-new-${project.id}`}
          onClick={() => { setProjectOpen(project.id, true); newThread(project.id); }}
          className="mr-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground/80 transition-colors hover:bg-background/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <SquarePlus className="h-4 w-4" strokeWidth={1.75} />
        </button>
      </div>
      {open && <div className="mb-1 flex flex-col gap-px">
        {draftHere && <div aria-current="page"
          className="flex min-h-8 items-center gap-2 rounded-md bg-muted px-2 pl-8 text-[15px] text-muted-foreground">
          <span className="min-w-0 flex-1 truncate">New thread</span>
        </div>}
        {shown.map((session) => {
          const selected = session.session_id === openSessionId && selection.sessionId !== null;
          const status = threadStatus(session, seen, openSessionId);
          const mark = agentOf.get(session.provider);
          const menuOpen = menu?.sessionId === session.session_id;
          if (renaming?.sessionId === session.session_id) {
            return <form key={session.session_id} className="flex min-h-8 items-center gap-1 pl-6 pr-1"
              onSubmit={(event) => { event.preventDefault(); void rename(session, renaming.title); }}>
              <input autoFocus aria-label={`Rename ${threadTitle(session)}`} value={renaming.title} maxLength={120}
                onChange={(event) => setRenaming({ sessionId: session.session_id, title: event.target.value })}
                onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setRenaming(null); } }}
                onBlur={() => void rename(session, renaming.title)}
                className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
              <button type="submit" aria-label="Save name" className="rounded p-1 text-muted-foreground hover:text-foreground"><Check className="h-4 w-4" /></button>
              <button type="button" aria-label="Cancel renaming" onMouseDown={(event) => event.preventDefault()} onClick={() => setRenaming(null)}
                className="rounded p-1 text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
            </form>;
          }
          return <div key={session.session_id} data-testid={`thread-row-${session.session_id}`}
            className={cn("group/thread relative flex min-h-8 items-center rounded-md transition-colors hover:bg-muted", (selected || menuOpen) && "bg-muted")}
            onContextMenu={(event) => {
              event.preventDefault();
              menuAnchor.current = event.currentTarget;
              setMenu({ sessionId: session.session_id, projectId: project.id });
            }}>
            <button type="button" aria-current={selected ? "page" : undefined}
              onClick={() => openThread(session.session_id, project.id)}
              onDoubleClick={() => setRenaming({ sessionId: session.session_id, title: threadTitle(session) })}
              title={threadTitle(session)}
              className={cn(
                "flex min-h-8 min-w-0 flex-1 items-center gap-2 rounded-md py-1 pl-8 pr-2 text-left text-[15px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected ? "text-foreground-strong" : "text-foreground",
              )}>
              <span className="min-w-0 flex-1 truncate">{threadTitle(session)}</span>
              {busy === session.session_id
                ? <Loader2 aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
                : <StatusMark status={status} />}
              <span className="flex shrink-0 items-center transition-opacity group-hover/thread:opacity-0 [@media(hover:none)]:opacity-0">
                {mark
                  ? <AgentMark agent={mark.agent} label={mark.label} variant="plain" size="sm" />
                  : <span className="text-xs tabular-nums text-muted-foreground">{shortAge(session.updated_ms)}</span>}
              </span>
            </button>
            <div className={cn(
              "absolute inset-y-0 right-0 flex items-center gap-1 rounded-r-md bg-gradient-to-l from-muted from-60% to-transparent pl-5 pr-1 transition-opacity",
              menuOpen ? "opacity-100" : "pointer-events-none opacity-0 group-hover/thread:pointer-events-auto group-hover/thread:opacity-100 group-focus-within/thread:pointer-events-auto group-focus-within/thread:opacity-100 [@media(hover:none)]:pointer-events-auto [@media(hover:none)]:opacity-100",
            )}>
              <span className="text-xs tabular-nums text-muted-foreground" title={new Date(session.updated_ms).toLocaleString()}>{shortAge(session.updated_ms)}</span>
              <button type="button" aria-label={`Thread actions for ${threadTitle(session)}`} aria-haspopup="menu" aria-expanded={menuOpen}
                onClick={(event) => {
                  menuAnchor.current = event.currentTarget;
                  setMenu(menuOpen ? null : { sessionId: session.session_id, projectId: project.id });
                }}
                className="rounded p-1 text-muted-foreground hover:bg-background/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <MoreHorizontal className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>;
        })}
        {threads.length > FOLDED_COUNT && <button type="button"
          onClick={() => setShowAll((current) => ({ ...current, [project.id]: !current[project.id] }))}
          className="flex min-h-7 items-center rounded-md pl-8 text-left text-[13px] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {showAll[project.id] ? "Show less" : `Show ${threads.length - FOLDED_COUNT} more`}
        </button>}
        {threads.length === 0 && !draftHere && <button type="button" onClick={() => newThread(project.id)}
          className="flex min-h-7 items-center gap-2 rounded-md pl-8 text-left text-[13px] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <Plus aria-hidden className="h-3.5 w-3.5" />Start a thread
        </button>}
      </div>}
    </div>;
  };

  const menuSession = menu ? sessions.find((session) => session.session_id === menu.sessionId) ?? null : null;

  return <div data-testid="ide-thread-tree" className="flex-1 px-2 pb-3 pt-2">
    <div className="flex h-8 items-center justify-between gap-2 pl-2 pr-1 text-[15px] font-semibold text-foreground">
      <span>Threads</span>
      <div className="flex items-center gap-1">
        <button type="button" aria-label="Jarvis Live" title="Jarvis Live" onClick={toggleVoice}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Mic className="h-3.5 w-3.5" /></button>
        <button type="button" aria-label="Connect project" title="Connect project folder" onClick={connectProject}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Plus className="h-3.5 w-3.5" /></button>
      </div>
    </div>
    {visible.some((project) => project.pinned) && <>
      <div className="px-2 pb-1 pt-3 text-[13px] font-medium text-muted-foreground/80">Pinned</div>
      {visible.filter((project) => project.pinned).map(projectRow)}
      {visible.some((project) => !project.pinned) && <div className="px-2 pb-1 pt-4 text-[13px] font-medium text-muted-foreground/80">Other projects</div>}
    </>}
    {visible.filter((project) => !project.pinned).map(projectRow)}
    {visible.length === 0 && <p className="px-2 py-2 text-[13px] text-muted-foreground">Connect a folder to start a project.</p>}

    <ThreadPopover anchor={menuAnchor} open={menu !== null && menuSession !== null} onClose={() => { setMenu(null); setConfirmDelete(null); }}
      label="Thread actions" width={220}>
      {menu && menuSession && (confirmDelete === menu.sessionId
        ? <div className="p-2">
          <p className="text-sm text-foreground">Delete this thread?</p>
          <p className="mt-1 text-xs text-muted-foreground">Its conversation leaves the list. The coding agent's own history stays on disk.</p>
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" onClick={() => setConfirmDelete(null)} className="rounded-md px-2.5 py-1 text-sm text-muted-foreground hover:bg-secondary">Cancel</button>
            <button type="button" onClick={() => { const id = menu.sessionId; setMenu(null); void remove(id); }}
              className="rounded-md bg-destructive px-2.5 py-1 text-sm text-destructive-foreground">Delete</button>
          </div>
        </div>
        : <div role="menu">
          <ThreadMenuItem icon={<Pencil className="h-3.5 w-3.5" />} label="Rename"
            onSelect={() => { setRenaming({ sessionId: menuSession.session_id, title: threadTitle(menuSession) }); setMenu(null); }} />
          <ThreadMenuItem icon={<SquarePlus className="h-3.5 w-3.5" />} label="New thread in this project"
            onSelect={() => { newThread(menu.projectId); setMenu(null); }} />
          <ThreadMenuSeparator />
          <ThreadMenuItem icon={<Trash2 className="h-3.5 w-3.5" />} label="Delete" danger disabled={Boolean(menuSession.running)}
            onSelect={() => setConfirmDelete(menu.sessionId)} />
        </div>)}
    </ThreadPopover>
  </div>;
}
