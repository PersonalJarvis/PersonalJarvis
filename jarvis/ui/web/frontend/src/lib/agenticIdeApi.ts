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
  /** Which subscription this pane runs on (see agentAccountsApi). */
  account?: string | null;
  /** Its display name, so the pane header can show it without a second lookup. */
  account_label?: string | null;
  /**
   * What this pane is doing, in one clause — the pane header's label.
   *
   * Only the OPENING value: the state is fetched when the workspace changes,
   * and a recap goes stale in seconds. `fetchTerminalRecaps` keeps it current.
   */
  recap?: string;
  /** The one-or-two-sentence version, shown when the header line is hovered. */
  recap_detail?: string;
}

/**
 * Who wrote the recap on screen.
 *
 * `"user"` outranks both machines: a pane the user has labelled themselves
 * keeps that label until they clear it.
 */
export type RecapSource = "user" | "model" | "heuristic";

/**
 * Why this recap and not a better one.
 *
 * The field exists because "the recap is thin and nobody knows why" was the
 * actual complaint: every value here used to be a silent early return in the
 * backend's scheduler, and the card turns it into a sentence.
 */
export type RecapReason =
  | "pinned"
  | "summarized"
  | "disabled"
  | "not_started"
  | "warming"
  | "working"
  | "queued"
  | "unavailable"
  | "";

/** One pane's live recap, as `/recaps` reports it. */
export interface TerminalRecap {
  key: string;
  name: string;
  status: string;
  recap: string;
  recap_detail: string;
  source?: RecapSource;
  reason?: RecapReason;
  /** The model that wrote it, when one did. */
  writer?: string;
  /** What went wrong the last time this pane was summarized. */
  note?: string;
  /** When the model wrote it, or when the user did. 0 for the derived one. */
  generated_at?: number;
}

