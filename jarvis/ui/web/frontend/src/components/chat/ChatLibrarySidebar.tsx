/**
 * Projects, their chats, and the few things you reach for constantly.
 *
 * The column is a reading list, not a file tree. It shows, in this order: what
 * you pinned, your projects with their conversations underneath, and the
 * sessions you started without picking a folder at all. Each project shows its
 * first few chats and says how to see the rest — a repository with two hundred
 * conversations must not be able to push the project below it off the screen.
 *
 * **Chats are fetched per project, never all at once.** The first few projects
 * load with the column because they are the ones on screen; the rest load when
 * they are opened, and stay loaded afterwards. There is no request that
 * returns everything, which is what keeps a sidebar of forty repositories the
 * same cost as a sidebar of four.
 *
 * ## Why it is this quiet
 *
 * Every row used to fill with the product's signal yellow on hover and stay
 * filled while active, and every chat carried a framed tile with its agent's
 * logo in it. Forty rows of that is a grid of boxes on a bed of gold — the eye
 * has nowhere to rest and the titles, which are the only thing anybody is
 * actually reading, come last. So: selection is a barely-there lift of the
 * surface, the agent's mark is the bare glyph at the weight of the text beside
 * it, and the yellow is kept for the things that genuinely signal — a running
 * agent, a folder that cannot be reached. The list is now mostly titles, which
 * is what a list of conversations should mostly be.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ChevronDown,
  ChevronRight,
  FolderPlus,
  Loader2,
  MoreHorizontal,
  Pencil,
  Pin,
  Plus,
  Search,
  SquarePen,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import { AgentMark } from "@/components/agentic/AgentMark";
import { NavRevealButton } from "@/components/layout/NavRevealButton";
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

type ChatsState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; chats: ChatRow[] }
  | { status: "error"; message: string };

/**
 * How many projects load their chats with the column.
 *
 * The list is ordered pinned-then-recent, so the top few are the ones a person
 * is about to click. Loading them eagerly is what makes the sidebar feel
 * finished on arrival instead of unfolding under the cursor; everything below
 * loads on demand and the cost stays flat as the library grows.
 */
const EAGER_PROJECTS = 4;

/** Chats shown per project before "Show more". */
const CHATS_PREVIEW = 5;

/**
 * The one surface treatment in this column, in its two strengths.
 *
 * A plain lift of the surface — no hue, no border, no shadow. Selection is
 * legible because the row is brighter than its neighbours, which is all a
 * selection has to be; the product's accent colour stays reserved for state
 * that means something.
 */
const ROW_HOVER = "hover:bg-foreground/[0.055]";
const ROW_ACTIVE = "bg-foreground/[0.09] text-foreground";

