/**
 * Projects, and the chats inside them.
 *
 * The column follows one rule the old workspace bar could not: **a project's
 * chats are fetched when the project is opened, never before.** Somebody with
 * forty repositories and a thousand conversations gets a sidebar that arrives
 * at once, and pays for exactly the list they clicked on. Each project keeps
 * whatever it has already loaded, so collapsing and reopening is free — but a
 * project that was never opened has never cost a request.
 *
 * Rows carry the coding agent's own mark rather than a generic icon, because
 * "which agent was this" is the question people actually scan the list for.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ChevronRight,
  FolderOpen,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import { AgentMark } from "@/components/agentic/AgentMark";
import { cn } from "@/lib/utils";
import {
  type ChatProject,
  type ChatRow,
  deleteChat,
  deleteProject,
  fetchChats,
  fetchProjects,
  patchChat,
  patchProject,
  projectColor,
} from "@/lib/chatLibraryApi";

/** What one project's chat list is doing right now. */
type ChatsState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; chats: ChatRow[] }
  | { status: "error"; message: string };

export interface ChatLibrarySidebarProps {
  /** The chat currently on screen, so its row can say so. */
  activeChatId?: string | null;
  /** Open a chat. The shell owns what "open" means. */
  onOpenChat?: (project: ChatProject, chat: ChatRow) => void;
  /** Start a new chat in a project — the shell asks the questions. */
  onNewChat?: (project: ChatProject) => void;
  /** Add a folder as a project. */
  onAddProject?: () => void;
}

/**
 * How long ago, in a form that fits a sidebar row.
 *
 * Deliberately coarse and locale-free: this sits under a title that already
 * competes for the row's width, and a full timestamp there reads as noise. The
 * exact time is on the chat itself.
 */
function shortAge(seconds: number): string {
  if (!seconds) return "";
  const delta = Math.max(0, Date.now() / 1000 - seconds);
  if (delta < 60) return "now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h`;
  if (delta < 604800) return `${Math.floor(delta / 86400)}d`;
  return `${Math.floor(delta / 604800)}w`;
}

