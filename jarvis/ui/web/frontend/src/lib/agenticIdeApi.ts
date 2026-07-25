// REST client for the Agentic IDE. Plain same-origin fetch (mirrors
// workspaceApi/chatsApi), `no-store` everywhere because WebView2 happily serves
// a stale folder listing or terminal state from cache otherwise.

export interface AgentStatus {
  name: string;
  display_name: string;
  installed: boolean;
  version: string | null;
  install_command: string | null;
}

export interface AgentsResponse {
  terminal_available: boolean;
  max_terminals: number;
  suggested_names: string[];
  agents: AgentStatus[];
}

export interface FolderItem {
  name: string;
  path: string;
  is_project: boolean;
  is_repo: boolean;
}

export interface FoldersResponse {
  path: string | null;
  parent: string | null;
  entries: FolderItem[];
  error?: string | null;
  /** Human-facing name of this machine ("Ruben's MacBook"). */
  device_name?: string | null;
}

export interface SearchResponse {
  query: string;
  entries: FolderItem[];
  truncated: boolean;
}

export interface RecentWorkspace {
  path: string;
  name: string;
  terminals: number;
  agents: Record<string, number>;
  last_used: number;
  exists: boolean;
}

export interface RecentsResponse {
  device_name: string;
  recents: RecentWorkspace[];
}

export interface ResolveResponse {
  resolved: string | null;
  candidates: FolderItem[];
  detail: string;
}

export interface ProjectProfile {
  path: string;
  name: string;
  exists: boolean;
  is_repo: boolean;
  branch: string | null;
  stacks: string[];
  instruction_files: string[];
  top_level_dirs: string[];
  skills: string[];
  /** Subagents this repo defines (`.claude/agents` / `.agents/agents`). */
  subagents: string[];
  /** Slash commands this repo defines (`.claude/commands`). */
  commands: string[];
  note: string;
}

export interface TerminalState {
  key: string;
  name: string;
  agent: string;
  display_name: string;
  index: number;
  /** Grid column, left to right. Each column is its own stack of panes. */
  column: number;
  /** Position within that column, top to bottom. */
  slot: number;
  status: "pending" | "live" | "exited" | "error";
  exit_code: number | null;
  error: string;
  started_at: number | null;
  last_output_at: number | null;
  idle_seconds: number | null;
  prompts_sent: number;
  last_prompt: string;
  lines_captured: number;
}

export interface SessionState {
  id: string;
  folder: string;
  project: ProjectProfile;
  created_at: number;
  focus_mode: boolean;
  terminals: TerminalState[];
}

export interface IdeState {
  active: boolean;
  session: SessionState | null;
  max_terminals: number;
}

export interface TerminalPlan {
  agent: string;
  name?: string;
}

async function detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    if (body?.detail) return body.detail;
  } catch {
    /* fall through */
  }
  return `request failed: ${res.status}`;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as T;
}

export function fetchIdeState(): Promise<IdeState> {
  return getJson<IdeState>("/api/agentic-ide/state");
}

export function fetchIdeAgents(): Promise<AgentsResponse> {
  return getJson<AgentsResponse>("/api/agentic-ide/agents");
}

export function fetchFolders(path?: string | null): Promise<FoldersResponse> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return getJson<FoldersResponse>(`/api/agentic-ide/folders${query}`);
}

export function searchFolders(query: string, limit = 40): Promise<SearchResponse> {
  const qs = new URLSearchParams({ q: query, limit: String(limit) });
  return getJson<SearchResponse>(`/api/agentic-ide/folders/search?${qs.toString()}`);
}

export function fetchRecents(): Promise<RecentsResponse> {
  return getJson<RecentsResponse>("/api/agentic-ide/recents");
}

export async function forgetRecent(path: string): Promise<void> {
  const res = await fetch(
    `/api/agentic-ide/recents?path=${encodeURIComponent(path)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await detail(res));
}

/**
 * Turn a drag-and-drop payload into a folder path.
 *
 * The browser refuses to tell a web page where a dropped folder lives, so the
 * caller sends whatever it could extract — a `file://` URI, a plain path, or
 * just the folder name — and the backend resolves it (searching by name when
 * that is all there is).
 */
export async function resolveDroppedFolder(payload: {
  path?: string;
  name?: string;
}): Promise<ResolveResponse> {
  const res = await fetch("/api/agentic-ide/folders/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as ResolveResponse;
}

export async function startIdeSession(
  folder: string,
  terminals: TerminalPlan[],
): Promise<SessionState> {
  const res = await fetch("/api/agentic-ide/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, terminals }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { session: SessionState };
  return body.session;
}

export async function endIdeSession(): Promise<void> {
  const res = await fetch("/api/agentic-ide/session", { method: "DELETE" });
  if (!res.ok) throw new Error(await detail(res));
}

/**
 * Open one more terminal in the running workspace.
 *
 * `direction` decides where it lands relative to `anchor`: "right" opens a new
 * column beside that pane, "down" splits the pane's own column and stacks the
 * new one under it. `agent` picks the coding CLI to run — omitted, the new pane
 * inherits the anchor's. Returns the updated workspace.
 */
export async function addTerminal(payload: {
  anchor?: string;
  direction?: "right" | "down";
  agent?: string;
  name?: string;
}): Promise<SessionState> {
  const res = await fetch("/api/agentic-ide/terminals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  if (!body.state.session) throw new Error("The workspace closed while adding a terminal.");
  return body.state.session;
}

/** Stop one terminal's agent and remove its pane. Returns the updated workspace. */
export async function closeTerminal(name: string): Promise<SessionState> {
  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  if (!body.state.session) throw new Error("The workspace is no longer open.");
  return body.state.session;
}

export async function setFocusMode(enabled: boolean): Promise<boolean> {
  const res = await fetch("/api/agentic-ide/mode", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { focus_mode: boolean };
  return body.focus_mode;
}

export interface PromptResult {
  terminal: string;
  /** What was actually typed into the agent. */
  sent: string;
  /** `llm` when a model wrote the prompt, `fallback` after cleanup, `raw` as typed. */
  composed_by: "llm" | "fallback" | "raw";
  /** Repo-relative files referenced with `@` in the sent prompt. */
  files: string[];
  /**
   * Did the agent actually ACCEPT the prompt? The text is typed either way; a
   * false means it is still sitting in that terminal's input box (see the
   * backend's submit verification). Never report a send as done without it.
   */
  submitted: boolean;
  /** Plain-language explanation when `submitted` is false. */
  detail?: string;
}

/**
 * Send an instruction to one terminal.
 *
 * `compose` asks the backend to rewrite a rough instruction into a briefed
 * prompt with `@file` references attached. The typed prompt bar leaves it off —
 * someone who typed a prompt already wrote what they meant, and silently
 * rewriting it would be the wrong kind of helpful. Spoken instructions take the
 * composed path.
 */
export async function promptTerminal(
  name: string,
  prompt: string,
  options: { compose?: boolean } = {},
): Promise<PromptResult> {
  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/prompt`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, compose: Boolean(options.compose) }),
    },
  );
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as PromptResult;
}