export interface ChatLibrarySidebarProps {
  activeChatId?: string | null;
  onOpenChat?: (project: ChatProject, chat: ChatRow) => void;
  onNewChat?: (project: ChatProject) => void;
  onAddProject?: () => void;
  /** Start a chat with no project folder chosen. Absent = the row is not offered. */
  onNewSession?: () => void;
  /** Bumped by the shell when something out there changed the library. */
  refreshToken?: number;
}

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
  onNewSession,
  refreshToken = 0,
}: ChatLibrarySidebarProps) {
  const [projects, setProjects] = useState<ChatProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [showAll, setShowAll] = useState<Set<string>>(() => new Set());
  const [chats, setChats] = useState<Record<string, ChatsState>>({});
  const [filter, setFilter] = useState("");
  const [menuFor, setMenuFor] = useState<string | null>(null);

  /*
   * A response that arrives after the column is gone must not write state. The
   * same class of bug as a stale terminal viewer painting over a fresh pane:
   * the write succeeds, and what lands on screen is the OLD answer.
   */
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const loadChats = useCallback(async (projectId: string) => {
    setChats((current) =>
      current[projectId]?.status === "ready"
        ? current
        : { ...current, [projectId]: { status: "loading" } },
    );
    try {
      const rows = await fetchChats(projectId);
      if (!alive.current) return;
      setChats((current) => ({ ...current, [projectId]: { status: "ready", chats: rows } }));
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

  const reload = useCallback(async () => {
    try {
      const next = await fetchProjects();
      if (!alive.current) return;
      setProjects(next);
      setLoadError(null);
      // The top of the list is what the user is about to click; everything
      // below waits to be opened. The project-less sessions load with it
      // regardless of where they sort — their band is always on screen.
      const eager = next.slice(0, EAGER_PROJECTS);
      for (const project of next) {
        if (project.scratch || eager.includes(project)) void loadChats(project.id);
      }
    } catch (error) {
      if (!alive.current) return;
      setLoadError(error instanceof Error ? error.message : "Could not load projects");
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [loadChats]);

  useEffect(() => {
    void reload();
  }, [reload, refreshToken]);

  const toggle = useCallback(
    (project: ChatProject) => {
      setCollapsed((current) => {
        const next = new Set(current);
        if (next.has(project.id)) {
          next.delete(project.id);
          if (!chats[project.id]) void loadChats(project.id);
        } else {
          next.add(project.id);
        }
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
      void reload();
    },
    [loadChats, reload],
  );

  const removeChat = useCallback(
    async (projectId: string, chat: ChatRow) => {
      if (
        !window.confirm(
          `Delete "${chat.title || "this chat"}"? The agent's own history on disk is kept.`,
        )
      ) {
        return;
      }
      await deleteChat(projectId, chat.id);
      void loadChats(projectId);
      void reload();
    },
    [loadChats, reload],
  );

  /**
   * The holder for chats started without a folder, and what is in it.
   *
   * Deliberately NOT a "recent" band. That one re-listed the same conversations
   * already visible under their projects two inches above, so the column said
   * everything twice and the duplicate was the louder copy. This band shows the
   * chats that appear nowhere else.
   */
  const scratch = useMemo(() => projects.find((p) => p.scratch) ?? null, [projects]);
  const scratchChats = useMemo(() => {
    if (!scratch) return [];
    const state = chats[scratch.id];
    if (state?.status !== "ready") return [];
    const needle = filter.trim().toLowerCase();
    return needle
      ? state.chats.filter((c) => c.title.toLowerCase().includes(needle))
      : state.chats;
  }, [chats, filter, scratch]);

  /** Pinned projects float to their own band, like the reference client. */
  const pinned = useMemo(
    () => projects.filter((p) => p.pinned && !p.scratch),
    [projects],
  );

  const visible = useMemo(() => {
    const real = projects.filter((p) => !p.scratch);
    const needle = filter.trim().toLowerCase();
    if (!needle) return real;
    // Filters what is already on screen. Typing a letter must not fire one
    // request per project — that is the exact cost this column exists to avoid.
    return real.filter((project) => {
      if (project.name.toLowerCase().includes(needle)) return true;
      const state = chats[project.id];
      return (
        state?.status === "ready" &&
        state.chats.some((chat) => chat.title.toLowerCase().includes(needle))
      );
    });
  }, [chats, filter, projects]);

  const chatsOf = (project: ChatProject): ChatRow[] => {
    const state = chats[project.id];
    if (state?.status !== "ready") return [];
    const needle = filter.trim().toLowerCase();
    const rows = needle
      ? state.chats.filter((c) => c.title.toLowerCase().includes(needle))
      : state.chats;
    return showAll.has(project.id) ? rows : rows.slice(0, CHATS_PREVIEW);
  };

  return (
    <div
      data-testid="chat-library-sidebar"
      className="flex h-full min-h-0 w-full flex-col border-r border-border/60 bg-background"
    >
      <div className="flex items-center gap-1 px-2 pb-2 pt-2.5">
        {/* The way back to the app's own navigation, which this surface hides.
            First thing in the column because that is where every editor keeps
            it — and because a way back belongs at the start, not the end. */}
        <NavRevealButton />
        <span className="flex-1 truncate px-1 text-sm font-semibold tracking-tight">
          Chat
        </span>
      </div>

      <div className="px-2 pb-2">
        <div className="relative flex items-center">
          <Search className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-muted-foreground/70" />
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Search"
            aria-label="Search projects and chats"
            className="h-8 w-full rounded-md border border-transparent bg-foreground/[0.045] pl-7 pr-2 text-xs outline-none placeholder:text-muted-foreground/60 focus-visible:border-border focus-visible:bg-transparent"
          />
        </div>
      </div>

      {/*
        The two ways to start something, as full rows rather than icons.

        They used to be a bare "+" beside the word "Chat" — one glyph carrying
        two different meanings depending on which one you guessed it was, which
        is how somebody ends up reporting that they cannot add a folder at all.
        A row with a word on it can only mean the word.
      */}
      <div className="px-1.5 pb-1">
        <SidebarAction
          icon={SquarePen}
          label="New chat"
          testId="sidebar-new-chat"
          onClick={() => {
            const target = pinned[0] ?? visible[0];
            if (target) onNewChat?.(target);
            else onNewSession?.();
          }}
        />
        {onNewSession && (
          <SidebarAction
            icon={Plus}
            label="New session"
            hint="A chat with no project folder"
            testId="sidebar-new-session"
            onClick={onNewSession}
          />
        )}
        <SidebarAction
          icon={FolderPlus}
          label="Add project"
          testId="sidebar-add-project"
          onClick={() => onAddProject?.()}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-1.5 pb-3">
        {loading ? (
          <div className="flex items-center gap-2 px-2 py-6 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Loading…
          </div>
        ) : loadError ? (
          <div className="px-2 py-6 text-xs text-destructive">{loadError}</div>
        ) : (
          <>
            {pinned.length > 0 && (
              <>
                <Band>Pinned</Band>
                {pinned.map((project) => (
                  <button
                    key={`pin-${project.id}`}
                    type="button"
                    onClick={() => onNewChat?.(project)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-foreground/80 transition-colors",
                      ROW_HOVER,
                    )}
                  >
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: projectColor(project) }}
                      aria-hidden
                    />
                    <span className="truncate">{project.name}</span>
                  </button>
                ))}
              </>
            )}

            {visible.length > 0 && <Band>Projects</Band>}
            {visible.length === 0 && projects.length > 0 && filter && (
              <div className="px-2 py-3 text-xs text-muted-foreground">Nothing matches that.</div>
            )}
            {visible.map((project) => {
              const open = !collapsed.has(project.id);
              const state = chats[project.id] ?? { status: "idle" as const };
              const rows = chatsOf(project);
              const total =
                state.status === "ready" ? state.chats.length : project.chats;
              return (
                <div key={project.id} className="mb-0.5">
                  <div
                    className={cn(
                      "group flex items-center gap-1 rounded-md px-1 py-1 transition-colors",
                      ROW_HOVER,
                    )}
                  >
                    <button
                      type="button"
                      data-testid={`project-row-${project.id}`}
                      onClick={() => toggle(project)}
                      aria-expanded={open}
                      className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                    >
                      {open ? (
                        <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground/70" aria-hidden />
                      ) : (
                        <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/70" aria-hidden />
                      )}
                      <span
                        className="h-1.5 w-1.5 shrink-0 rounded-full"
                        style={{ background: projectColor(project) }}
                        aria-hidden
                      />
                      <span className="min-w-0 flex-1 truncate text-xs font-medium">
                        {project.name}
                      </span>
                      {!project.exists && (
                        <TriangleAlert
                          className="h-3 w-3 shrink-0 text-amber-400"
                          aria-label="Folder not reachable right now"
                        />
                      )}
                    </button>
                    <IconButton
                      icon={Plus}
                      label={`New chat in ${project.name}`}
                      onClick={() => onNewChat?.(project)}
                      hideUntilHover
                    />
                    <div className="relative shrink-0">
                      <IconButton
                        icon={MoreHorizontal}
                        label={`More for ${project.name}`}
                        onClick={() =>
                          setMenuFor((c) => (c === project.id ? null : project.id))
                        }
                        hideUntilHover
                      />
                      {menuFor === project.id && (
                        <div
                          className="absolute right-0 top-7 z-30 w-44 rounded-md border border-border bg-popover py-1 shadow-xl"
                          onMouseLeave={() => setMenuFor(null)}
                        >
                          <MenuItem
                            icon={Pencil}
                            label="Rename project"
                            onClick={() => {
                              setMenuFor(null);
                              const next = window.prompt("Rename this project", project.name);
                              if (next !== null) {
                                void patchProject(project.id, { name: next }).then(reload);
                              }
                            }}
                          />
                          <MenuItem
                            icon={Pin}
                            label={project.pinned ? "Unpin" : "Pin to top"}
                            onClick={() => {
                              setMenuFor(null);
                              void patchProject(project.id, {
                                pinned: !project.pinned,
                              }).then(reload);
                            }}
                          />
                          <MenuItem
                            icon={Trash2}
                            label="Remove project"
                            destructive
                            onClick={() => {
                              setMenuFor(null);
                              if (
                                window.confirm(
                                  `Remove ${project.name} and its ${total} chat(s)? The folder itself is untouched.`,
                                )
                              ) {
                                void deleteProject(project.id).then(reload);
                              }
                            }}
                          />
                        </div>
                      )}
                    </div>
                  </div>

                  {open && (
                    <div className="ml-3 pl-1.5">
                      {state.status === "loading" && (
                        <div className="px-2 py-1.5 text-[11px] text-muted-foreground">
                          Loading chats…
                        </div>
                      )}
                      {state.status === "error" && (
                        <div className="px-2 py-1.5 text-[11px] text-destructive">
                          {state.message}
                        </div>
                      )}
                      {state.status === "ready" && rows.length === 0 && (
                        <div className="px-2 py-1.5 text-[11px] text-muted-foreground/70">
                          No chats
                        </div>
                      )}
                      {rows.map((chat) => (
                        <ChatRowItem
                          key={chat.id}
                          chat={chat}
                          active={activeChatId === chat.id}
                          onOpen={() => onOpenChat?.(project, chat)}
                          onRename={() => void renameChat(project.id, chat)}
                          onArchive={() => void archiveChat(project.id, chat)}
                          onDelete={() => void removeChat(project.id, chat)}
                        />
                      ))}
                      {state.status === "ready" &&
                        !showAll.has(project.id) &&
                        state.chats.length > CHATS_PREVIEW && (
                          <button
                            type="button"
                            onClick={() =>
                              setShowAll((c) => new Set(c).add(project.id))
                            }
                            className="px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
                          >
                            Show more
                          </button>
                        )}
                    </div>
                  )}
                </div>
              );
            })}

            {projects.length === 0 && (
              <div className="px-2 py-6 text-xs text-muted-foreground">
                Nothing here yet. Start a session, or add a project folder to
                work in one.
              </div>
            )}

            {scratchChats.length > 0 && (
              <>
                <Band>Sessions</Band>
                {scratchChats.map((chat) => (
                  <ChatRowItem
                    key={`scratch-${chat.id}`}
                    chat={chat}
                    active={activeChatId === chat.id}
                    onOpen={() => scratch && onOpenChat?.(scratch, chat)}
                    onRename={() => scratch && void renameChat(scratch.id, chat)}
                    onArchive={() => scratch && void archiveChat(scratch.id, chat)}
                    onDelete={() => scratch && void removeChat(scratch.id, chat)}
                  />
                ))}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function ChatRowItem({
  chat,
  active,
  onOpen,
  onRename,
  onArchive,
  onDelete,
}: {
  chat: ChatRow;
  active: boolean;
  onOpen: () => void;
  onRename: () => void;
  onArchive: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={cn(
        "group/chat flex items-center gap-1 rounded-md px-1.5 py-[5px] transition-colors",
        ROW_HOVER,
        active && ROW_ACTIVE,
      )}
    >
      <button
        type="button"
        data-testid={`chat-row-${chat.id}`}
        onClick={onOpen}
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
      >
        <AgentMark
          agent={chat.agent}
          label={chat.agent || "?"}
          size="sm"
          variant="plain"
        />
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-[12px]",
            active ? "text-foreground" : "text-foreground/75",
          )}
        >
          {chat.title || "New chat"}
        </span>
        {chat.terminal ? (
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400"
            title="Running"
            aria-label="Running"
          />
        ) : (
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/70 transition-opacity group-hover/chat:opacity-0">
            {shortAge(chat.updated_at)}
          </span>
        )}
      </button>
      <div className="flex shrink-0 items-center opacity-0 transition-opacity focus-within:opacity-100 group-hover/chat:opacity-100">
        <IconButton icon={Pencil} label="Rename chat" onClick={onRename} tiny />
        <IconButton icon={Archive} label="Archive chat" onClick={onArchive} tiny />
        <IconButton icon={Trash2} label="Delete chat" onClick={onDelete} tiny destructive />
      </div>
    </div>
  );
}

/**
 * One of the column's standing actions — a word, an icon, a whole row to hit.
 *
 * Rows rather than a toolbar of glyphs because these are the three things a
 * person arrives wanting to do, and each of them has to be able to say which
 * one it is without being hovered.
 */
function SidebarAction({
  icon: Icon,
  label,
  hint,
  onClick,
  testId,
}: {
  icon: typeof Pencil;
  label: string;
  hint?: string;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      title={hint ?? label}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium text-foreground/85 transition-colors",
        ROW_HOVER,
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
      {label}
    </button>
  );
}

function Band({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 pb-1 pt-4 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60">
      {children}
    </div>
  );
}

function IconButton({
  icon: Icon,
  label,
  onClick,
  tiny,
  destructive,
  hideUntilHover,
}: {
  icon: typeof Pencil;
  label: string;
  onClick: () => void;
  tiny?: boolean;
  destructive?: boolean;
  hideUntilHover?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "flex shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-foreground/10",
        tiny ? "h-5 w-5" : "h-6 w-6",
        destructive ? "hover:text-destructive" : "hover:text-foreground",
        hideUntilHover && "opacity-0 focus-visible:opacity-100 group-hover:opacity-100",
      )}
    >
      <Icon className={tiny ? "h-3 w-3" : "h-3.5 w-3.5"} aria-hidden />
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
        "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-foreground/[0.06]",
        destructive ? "text-destructive" : "text-foreground",
      )}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {label}
    </button>
  );
}