export interface RecapsResponse {
  /** Which workspace answered; null when none is on screen. */
  workspace_id: string | null;
  terminals: TerminalRecap[];
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

export interface ResumeWorkspaceOffer {
  session_id: string;
  folder: string;
  folder_name: string;
  /** The label the user gave this tab, empty when never renamed. */
  name: string;
  folder_exists: boolean;
  /** False when the folder is gone or none of its coding CLIs are installed. */
  available: boolean;
  /** How many of its panes bring their conversation back. */
  resumable_count: number;
  /** When THIS workspace was last open — not the file's stamp. Absent on an older backend. */
  saved_at?: number;
  /**
   * True when it was open at the last save, so resuming reopens it. False for a
   * folder that is only remembered from an earlier session. Absent on an older
   * backend, which reopened everything — so absent reads as true.
   */
  in_last_session?: boolean;
  terminals: ResumeTerminalOffer[];
}

export interface ResumeOffer {
  available: boolean;
  saved_at: number;
  /** Counts describe the LAST session — what resuming actually reopens. */
  workspace_count: number;
  terminal_count: number;
  resumable_count: number;
  /** Remembered folders from earlier sessions, which resuming does NOT reopen. */
  earlier_count?: number;
  workspaces: ResumeWorkspaceOffer[];
}

export interface ResumeResult {
  /** The whole workspace state after reopening — bar included. */
  state: IdeState;
  workspace_count: number;
  terminal_count: number;
  /** Panes that continued their conversation. */
  resumable_count: number;
  /** Panes that came back with the right name and an empty history. */
  started_fresh: number;
  /** Workspaces that could not come back, with a reason each. */
  skipped: { folder: string; detail: string }[];
}

/**
 * One pane that came back holding its conversation and was never restarted.
 *
 * The state a restart leaves behind: resuming reconnects a pane to the
 * conversation it was having, but a coding CLI launched on an old transcript
 * reads it and then waits at its prompt. So the agent knows everything about the
 * job it was halfway through and does nothing with it — which on screen is
 * indistinguishable from a pane that finished.
 */
export interface InterruptedPane {
  workspace_id: string;
  /** The workspace tab it belongs to — a list can span several. */
  workspace: string;
  folder: string;
  key: string;
  name: string;
  agent: string;
  display_name: string;
  status: string;
  /** False when its agent is not running: an instruction cannot be typed into it. */
  continuable: boolean;
  /** Why not, in one sentence. Empty when it can be continued. */
  blocked_reason: string;
  /** What it was last asked to do. Empty when that instruction was typed in by hand. */
  last_task: string;
  prompts_sent: number;
  started_at: number | null;
}

export interface InterruptedOffer {
  count: number;
  continuable_count: number;
  /** The instruction the continue action sends — "continue" unless configured. */
  prompt: string;
  panes: InterruptedPane[];
}

export interface ContinueResult {
  ok: boolean;
  /** Panes that accepted the instruction and started. */
  continued: string[];
  /**
   * Panes the text was typed into without a confirmed submit — the prompt may be
   * sitting in the input box. Reporting these as running is the one wrong thing
   * to do with this answer.
   */
  unconfirmed: string[];
  failed: { name: string; detail: string }[];
  /** Interrupted panes still waiting afterwards. */
  remaining: number;
}

export interface TerminalPlan {
  agent: string;
  name?: string;
  /** Which registered subscription to open on; omitted uses the active one. */
  account?: string;
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

/**
 * What each pane of a workspace is doing right now.
 *
 * Deliberately its own read rather than part of `fetchIdeState`: the layout
 * changes when a pane is opened or closed, the recaps change whenever an agent
 * prints a line. Polling the full state often enough for the second would
 * re-send the whole workspace to update one sentence.
 */
export function fetchTerminalRecaps(
  workspaceId?: string,
): Promise<RecapsResponse> {
  const query = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : "";
  return getJson<RecapsResponse>(`/api/agentic-ide/recaps${query}`);
}

function recapUrl(name: string, workspaceId?: string, suffix = ""): string {
  const query = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : "";
  return `/api/agentic-ide/terminals/${encodeURIComponent(name)}/recap${suffix}${query}`;
}

async function recapCall(
  url: string,
  init: RequestInit,
): Promise<TerminalRecap> {
  const res = await fetch(url, { cache: "no-store", ...init });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as TerminalRecap;
}

/**
 * Write a pane's recap yourself.
 *
 * Neither the model nor the string rules can know what YOU are keeping a pane
 * for — "the branch I'm about to demo", "leave this one alone". What is written
 * here wins over both and stops the background summarizer re-describing that
 * pane until it is cleared.
 */
export function setTerminalRecap(
  name: string,
  recap: string,
  recapDetail: string,
  workspaceId?: string,
): Promise<TerminalRecap> {
  return recapCall(recapUrl(name, workspaceId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recap, recap_detail: recapDetail }),
  });
}

/** Drop a hand-written recap and hand the pane back to the automatic one. */
export function clearTerminalRecap(
  name: string,
  workspaceId?: string,
): Promise<TerminalRecap> {
  return recapCall(recapUrl(name, workspaceId), { method: "DELETE" });
}

/**
 * Read the pane again and write a fresh recap, waiting for it.
 *
 * The background summarizer is lazy on purpose — right for a header nobody is
 * looking at, wrong the moment somebody is. This skips its cooldown. It never
 * fails because of the model: no key or an unreachable provider comes back as
 * the derived recap with `reason: "unavailable"`.
 */
export function refreshTerminalRecap(
  name: string,
  workspaceId?: string,
): Promise<TerminalRecap> {
  return recapCall(recapUrl(name, workspaceId, "/refresh"), { method: "POST" });
}

export function fetchFolders(path?: string | null): Promise<FoldersResponse> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return getJson<FoldersResponse>(`/api/agentic-ide/folders${query}`);
}

