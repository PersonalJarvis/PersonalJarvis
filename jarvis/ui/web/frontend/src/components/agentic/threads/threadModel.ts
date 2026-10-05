import type { AgentChatSession } from "@/lib/agentChatApi";
import type { IdeProject } from "@/lib/agenticIdeApi";
import { createAgentChatStore, type ProviderOption } from "@/store/agentChat";

/**
 * The pure half of the IDE's thread layout: which project a thread belongs
 * to, what it is called, and what state its row shows.
 *
 * A thread is one agent-chat session on the IDE's own surface (`agent`): a
 * coding CLI driven through its own binary, so its tools, skills, MCP servers
 * and login are the CLI's, and the conversation is the CLI's own session.
 */

/** The IDE threads' chat store — one socket, one draft, one session list. */
export const useThreadChatStore = createAgentChatStore("agent", "threads");

/** How a row in the thread list reads at a glance. */
export type ThreadStatus = "approval" | "running" | "unseen" | "idle";

/** What a thread is called in the list and its header. */
export function threadTitle(session: Pick<AgentChatSession, "title" | "cli_title"> | null | undefined): string {
  return session?.cli_title?.trim() || session?.title?.trim() || "New thread";
}

/** A path in one comparable spelling: forward slashes, no trailing slash, case folded on drive paths. */
export function comparablePath(path: string): string {
  let text = path.trim().replace(/\\/g, "/");
  while (text.length > 1 && text.endsWith("/") && !/^[A-Za-z]:\/$/.test(text)) text = text.slice(0, -1);
  // Windows paths are case-insensitive; POSIX paths are not and stay as written.
  return /^[A-Za-z]:\//.test(text) || text.startsWith("//") ? text.toLowerCase() : text;
}

/** True when `child` is `parent` or lies inside it. */
export function isInside(child: string, parent: string): boolean {
  const a = comparablePath(child);
  const b = comparablePath(parent);
  if (!a || !b) return false;
  return a === b || a.startsWith(b.endsWith("/") ? b : `${b}/`);
}

/**
 * The project a thread belongs to: the one it was started in when that is
 * remembered, else the deepest project folder holding the thread's folder.
 */
export function projectIdFor(
  session: Pick<AgentChatSession, "session_id" | "cwd">,
  projects: readonly IdeProject[],
  projectOf: Readonly<Record<string, string>>,
): string | null {
  const remembered = projectOf[session.session_id];
  if (remembered && projects.some((project) => project.id === remembered)) return remembered;
  let best: IdeProject | null = null;
  for (const project of projects) {
    if (!project.path || !isInside(session.cwd, project.path)) continue;
    if (!best || comparablePath(project.path).length > comparablePath(best.path).length) best = project;
  }
  return best?.id ?? null;
}

/** Every project's threads, newest first. Threads outside every project are left out. */
export function threadsByProject(
  sessions: readonly AgentChatSession[],
  projects: readonly IdeProject[],
  projectOf: Readonly<Record<string, string>>,
): Map<string, AgentChatSession[]> {
  const out = new Map<string, AgentChatSession[]>();
  for (const session of sessions) {
    const projectId = projectIdFor(session, projects, projectOf);
    if (!projectId) continue;
    const list = out.get(projectId);
    if (list) list.push(session);
    else out.set(projectId, [session]);
  }
  for (const list of out.values()) list.sort((a, b) => b.updated_ms - a.updated_ms);
  return out;
}

/** The row's state: a waiting approval first, then work, then news since the last look. */
export function threadStatus(
  session: Pick<AgentChatSession, "session_id" | "running" | "pending_approvals" | "updated_ms">,
  seen: Readonly<Record<string, number>>,
  openSessionId: string | null,
): ThreadStatus {
  if (session.pending_approvals && session.pending_approvals.length > 0) return "approval";
  if (session.running) return "running";
  if (session.session_id !== openSessionId && session.updated_ms > (seen[session.session_id] ?? Number.POSITIVE_INFINITY)) {
    return "unseen";
  }
  return "idle";
}

/** The CLIs a thread can run on: the catalog's coding-agent rows, installed ones first. */
export function threadAgents(options: readonly ProviderOption[]): ProviderOption[] {
  return options
    .filter((option) => Boolean(option.agent))
    .sort((a, b) => Number(b.connected) - Number(a.connected));
}

/** "now", "5m", "3h", "2d", "4w" — how long ago, the way a dense list says it. */
export function shortAge(ms: number, now = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - ms) / 1000));
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return `${Math.floor(days / 7)}w`;
}

/** The last folder name of a path — what a branch-less checkout is called. */
export function folderLabel(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** The agent, model, effort and access the person picked last — what every new thread starts on. */
export interface ThreadSeat {
  provider: string;
  model: string;
  effort: string;
  permissionMode: string;
}

const SEAT_KEY = "jarvis.ide.threadSeat.v1";

/** Remember an explicit pick. Opening an old thread never calls this, so it never moves the seat. */
export function rememberSeat(seat: ThreadSeat): void {
  if (!seat.provider) return;
  try {
    localStorage.setItem(SEAT_KEY, JSON.stringify({
      provider: seat.provider, model: seat.model, effort: seat.effort, permissionMode: seat.permissionMode,
    }));
  } catch { /* storage blocked: new threads start on the provider's defaults */ }
}

export function rememberedSeat(): ThreadSeat | null {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(SEAT_KEY) ?? "null");
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Record<string, unknown>;
    if (typeof row.provider !== "string" || !row.provider) return null;
    const text = (value: unknown) => (typeof value === "string" ? value : "");
    return { provider: row.provider, model: text(row.model), effort: text(row.effort), permissionMode: text(row.permissionMode) };
  } catch {
    return null;
  }
}
