import { create } from "zustand";

/**
 * How the Agentic IDE is laid out, and which thread the thread layout shows.
 *
 * `grid` is the terminal wall: workspaces of panes. `threads` is one
 * conversation at a time — a coding agent (Claude Code, Codex, …) driven
 * through its own CLI, read as a chat. The app sidebar, the IDE view and the
 * thread view all read this; none of them can reach the others by props.
 *
 * The open thread is either an existing chat session (`sessionId`) or a draft
 * in one project (`sessionId: null`) whose first message creates the session.
 * Both survive a reload through localStorage, which may be blocked — every
 * read and write degrades to the defaults instead of throwing. So does the
 * person's own arrangement of the list: the order they dragged a project's
 * threads into and the threads they archived out of it.
 */

export type IdeLayout = "grid" | "threads";

export const IDE_LAYOUTS: readonly IdeLayout[] = ["grid", "threads"];

export interface ThreadSelection {
  /** The project the thread belongs to; "" while no project is known yet. */
  projectId: string;
  /** The chat session on screen, or null for a new thread's draft. */
  sessionId: string | null;
}

const LAYOUT_KEY = "jarvis.agenticIde.layout.v1";
const SELECTION_KEY = "jarvis.agenticIde.threadSelection.v1";
const PROJECT_OF_KEY = "jarvis.agenticIde.threadProjects.v1";
const SEEN_KEY = "jarvis.agenticIde.threadSeen.v1";
const ARCHIVED_KEY = "jarvis.agenticIde.threadArchived.v1";
const ORDER_KEY = "jarvis.agenticIde.threadOrder.v1";
/** Bounds on the two maps kept in storage — oldest entries go first. */
const MAX_REMEMBERED = 400;
/**
 * The archive's bound. Dropping an entry brings an old thread back into the
 * list, so this one is far roomier than the maps above.
 */
const MAX_ARCHIVED = 4000;

function read<T>(key: string, parse: (raw: unknown) => T | null, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    return parse(JSON.parse(raw)) ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* a convenience only: the choice just does not survive this session */
  }
}

const parseLayout = (raw: unknown): IdeLayout | null =>
  typeof raw === "string" && (IDE_LAYOUTS as readonly string[]).includes(raw) ? (raw as IdeLayout) : null;

const parseSelection = (raw: unknown): ThreadSelection | null => {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const projectId = typeof row.projectId === "string" ? row.projectId : "";
  const sessionId = typeof row.sessionId === "string" && row.sessionId ? row.sessionId : null;
  return { projectId, sessionId };
};

const parseStringMap = (raw: unknown): Record<string, string> | null => {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  return Object.fromEntries(Object.entries(raw).filter(([, value]) => typeof value === "string")) as Record<string, string>;
};

const parseNumberMap = (raw: unknown): Record<string, number> | null => {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  return Object.fromEntries(Object.entries(raw).filter(([, value]) => typeof value === "number")) as Record<string, number>;
};

const parseOrder = (raw: unknown): Record<string, string[]> | null => {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const out: Record<string, string[]> = {};
  for (const [projectId, ids] of Object.entries(raw)) {
    if (Array.isArray(ids)) out[projectId] = ids.filter((id): id is string => typeof id === "string");
  }
  return out;
};

/** Drop the oldest keys once a map outgrows `limit` (insertion order). */
function bounded<T>(map: Record<string, T>, limit = MAX_REMEMBERED): Record<string, T> {
  const keys = Object.keys(map);
  if (keys.length <= limit) return map;
  return Object.fromEntries(keys.slice(keys.length - limit).map((key) => [key, map[key]]));
}

/** Every project's order without `sessionId`; the same object when it was in none. */
function withoutInOrder(order: Record<string, string[]>, sessionId: string): Record<string, string[]> {
  if (!Object.values(order).some((ids) => ids.includes(sessionId))) return order;
  return Object.fromEntries(Object.entries(order).map(([projectId, ids]) => [projectId, ids.filter((id) => id !== sessionId)]));
}

interface IdeThreadsState {
  layout: IdeLayout;
  selection: ThreadSelection;
  /**
   * Which project a thread was started in. A thread in a fresh worktree runs
   * in a folder outside the project's own, so its folder alone cannot say.
   */
  projectOf: Record<string, string>;
  /** When each thread was last looked at (its `updated_ms` then) — drives the "new" dot. */
  seen: Record<string, number>;
  /** Threads moved out of the list, with when; each project shows them under "Archived". */
  archived: Record<string, number>;
  /** Each project's threads in the order the person dragged them into. */
  order: Record<string, string[]>;
  /** Bumped to ask the thread view to focus its composer. */
  focusNonce: number;