export function searchFolders(
  query: string,
  limit = 40,
): Promise<SearchResponse> {
  const qs = new URLSearchParams({ q: query, limit: String(limit) });
  return getJson<SearchResponse>(
    `/api/agentic-ide/folders/search?${qs.toString()}`,
  );
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
export async function openNativePicker(
  start?: string | null,
): Promise<NativePickResult> {
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
  return (
    body.state ?? { ...EMPTY_IDE_STATE, active: true, session: body.session }
  );
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

/** Rename one workspace tab without touching its folder or running agents. */
export async function renameWorkspace(
  id: string,
  name: string,
): Promise<IdeState> {
  const res = await fetch(
    `/api/agentic-ide/workspaces/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    },
  );
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
  /** Subscription for the new pane; omitted inherits the anchor's. */
  account?: string;
}): Promise<SessionState> {
  const res = await fetch("/api/agentic-ide/terminals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  if (!body.state.session)
    throw new Error("The workspace closed while adding a terminal.");
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

export interface CloseTerminalsResult {
  closed: string[];
  failed: Array<{ name: string; detail: string }>;
  session: SessionState;
}

/** Stop several agents through the dangerous batch route and return canonical state. */
export async function closeTerminals(names: string[]): Promise<CloseTerminalsResult> {
  const res = await fetch("/api/agentic-ide/terminals/close-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as {
    closed: string[];
    failed: Array<{ name: string; detail: string }>;
    state: IdeState;
  };
  if (!body.state.session) throw new Error("The workspace is no longer open.");
  return {
    closed: body.closed ?? [],
    failed: body.failed ?? [],
    session: body.state.session,
  };
}

/**
 * Which panes are waiting to be told to carry on, across every open workspace.
 *
 * Its own read rather than part of `fetchIdeState`: the answer changes only when
 * a pane is resumed or driven again, and folding it into the state poll would
 * re-send every workspace to update a number.
 */
export function fetchInterrupted(): Promise<InterruptedOffer> {
  return getJson<InterruptedOffer>("/api/agentic-ide/interrupted");
}

/**
 * Tell interrupted panes to carry on. No names means every one of them.
 *
 * `prompt` overrides the default "continue" — the agent still holds its whole
 * conversation, so short beats elaborate.
 */
export async function continueInterrupted(
  names?: string[],
  prompt?: string,
): Promise<ContinueResult> {
  const res = await fetch("/api/agentic-ide/interrupted/continue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names: names ?? [], prompt: prompt ?? "" }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as Partial<ContinueResult>;
  return {
    ok: body.ok ?? false,
    continued: body.continued ?? [],
    unconfirmed: body.unconfirmed ?? [],
    failed: body.failed ?? [],
    remaining: body.remaining ?? 0,
  };
}

/** The active subscription per coding CLI, without the whole workspace state. */
export async function fetchIdeAccounts(): Promise<IdeAccountState[]> {
  const body = await getJson<{ accounts: IdeAccountState[] }>(
    "/api/agentic-ide/accounts",
  );
  return body.accounts ?? [];
}

/**
 * Switch which subscription NEW terminals of one coding CLI open on.
 *
 * Panes that are already open keep the account they started with — a running
 * agent must never be moved onto a plan whose history has never seen its
 * conversation. Returns the whole workspace state, so the caller never has to
 * re-read to find out what it just changed.
 */
export async function setIdeActiveAccount(
  agent: string,
  accountId: string,
): Promise<IdeState> {
  const res = await fetch("/api/agentic-ide/accounts/active", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent, account_id: accountId }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  return body.state;
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
  payload: {
    files?: File[];
    paths?: string[];
    note?: string;
    submit?: boolean;
  },
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
   * Did the agent actually ACCEPT the prompt? Three answers, because there are
   * three: `true` it started, `false` the text is provably still sitting in
   * that terminal's input box, `null` the pane never visibly took it so nobody
   * can say (a pane still booting can swallow a paste whole). Only `true` means
   * the instruction is running — never report a send as done without it.
   */
  submitted: boolean | null;
  /** Plain-language explanation whenever `submitted` is not `true`. */
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
