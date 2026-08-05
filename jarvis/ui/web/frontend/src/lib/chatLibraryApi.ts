/**
 * Client for the project/chat library — `/api/chat-library`.
 *
 * The shape of this module IS the lazy-loading rule: `fetchProjects` returns
 * rows with a chat COUNT, and `fetchChats` is a separate call taking one
 * project. There is deliberately no "give me everything" function, because the
 * moment one exists somebody calls it on mount and a user with forty repos
 * waits for a thousand conversations they did not ask for.
 *
 * Nothing here returns message content. Opening a conversation is a different
 * call against the coding CLI's own transcript; these rows are titles, marks
 * and timestamps.
 */

export interface ChatProject {
  id: string;
  path: string;
  name: string;
  color: string | null;
  pinned: boolean;
  archived: boolean;
  created_at: number;
  last_opened_at: number;
  /** Is the folder reachable right now? False = unplugged drive, dead share. */
  exists: boolean;
  /** How many live chats it holds. The count travels; the chats do not. */
  chats: number;
  /**
   * Is this the holder for chats started without picking a folder?
   *
   * At most one per install. Its chats are listed on their own rather than
   * among the projects, because nobody chose the folder they run in.
   */
  scratch: boolean;
}

export interface ChatRow {
  id: string;
  project_id: string;
  title: string;
  agent: string;
  model: string | null;
  account: string | null;
  /** The live pane this chat is attached to, or null when nothing is running. */
  terminal: string | null;
  /** Can the CLI reopen this conversation? False = only a fresh start is honest. */
  resumable: boolean;
  created_at: number;
  updated_at: number;
  archived: boolean;
  preview: string;
  prompts_sent: number;
}

const BASE = "/api/chat-library";

export class ChatLibraryError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ChatLibraryError";
  }
}

/**
 * One request, with the server's own explanation when it refuses.
 *
 * FastAPI answers a rejection with `{"detail": "..."}`, and that sentence is
 * almost always more useful than "Request failed" — a caller showing it to the
 * user is telling them what actually happened. A body that is not JSON (a proxy
 * error page, a restarting backend) falls back to the status text rather than
 * throwing a parse error on top of the original failure.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON — the status text is the best we have */
    }
    throw new ChatLibraryError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function fetchProjects(
  options: { includeArchived?: boolean } = {},
): Promise<ChatProject[]> {
  const query = options.includeArchived ? "?include_archived=true" : "";
  const body = await request<{ projects: ChatProject[] }>(`/projects${query}`);
  return body.projects;
}

/** Register (or re-open) a folder as a project. Idempotent by folder. */
export async function openProject(path: string, name?: string): Promise<ChatProject> {
  return request<ChatProject>("/projects", {
    method: "POST",
    body: JSON.stringify({ path, name }),
  });
}

/**
 * The one holder for chats that were started without choosing a folder.
 *
 * Idempotent, so the caller does not have to know whether it exists yet: the
 * "new session" button asks for it every time and gets the same project back.
 */
export async function openScratchProject(): Promise<ChatProject> {
  return request<ChatProject>("/projects/scratch", { method: "POST" });
}

export async function patchProject(
  projectId: string,
  changes: Partial<Pick<ChatProject, "name" | "color" | "pinned" | "archived">>,
): Promise<ChatProject> {
  return request<ChatProject>(`/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export async function deleteProject(projectId: string): Promise<boolean> {
  const body = await request<{ removed: boolean }>(
    `/projects/${encodeURIComponent(projectId)}`,
    { method: "DELETE" },
  );
  return body.removed;
}

/** One project's chats. Called when a project is opened, never on mount. */
export async function fetchChats(
  projectId: string,
  options: { includeArchived?: boolean } = {},
): Promise<ChatRow[]> {
  const query = options.includeArchived ? "?include_archived=true" : "";
  const body = await request<{ chats: ChatRow[] }>(
    `/projects/${encodeURIComponent(projectId)}/chats${query}`,
  );
  return body.chats;
}

export async function createChat(
  projectId: string,
  options: { agent: string; model?: string | null; account?: string | null; title?: string },
): Promise<ChatRow> {
  return request<ChatRow>(`/projects/${encodeURIComponent(projectId)}/chats`, {
    method: "POST",
    body: JSON.stringify(options),
  });
}

export async function patchChat(
  projectId: string,
  chatId: string,
  changes: Partial<Pick<ChatRow, "title" | "archived" | "model" | "account" | "terminal">>,
): Promise<ChatRow> {
  return request<ChatRow>(
    `/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}`,
    { method: "PATCH", body: JSON.stringify(changes) },
  );
}

export async function deleteChat(projectId: string, chatId: string): Promise<boolean> {
  const body = await request<{ removed: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}`,
    { method: "DELETE" },
  );
  return body.removed;
}

/**
 * A stable accent colour for a project that has not been given one.
 *
 * Derived from the id so the same project is the same colour on every machine
 * and after every restart, and so a user never has to pick one to get a
 * readable sidebar. The palette is fixed rather than a free hue rotation:
 * arbitrary HSL produces colours that vanish against one of the two themes.
 */
const PROJECT_COLORS = [
  "#e7c46e",
  "#7dd3fc",
  "#a5b4fc",
  "#86efac",
  "#fca5a5",
  "#f0abfc",
  "#fdba74",
  "#5eead4",
] as const;

export function projectColor(project: Pick<ChatProject, "id" | "color">): string {
  if (project.color) return project.color;
  let sum = 0;
  for (const char of project.id) sum = (sum + char.charCodeAt(0)) % 4096;
  return PROJECT_COLORS[sum % PROJECT_COLORS.length];
}