  setLayout: (layout: IdeLayout) => void;
  /** Open an existing thread. */
  openThread: (sessionId: string, projectId: string) => void;
  /** Open an empty draft in a project; its first message starts the thread. */
  newThread: (projectId: string) => void;
  /** The draft became a session: remember where it belongs and show it. */
  adoptThread: (sessionId: string, projectId: string) => void;
  /** A session started in `projectId` while another thread was picked: file it, show nothing new. */
  rememberProject: (sessionId: string, projectId: string) => void;
  markSeen: (sessionId: string, updatedMs: number) => void;
  /** A thread was deleted: forget everything kept about it. */
  forgetThread: (sessionId: string) => void;
  /** Move a thread out of the list; an open one gives way to its project's draft. */
  archiveThread: (sessionId: string) => void;
  /** Bring an archived thread back; it returns at the top of its project. */
  restoreThread: (sessionId: string) => void;
  /** Keep a project's threads in this order. */
  setThreadOrder: (projectId: string, sessionIds: readonly string[]) => void;
}

export const useIdeThreadsStore = create<IdeThreadsState>((set, get) => ({
  layout: read(LAYOUT_KEY, parseLayout, "grid"),
  selection: read(SELECTION_KEY, parseSelection, { projectId: "", sessionId: null }),
  projectOf: read(PROJECT_OF_KEY, parseStringMap, {}),
  seen: read(SEEN_KEY, parseNumberMap, {}),
  archived: read(ARCHIVED_KEY, parseNumberMap, {}),
  order: read(ORDER_KEY, parseOrder, {}),
  focusNonce: 0,

  setLayout: (layout) => {
    if (get().layout === layout) return;
    write(LAYOUT_KEY, layout);
    set({ layout });
  },
  openThread: (sessionId, projectId) => {
    const selection = { projectId, sessionId };
    write(SELECTION_KEY, selection);
    set({ selection });
  },
  newThread: (projectId) => {
    const selection = { projectId, sessionId: null };
    write(SELECTION_KEY, selection);
    set((state) => ({ selection, focusNonce: state.focusNonce + 1 }));
  },
  adoptThread: (sessionId, projectId) => {
    const selection = { projectId, sessionId };
    const projectOf = projectId ? bounded({ ...get().projectOf, [sessionId]: projectId }) : get().projectOf;
    write(SELECTION_KEY, selection);
    write(PROJECT_OF_KEY, projectOf);
    set({ selection, projectOf });
  },
  rememberProject: (sessionId, projectId) => {
    const projectOf = bounded({ ...get().projectOf, [sessionId]: projectId });
    write(PROJECT_OF_KEY, projectOf);
    set({ projectOf });
  },
  markSeen: (sessionId, updatedMs) => {
    if ((get().seen[sessionId] ?? 0) >= updatedMs) return;
    const { [sessionId]: _previous, ...rest } = get().seen;
    const seen = bounded({ ...rest, [sessionId]: updatedMs });
    write(SEEN_KEY, seen);
    set({ seen });
  },
  forgetThread: (sessionId) => {
    const { [sessionId]: _project, ...projectOf } = get().projectOf;
    const { [sessionId]: _seen, ...seen } = get().seen;
    const { [sessionId]: _archived, ...archived } = get().archived;
    const order = withoutInOrder(get().order, sessionId);
    write(PROJECT_OF_KEY, projectOf);
    write(SEEN_KEY, seen);
    write(ARCHIVED_KEY, archived);
    write(ORDER_KEY, order);
    const selection = get().selection.sessionId === sessionId
      ? { projectId: get().selection.projectId, sessionId: null }
      : get().selection;
    write(SELECTION_KEY, selection);
    set({ projectOf, seen, archived, order, selection });
  },
  archiveThread: (sessionId) => {
    const { [sessionId]: _previous, ...rest } = get().archived;
    const archived = bounded({ ...rest, [sessionId]: Date.now() }, MAX_ARCHIVED);
    // Out of the dragged order too, so a restored thread comes back on top.
    const order = withoutInOrder(get().order, sessionId);
    write(ARCHIVED_KEY, archived);
    write(ORDER_KEY, order);
    const selection = get().selection.sessionId === sessionId
      ? { projectId: get().selection.projectId, sessionId: null }
      : get().selection;
    write(SELECTION_KEY, selection);
    set({ archived, order, selection });
  },
  restoreThread: (sessionId) => {
    if (!(sessionId in get().archived)) return;
    const { [sessionId]: _archived, ...archived } = get().archived;
    write(ARCHIVED_KEY, archived);
    set({ archived });
  },
  setThreadOrder: (projectId, sessionIds) => {
    const order = { ...get().order, [projectId]: [...sessionIds] };
    write(ORDER_KEY, order);
    set({ order });
  },
}));
