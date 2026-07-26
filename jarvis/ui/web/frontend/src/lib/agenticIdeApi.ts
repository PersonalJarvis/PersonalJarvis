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

/**
 * One open workspace, as the workspace bar shows it.
 *
 * Deliberately not a whole `SessionState`: the bar renders a name and a couple
 * of numbers, and carrying six full project profiles plus every pane's
 * transcript statistics to do that would make every poll expensive.
 */
export interface WorkspaceCard {
  id: string;
  folder: string;
  /** Project name — what the tab is labelled with. */
  name: string;
  branch: string | null;
  terminals: number;
  /** Panes whose agent is running right now — a background tab's honest count. */
  live_terminals: number;
  focus_mode: boolean;
  created_at: number;
  last_active_at: number;
  active: boolean;
}

export interface IdeState {
  active: boolean;
  session: SessionState | null;
  max_terminals: number;
  /** Every open workspace, in tab order. */
  workspaces: WorkspaceCard[];
  /** The one on screen, or null while the wizard is showing. */
  active_id: string | null;
  max_workspaces: number;
}

/** One pane of the workspace being offered back after a close or a restart. */
export interface ResumeTerminalOffer {
  key: string;
  name: string;
  agent: string;
  display_name: string;
  column: number;
  slot: number;
  /** Can this pane open at all? False when its coding CLI is gone from this machine. */
  available: boolean;
  /**
   * Does its CONVERSATION come back, or only its call-sign?
   *
   * The distinction is the whole point of showing this before the click: a pane
   * that reopens empty looks exactly like one that continued, right up until it
   * is asked a follow-up question.
   */
  resumable: boolean;
  prompts_sent: number;
}

export interface ResumeOffer {
  available: boolean;
  folder: string;
  folder_name: string;
  folder_exists: boolean;
  saved_at: number;
  session_id: string;
  resumable_count: number;
  terminals: ResumeTerminalOffer[];
}

export interface ResumeResult {
  session: SessionState;
  /** Panes that continued their conversation. */
  resumable_count: number;
  /** Panes that came back with the right name and an empty history. */
  started_fresh: number;
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

export interface NativePickerSupport {
  available: boolean;
  backend?: string | null;
  reason?: string | null;
}

export interface NativePickResult {
  path?: string | null;
  cancelled?: boolean;
  error?: string | null;
}

/**
 * Whether this machine can show the operating system's own folder window.
 *
 * Asked before the button is offered rather than after it is pressed: the
 * window opens where the SERVER runs, so from a phone or another laptop it
 * would appear on a screen nobody is watching. A `false` here always comes with
 * a `reason` worth showing.
 */
export function fetchNativePickerSupport(): Promise<NativePickerSupport> {
  return getJson<NativePickerSupport>("/api/agentic-ide/folders/native");
}

/**
 * Open the system folder window and wait for an answer.
 *
 * The request stays open for as long as the window does — that is the point,
 * not a hang. Cancelling comes back as `cancelled`, never as an error.
 */
export async function openNativePicker(start?: string | null): Promise<NativePickResult> {
  const res = await fetch("/api/agentic-ide/folders/native", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start: start ?? null }),
  });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as NativePickResult;
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

/**
 * Open `folder` as another workspace and return the state that results.
 *
 * The whole state, not just the new session: opening ADDS a workspace, so the
 * bar changes too, and answering with both means the view never has to re-read
 * to find out what it just did. A second fetch would also be a race — it can
 * return a snapshot from before the open and blank the workspace that was just
 * created.
 */
