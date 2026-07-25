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
  note: string;
}

export interface TerminalState {
  key: string;
  name: string;
  agent: string;
  display_name: string;
  index: number;
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

export async function promptTerminal(name: string, prompt: string): Promise<void> {
  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/prompt`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    },
  );
  if (!res.ok) throw new Error(await detail(res));
}