export function ChatLibrarySidebar({
  activeChatId = null,
  onOpenChat,
  onNewChat,
  onAddProject,
}: ChatLibrarySidebarProps) {
  const [projects, setProjects] = useState<ChatProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [chats, setChats] = useState<Record<string, ChatsState>>({});
  const [filter, setFilter] = useState("");
  const [menuFor, setMenuFor] = useState<string | null>(null);
  /*
   * A late response from a project the user has already collapsed (or a whole
   * sidebar that unmounted) must not write into state. Without this the
   * "loading" spinner of a reopened project is replaced by the PREVIOUS
   * request's list, which is the same class of bug as a stale terminal viewer
   * painting over a fresh pane.
   */
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const reloadProjects = useCallback(async () => {
    try {
      const next = await fetchProjects();
      if (!alive.current) return;
      setProjects(next);
      setLoadError(null);
    } catch (error) {
      if (!alive.current) return;
      setLoadError(error instanceof Error ? error.message : "Could not load projects");
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reloadProjects();
  }, [reloadProjects]);

  const loadChats = useCallback(async (projectId: string) => {
    setChats((current) => ({ ...current, [projectId]: { status: "loading" } }));
    try {
      const rows = await fetchChats(projectId);
      if (!alive.current) return;
      setChats((current) => ({
        ...current,
        [projectId]: { status: "ready", chats: rows },
      }));
    } catch (error) {
      if (!alive.current) return;
      setChats((current) => ({
        ...current,
        [projectId]: {
          status: "error",
          message: error instanceof Error ? error.message : "Could not load chats",
        },
      }));
    }
  }, []);

  const toggleProject = useCallback(
    (project: ChatProject) => {
      setExpanded((current) => {
        const next = new Set(current);
        if (next.has(project.id)) {
          next.delete(project.id);
          return next;
        }
        next.add(project.id);
        // Fetch only the first time. A project that is reopened shows what it
        // already had, instantly, and refreshes only when something changes it.
        if (!chats[project.id]) void loadChats(project.id);
        return next;
      });
    },
    [chats, loadChats],
  );

  const renameChat = useCallback(
    async (projectId: string, chat: ChatRow) => {
      const next = window.prompt("Rename this chat", chat.title || "");
      if (next === null) return;
      await patchChat(projectId, chat.id, { title: next });
      void loadChats(projectId);
    },
    [loadChats],
  );

  const archiveChat = useCallback(
    async (projectId: string, chat: ChatRow) => {
      await patchChat(projectId, chat.id, { archived: true });
      void loadChats(projectId);
      void reloadProjects();
    },
    [loadChats, reloadProjects],
  );

  const removeChat = useCallback(
    async (projectId: string, chat: ChatRow) => {
      const label = chat.title || "this chat";
      if (!window.confirm(`Delete ${label}? The agent's own history is kept.`)) return;
      await deleteChat(projectId, chat.id);
      void loadChats(projectId);
      void reloadProjects();
    },
    [loadChats, reloadProjects],
  );

  const renameProject = useCallback(
    async (project: ChatProject) => {
      const next = window.prompt("Rename this project", project.name);
      if (next === null) return;
      await patchProject(project.id, { name: next });
      void reloadProjects();
    },
    [reloadProjects],
  );

  const removeProject = useCallback(
    async (project: ChatProject) => {
      if (
        !window.confirm(
          `Remove ${project.name} and its ${project.chats} chat(s)? The folder itself is untouched.`,
        )
      ) {
        return;
      }
      await deleteProject(project.id);
      void reloadProjects();
    },
    [reloadProjects],
  );

  /*
   * The filter matches on what the user can SEE — a project's name and the
   * titles of the chats already loaded under it. It deliberately does not go
   * to the server for the chats of collapsed projects: typing a letter must
   * not fire forty requests, which is the exact cost this column exists to
   * avoid.
   */
  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return projects;
    return projects.filter((project) => {
      if (project.name.toLowerCase().includes(needle)) return true;
      const state = chats[project.id];
      if (state?.status !== "ready") return false;
      return state.chats.some((chat) => chat.title.toLowerCase().includes(needle));
    });
  }, [chats, filter, projects]);

  return (
    <div
      data-testid="chat-library-sidebar"
      className="flex h-full min-h-0 w-full flex-col border-r border-border bg-card/30"
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        <div className="relative flex min-w-0 flex-1 items-center">
          <Search className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Search projects and chats"
            aria-label="Search projects and chats"
            className="h-8 w-full rounded-md border border-border bg-background/60 pl-7 pr-2 text-xs outline-none placeholder:text-muted-foreground/70 focus-visible:ring-1 focus-visible:ring-ring"
          />
        </div>
        <button
          type="button"
          onClick={onAddProject}
          title="Add a project folder"
          aria-label="Add a project folder"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
        >
          <Plus className="h-4 w-4" aria-hidden />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis p-1.5">
        {loading ? (
          <div className="flex items-center gap-2 px-2 py-6 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Loading projects…
          </div>
        ) : loadError ? (
          <div className="px-2 py-6 text-xs text-destructive">{loadError}</div>
        ) : visible.length === 0 ? (
          <div className="px-2 py-6 text-xs text-muted-foreground">
            {filter
              ? "Nothing matches that."
              : "No projects yet. Add a folder to start your first chat."}
          </div>
        ) : (
          visible.map((project) => {
            const open = expanded.has(project.id);
            const state = chats[project.id] ?? { status: "idle" as const };
            return (
              <div key={project.id} className="mb-0.5">
                <div
                  className={cn(
                    "group flex items-center gap-1.5 rounded-md px-1.5 py-1.5",
                    "transition-colors hover:bg-accent/40",
                  )}
                >
                  <button
                    type="button"
                    data-testid={`project-row-${project.id}`}
                    onClick={() => toggleProject(project)}
                    aria-expanded={open}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <ChevronRight
                      className={cn(
                        "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                        open && "rotate-90",
                      )}
                      aria-hidden
                    />
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ background: projectColor(project) }}
                      aria-hidden
                    />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">
                      {project.name}
                    </span>
                    {!project.exists && (
                      <TriangleAlert
                        className="h-3.5 w-3.5 shrink-0 text-amber-400"
                        aria-label="Folder not reachable right now"
                      />
                    )}
                    <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                      {project.chats}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => onNewChat?.(project)}
                    title={`New chat in ${project.name}`}
                    aria-label={`New chat in ${project.name}`}
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-accent/60 hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <Plus className="h-3.5 w-3.5" aria-hidden />
                  </button>
                  <div className="relative shrink-0">
                    <button
                      type="button"
                      onClick={() =>
                        setMenuFor((current) => (current === project.id ? null : project.id))
                      }
                      title={`More for ${project.name}`}
                      aria-label={`More for ${project.name}`}
                      className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-accent/60 hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
                    </button>
                    {menuFor === project.id && (
                      <div
                        className="absolute right-0 top-7 z-20 w-40 rounded-md border border-border bg-popover py-1 shadow-lg"
                        onMouseLeave={() => setMenuFor(null)}
                      >
                        <MenuItem
                          icon={Pencil}
                          label="Rename"
                          onClick={() => {
                            setMenuFor(null);
                            void renameProject(project);
                          }}
                        />
                        <MenuItem
                          icon={Archive}
                          label={project.pinned ? "Unpin" : "Pin to top"}
                          onClick={() => {
                            setMenuFor(null);
                            void patchProject(project.id, { pinned: !project.pinned }).then(
                              reloadProjects,
                            );
                          }}
                        />
                        <MenuItem
                          icon={Trash2}
                          label="Remove project"
                          destructive
                          onClick={() => {
                            setMenuFor(null);
                            void removeProject(project);
                          }}
                        />
                      </div>
                    )}
                  </div>
                </div>

                {open && (
                  <div className="ml-4 border-l border-border/60 pl-1.5">
                    {state.status === "loading" && (
                      <div className="flex items-center gap-2 px-2 py-2 text-[11px] text-muted-foreground">
                        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                        Loading chats…
                      </div>
                    )}
                    {state.status === "error" && (
                      <div className="px-2 py-2 text-[11px] text-destructive">
                        {state.message}
                      </div>
                    )}
                    {state.status === "ready" && state.chats.length === 0 && (
                      <button
                        type="button"
                        onClick={() => onNewChat?.(project)}
                        className="w-full rounded px-2 py-2 text-left text-[11px] text-muted-foreground hover:bg-accent/40 hover:text-foreground"
                      >
                        No chats yet — start one
                      </button>
                    )}
                    {state.status === "ready" &&
                      state.chats.map((chat) => (
                        <div
                          key={chat.id}
                          className={cn(
                            "group/chat flex items-center gap-1 rounded-md px-1 py-1",
                            "transition-colors hover:bg-accent/40",
                            activeChatId === chat.id && "bg-accent/60",
                          )}
                        >
                          <button
                            type="button"
                            data-testid={`chat-row-${chat.id}`}
                            onClick={() => onOpenChat?.(project, chat)}
                            className="flex min-w-0 flex-1 items-center gap-2 text-left"
                          >
                            <AgentMark agent={chat.agent} label={chat.agent} size="sm" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[11px] font-medium">
                                {chat.title || "New chat"}
                              </span>
                              {chat.preview && (
                                <span className="block truncate text-[10px] text-muted-foreground">
                                  {chat.preview}
                                </span>
                              )}
                            </span>
                            {chat.terminal && (
                              <span
                                className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400"
                                title="Running"
                                aria-label="Running"
                              />
                            )}
                            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                              {shortAge(chat.updated_at)}
                            </span>
                          </button>
                          <div className="flex shrink-0 items-center opacity-0 transition-opacity focus-within:opacity-100 group-hover/chat:opacity-100">
                            <IconButton
                              icon={Pencil}
                              label={`Rename ${chat.title || "chat"}`}
                              onClick={() => void renameChat(project.id, chat)}
                            />
                            <IconButton
                              icon={Archive}
                              label={`Archive ${chat.title || "chat"}`}
                              onClick={() => void archiveChat(project.id, chat)}
                            />
                            <IconButton
                              icon={Trash2}
                              label={`Delete ${chat.title || "chat"}`}
                              destructive
                              onClick={() => void removeChat(project.id, chat)}
                            />
                          </div>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <button
        type="button"
        onClick={onAddProject}
        className="flex items-center gap-2 border-t border-border px-3 py-2.5 text-xs text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
      >
        <FolderOpen className="h-3.5 w-3.5" aria-hidden />
        Add a project folder
      </button>
    </div>
  );
}

function IconButton({
  icon: Icon,
  label,
  onClick,
  destructive,
}: {
  icon: typeof Pencil;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent/60",
        destructive ? "hover:text-destructive" : "hover:text-foreground",
      )}
    >
      <Icon className="h-3 w-3" aria-hidden />
    </button>
  );
}

function MenuItem({
  icon: Icon,
  label,
  onClick,
  destructive,
}: {
  icon: typeof Pencil;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-accent/60",
        destructive ? "text-destructive" : "text-foreground",
      )}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {label}
    </button>
  );
}