export async function startIdeSession(
  folder: string,
  terminals: TerminalPlan[],
): Promise<IdeState> {
  const res = await fetch("/api/agentic-ide/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, terminals }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { session: SessionState; state: IdeState };
  // `state` is authoritative; `session` alone is kept as the fallback for a
  // backend that predates the workspace bar.
  return body.state ?? { ...EMPTY_IDE_STATE, active: true, session: body.session };
}

/** Shape a pre-workspace-bar backend does not send. */
const EMPTY_IDE_STATE: IdeState = {
  active: false,
  session: null,
  max_terminals: 12,
  workspaces: [],
  active_id: null,
  max_workspaces: 6,
};

export async function endIdeSession(): Promise<void> {
  const res = await fetch("/api/agentic-ide/session", { method: "DELETE" });
  if (!res.ok) throw new Error(await detail(res));
}

export interface WorkspacesResponse {
  workspaces: WorkspaceCard[];
  active_id: string | null;
  max_workspaces: number;
}

/** Every open workspace, in tab order, with the front one marked. */
export function fetchWorkspaces(): Promise<WorkspacesResponse> {
  return getJson<WorkspacesResponse>("/api/agentic-ide/workspaces");
}

/**
 * Bring one workspace to the front, or clear the front entirely.
 *
 * Nothing starts, stops or restarts — the agents in every open workspace keep
 * working and the one that comes forward reconnects to the processes that were
 * running all along.
 *
 * `null` means "show no workspace": the state the view is in while the wizard
 * opens an ADDITIONAL one. It has to be sent BEFORE the outgoing panes unmount,
 * which is why it is awaited rather than fired off — see AgenticIdeView.
 */
export async function activateWorkspace(id: string | null): Promise<IdeState> {
  const res = await fetch("/api/agentic-ide/workspaces/active", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  return body.state;
}

/** Close ONE workspace and stop every agent in it. Returns the state that is left. */
export async function closeWorkspace(id: string): Promise<IdeState> {
  const res = await fetch(
    `/api/agentic-ide/workspaces/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  return body.state;
}

/** What reopening the last workspace would bring back, checked against this machine. */
export function fetchResumeOffer(): Promise<ResumeOffer> {
  return getJson<ResumeOffer>("/api/agentic-ide/resume");
}

/**
 * Reopen the last workspace — same panes, same places, same coding CLIs.
 *
 * Nothing is started here: the panes connect the way they always do, and that
 * connection is what continues each conversation.
 */
export async function resumeWorkspace(): Promise<ResumeResult> {
  const res = await fetch("/api/agentic-ide/resume", { method: "POST" });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as ResumeResult;
}

/** Throw the restore point away, so the IDE opens to a clean wizard. */
export async function forgetResumeOffer(): Promise<void> {
  const res = await fetch("/api/agentic-ide/resume", { method: "DELETE" });
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

export interface AttachResult {
  terminal: string;
  /** What was typed into the pane — `@path` for Claude Code, a quoted path otherwise. */
  references: string[];
  /** File names now in front of the agent. */
  files: string[];
  /** How many had to be copied into the workspace (the rest were already there). */
  copied: number;
  submitted: boolean;
}

/**
 * Put dropped or pasted files in front of the agent in one pane.
 *
 * Two inputs because a browser gives you one or the other, never reliably both:
 * `paths` are the real locations an Explorer/Finder drag usually carries, and
 * `files` are raw bytes for everything else (a pasted screenshot has no path at
 * all). Sending both lets the backend skip copying whatever already exists.
 */
export async function attachToTerminal(
  name: string,
  payload: { files?: File[]; paths?: string[]; note?: string; submit?: boolean },
): Promise<AttachResult> {
  const form = new FormData();
  for (const file of payload.files ?? []) form.append("files", file, file.name);
  if (payload.paths?.length) form.append("paths", payload.paths.join("\n"));
  if (payload.note) form.append("note", payload.note);
  if (payload.submit) form.append("submit", "true");

  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/attach`,
    { method: "POST", body: form },
  );
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as AttachResult;
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
 * prompt with `@file` references attached. The typed prompt bar sends with
 * `compose` OFF here — it composes in a separate step first (see
 * `composePrompt`) so the user approves the rewrite before it is typed into
 * their agent. Silently rewriting what someone typed would be the wrong kind
 * of helpful; showing it and asking is not.
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

export interface ComposedPreview {
  /** The briefed markdown prompt, ready to send. */
  composed: string;
  /** `llm` when a model wrote it, `fallback` when no capable model was up. */
  composed_by: "llm" | "fallback" | "raw";
  /** Repo-relative files the prompt references with `@`. */
  files: string[];
}

/**
 * Build the briefed prompt for `name` WITHOUT sending it.
 *
 * Same composition the spoken path uses, stopped one step short so the user can
 * read it. Takes as long as one model call plus reading a few files — the
 * caller should show that it is working.
 */
export async function composePrompt(
  name: string,
  prompt: string,
): Promise<ComposedPreview> {
  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/prompt`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, compose: true, dry_run: true }),
    },
  );
  if (!res.ok) throw new Error(await detail(res));
  const data = (await res.json()) as {
    composed: string;
    composed_by: ComposedPreview["composed_by"];
    files: string[];
  };
  return {
    composed: data.composed,
    composed_by: data.composed_by,
    files: data.files ?? [],
  };
}
