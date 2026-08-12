// REST client for the Agentic IDE. Plain same-origin fetch (mirrors
// workspaceApi/chatsApi), `no-store` everywhere because WebView2 happily serves
// a stale folder listing or terminal state from cache otherwise.

// Type-only: the split tree's wire form is defined next to the code that lays
// it out, and the session state simply carries it.
import type { LayoutNode } from "../components/agentic/treeLayout";

export interface AgentStatus {
  name: string;
  display_name: string;
  installed: boolean;
  version: string | null;
  install_command: string | null;
  /**
   * What opening this entry gives you: `"cli"` a coding agent, `"shell"` a
   * plain terminal on this machine's own shell. The UI branches on this rather
   * than on the entry's name, so a CLI registered later needs no change here.
   */
  kind?: string;
  /** One line a picker can show under the name. */
  description?: string;
  /**
   * True for a CLI the user added themselves (`workspaceClisApi`). Surfaces use
   * it to offer editing or removing the entry, which makes no sense for one
   * this app ships.
   */
  custom?: boolean;
  /**
   * Where to fetch this entry's mark. Empty for a shipped entry — whose logo is
   * an asset this bundle already carries — and for a custom one with no logo,
   * which falls back to a monogram.
   */
  logo_url?: string;
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

export interface WorkspaceFileItem {
  name: string;
  /** POSIX-style path relative to the workspace root. */
  path: string;
  is_directory: boolean;
  is_symlink: boolean;
  size?: number | null;
}

export interface WorkspaceFilesResponse {
  workspace_id: string;
  root_name: string;
  path: string;
  entries: WorkspaceFileItem[];
  truncated: boolean;
  error?: string | null;
}

export interface WorkspaceFilePreviewResponse {
  workspace_id: string;
  path: string;
  name: string;
  size: number;
  media_type: string;
  text: string | null;
  truncated: boolean;
  hex_preview: string | null;
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
  /** Stable for one pane lifetime, even when its reusable call-sign is recycled. */
  history_id?: string;
  name: string;
  agent: string;
  display_name: string;
  /**
   * Can Jarvis type into this pane? False for a plain terminal — that is a
   * shell prompt, and an injected line would RUN rather than be read, so the
   * prompt bar leaves those panes out instead of offering a target that
   * refuses everything sent to it.
   */
  accepts_prompts?: boolean;
  index: number;
  /**
   * Coarse "roughly which column" hint, derived from the workspace's split
   * tree. The GEOMETRY is `SessionState.layout`; these two numbers survive
   * for consumers that only talk about the grid (the resume offer's dots).
   */
  column: number;
  /** Coarse position within that column, top to bottom — same caveat. */
  slot: number;
  status: "pending" | "live" | "exited" | "error";
  exit_code: number | null;
  error: string;
  started_at: number | null;
  last_output_at: number | null;
  idle_seconds: number | null;
  prompts_sent: number;
  /** The opening of the last delivered prompt — an excerpt, not the whole brief. */
  last_prompt: string;
  /** Its true length, so an excerpt is never presented as the whole thing. */
  last_prompt_chars?: number;
  /**
   * When this pane was last handed a prompt (epoch seconds), null if never.
   *
   * The durable half of the delivery receipt. A pane proves a prompt arrived
   * by echoing it, and that proof is absent in precisely the situations where
   * it is needed — output parked while the pane was off screen, an emulator
   * that never painted, a socket reconnecting, a CLI redrawing its input box
   * out of view. This is read at mount and at every poll, so the receipt is
   * still there for someone who looks a quarter of an hour later.
   */
  last_prompt_at?: number | null;
  /** Did the last prompt start the agent, or is it still in the input box? */
  submitted?: boolean | null;
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
  /** The one-or-two-sentence version, shown when the header line is opened. */
  recap_detail?: string;
  /** Is its agent still on the job? The OPENING value, like `recap` above. */
  activity?: PaneActivity;
  /** When it entered that state (epoch seconds); 0 when unknown. */
  activity_since?: number;
  /** Has anything ever been asked of this pane? */
  worked?: boolean;
}

/**
 * Whether a pane's agent is still on the job.
 *
 * Deliberately NOT the same question as `TerminalState.status`, which is about
 * the pipe: a pane can be perfectly "live" and have been finished for twenty
 * minutes, and that gap is the whole reason this exists.
 *
 * The backend derives it from the terminal SCREEN — a pane whose picture keeps
 * changing is working, one that stands still has stopped — so it holds for every
 * coding CLI a pane can run, including ones connected later. It knows nothing
 * about what any product prints. See `jarvis/agentic_ide/activity.py`.
 *
 * `""` means "no answer for this pane": a plain terminal is a shell prompt, not
 * an agent, so it has no job to be in the middle of.
 */
export type PaneActivity =
  | "starting"
  | "working"
  | "waiting"
  | "asking"
  | "failed"
  | "exited"
  | "";

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

/**
 * What one pane is DOING, and nothing about what it is doing it about.
 *
 * The skinny base of `TerminalRecap`, and the whole answer of the fast poll
 * behind the status badge: the recap poll also schedules a summarizer pass
 * per pane, so it runs on a relaxed clock — and a badge that only learns
 * "working" on that clock trails the pane it describes by several seconds.
 * This row is one stamped word per pane and is cheap enough to poll fast.
 * One declaration for both rows, so the two polls cannot drift apart about
 * what these fields mean.
 */
export interface TerminalActivityRow {
  key: string;
  name: string;
  /** The pane's process status: `pending`, `live`, `exited` or `error`. */
  status: string;
  /**
   * Is its agent still on the job, or has it stopped? What keeps the pane
   * list current: what the pane is DOING, beside what it is doing it about.
   */
  activity?: PaneActivity;
  /** When it entered that state (epoch seconds); 0 when unknown. */
  activity_since?: number;
  /**
   * Has anything ever been asked of this pane — an instruction sent to it, or
   * the conversation it resumed?
   *
   * What separates "this agent finished" from "nobody has asked this terminal
   * for anything" — the same still screen, and not the same news.
   */
  worked?: boolean;
}

/** One pane's live recap, as `/recaps` reports it: the activity row plus words. */
export interface TerminalRecap extends TerminalActivityRow {
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

export interface ActivityResponse {
  /** Which workspace answered; null when none is on screen. */
  workspace_id: string | null;
  terminals: TerminalActivityRow[];
}

export interface SessionState {
  id: string;
  folder: string;
  project: ProjectProfile;
  created_at: number;
  focus_mode: boolean;
  /**
   * WHERE every pane sits and how much room it has — the split tree the grid
   * draws from (see `components/agentic/treeLayout`). Null only while the
   * workspace has no panes; absent from states sent by older backends.
   */
  layout?: LayoutNode | null;
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

/**
 * Which subscription new terminals of one coding CLI open on.
 *
 * `account_count` is what lets a surface stay quiet: with a single login there
 * is nothing to choose, and a chip answering a question nobody has is noise.
 */
export interface IdeAccountState {
  /** Backend id of the coding CLI — "claude", "codex". */
  agent: string;
  /** What the user reads — "Claude Code". */
  display_name: string;
  active_account: string | null;
  /** Its display name ("Work seat"), which is the only readable form of the id. */
  active_label: string | null;
  /** How many subscriptions are registered for this CLI, the built-in included. */
  account_count: number;
}

export interface IdeState {
  active: boolean;
  session: SessionState | null;
  max_terminals: number;
  /** Every open workspace, in tab order. */
  workspaces: WorkspaceCard[];
  /** The one on screen, or null while the wizard is showing. */
  active_id: string | null;
  /** Null means the backend leaves workspace count to the machine and user. */
  max_workspaces?: number | null;
  /** The active subscription per coding CLI. Absent on an older backend. */
  accounts?: IdeAccountState[];
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
  /**
   * Will a "continue" reach it? False only when its agent is DEAD — a pane that
   * is merely still starting IS continuable, the instruction just waits for it.
   */
  continuable: boolean;
  /**
   * Its agent is still coming up.
   *
   * Cold starts are staggered on purpose, so most of a big workspace is in this
   * state for the first seconds after it appears — which is exactly when this
   * button gets pressed. Those panes are queued, never skipped.
   */
  starting?: boolean;
  /** A "continue" is already on its way to this pane. */
  queued?: boolean;
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
   * Panes whose agent had not started yet. The instruction is held and delivered
   * when each comes up — "shortly", never "done".
   */
  queued: string[];
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

/**
 * Whether each pane's agent is still working — the status badge's fast poll.
 *
 * Split from `fetchTerminalRecaps` the way that one is split from
 * `fetchIdeState`: a recap costs a walk of the pane's transcript, this costs a
 * look at one stamped word, so this one may run every second or two while the
 * recaps keep their relaxed clock.
 */
export function fetchTerminalActivity(
  workspaceId?: string,
): Promise<ActivityResponse> {
  const query = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : "";
  return getJson<ActivityResponse>(`/api/agentic-ide/activity${query}`);
}

/** The exact prompt a pane was last sent, as `/terminals/{name}/prompt` reports it. */
export interface LastPrompt {
  name: string;
  /** The brief as it was written into the pane, unabridged. */
  text: string;
  chars: number;
  /** Epoch seconds; null when this pane has never been sent anything. */
  at: number | null;
  submitted: boolean | null;
  prompts_sent: number;
}

/**
 * Read back, word for word, what a pane was last told to do.
 *
 * The proof half of a delivery, and its own request on purpose: a composed
 * brief runs to thousands of characters, so the workspace state carries only
 * an excerpt, a length and a timestamp — enough to render the receipt. This is
 * what a user gets when they open that receipt, and it is the only form of
 * "I sent it" that can be checked rather than believed.
 */
export function fetchLastPrompt(
  name: string,
  workspaceId?: string,
): Promise<LastPrompt> {
  const query = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : "";
  return getJson<LastPrompt>(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/prompt${query}`,
  );
}

/** One exact prompt in a pane's durable delivery history. */
export interface PromptHistoryItem {
  id: string;
  sequence: number;
  text: string;
  chars: number;
  /** Epoch seconds. */
  at: number;
  submitted: boolean | null;
}

export interface PromptHistoryResponse {
  name: string;
  /** Known delivery count, including prompts from before history recording existed. */
  total: number;
  /** Exact prompt records available to inspect and copy. */
  available: number;
  complete: boolean;
  /** Newest first. */
  items: PromptHistoryItem[];
}

/** Read every exact prompt handed to this pane without loading it into workspace state. */
export function fetchPromptHistory(
  name: string,
  workspaceId?: string,
): Promise<PromptHistoryResponse> {
  const query = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : "";
  return getJson<PromptHistoryResponse>(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/prompts${query}`,
  );
}

export interface ConversationStep {
  tool: string;
  target: string;
  detail: string;
}

export interface ConversationTurn {
  role: "user" | "assistant" | string;
  text: string;
  steps: ConversationStep[];
}

export interface ConversationResponse {
  terminal: string;
  agent: string;
  /** False when this CLI's session records exist but cannot be parsed. */
  readable: boolean;
  /** False when this CLI keeps no session record to read at all. */
  available: boolean;
  turns: ConversationTurn[];
}

/**
 * The pane's conversation as the CLI itself recorded it on disk — the one
 * scroll-history source that does not depend on what the TUI happens to be
 * painting, which is why the pane history view is built on it.
 */
export function fetchTerminalConversation(
  name: string,
): Promise<ConversationResponse> {
  return getJson<ConversationResponse>(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/conversation`,
  );
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

/** Load one level of an open workspace's file tree. */
export function fetchWorkspaceFiles(
  workspaceId: string,
  path = "",
): Promise<WorkspaceFilesResponse> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return getJson<WorkspaceFilesResponse>(
    `/api/agentic-ide/workspaces/${encodeURIComponent(workspaceId)}/files${query}`,
  );
}

function workspaceFileUrlFor(
  workspaceId: string,
  path: string,
  endpoint: "file" | "file-preview",
): string {
  const query = new URLSearchParams({ path });
  return `/api/agentic-ide/workspaces/${encodeURIComponent(workspaceId)}/${endpoint}?${query.toString()}`;
}

/** A same-origin URL suitable for an image, media element, or sandboxed frame. */
export function workspaceFileUrl(workspaceId: string, path: string): string {
  return workspaceFileUrlFor(workspaceId, path, "file");
}

/** Extract a bounded, safe text or hexadecimal preview for a workspace file. */
export function fetchWorkspaceFilePreview(
  workspaceId: string,
  path: string,
): Promise<WorkspaceFilePreviewResponse> {
  return getJson<WorkspaceFilePreviewResponse>(
    workspaceFileUrlFor(workspaceId, path, "file-preview"),
  );
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

export interface OpenTerminalTargetResult {
  opened: boolean;
  kind: "file" | "directory";
  path: string;
}

/** Open a modifier-clicked path from one terminal's own workspace. */
export async function openTerminalTarget(
  workspaceId: string,
  target: string,
): Promise<OpenTerminalTargetResult> {
  const res = await fetch("/api/agentic-ide/terminal-target/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace_id: workspaceId, target }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const result = (await res.json()) as OpenTerminalTargetResult;
  if (!result.opened) {
    throw new Error("Could not open that file or folder on this computer.");
  }
  return result;
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
  max_workspaces: number | null;
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

/**
 * Persist the pane sizes a seam drag produced.
 *
 * Sends back the whole tree this client was looking at. The backend adopts
 * only the WEIGHTS, and only while the workspace still has that shape — a
 * drag that raced a voice-opened pane is quietly declined, and the returned
 * state carries the authoritative tree either way.
 */
export async function saveLayoutWeights(layout: LayoutNode): Promise<SessionState> {
  const res = await fetch("/api/agentic-ide/layout/weights", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layout }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  if (!body.state.session)
    throw new Error("The workspace closed while saving pane sizes.");
  return body.state.session;
}

/** Where a dragged pane may land relative to the pane it was dropped on. */
export type PaneMovePosition = "swap" | "left" | "right" | "above" | "below";

/**
 * Move a running pane to another place in the grid.
 *
 * Nothing is started or stopped — only the two numbers that say where a pane is
 * drawn change, which is what makes rearranging safe on a grid full of working
 * agents. The answer carries the whole workspace, so the grid redraws from this
 * call alone rather than polling for the layout it just asked for.
 */
export async function moveTerminal(
  name: string,
  target: string,
  position: PaneMovePosition = "swap",
): Promise<SessionState> {
  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/move`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, position }),
    },
  );
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  if (!body.state.session)
    throw new Error("The workspace closed while moving a terminal.");
  return body.state.session;
}

/**
 * Give one pane another call-sign.
 *
 * Nothing is started or stopped — the agent keeps working and keeps its
 * conversation. The pane simply answers to a different name afterwards, in the
 * header, in a spoken instruction, and in every route that takes a name.
 */
export async function renameTerminal(
  name: string,
  newName: string,
): Promise<SessionState> {
  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    },
  );
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { state: IdeState };
  if (!body.state.session)
    throw new Error("The workspace closed while renaming a terminal.");
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
    queued: body.queued ?? [],
    unconfirmed: body.unconfirmed ?? [],
    failed: body.failed ?? [],
    remaining: body.remaining ?? 0,
  };
}

/**
 * What happened in one pane, as the header bell lists it.
 *
 * `kind` is what the backend established from the pane's own screen, never from
 * reading the agent's prose: `completed` means it stopped drawing the interrupt
 * hint its CLI shows while busy — the terminal went quiet, which is a different
 * claim from "the work is right".
 */
export type PaneNotificationKind =
  | "completed"
  | "needs_input"
  | "exited"
  | "failed";

export interface PaneNotification {
  id: string;
  kind: PaneNotificationKind;
  /** Which workspace it happened in — the list spans every open one. */
  workspace_id: string;
  workspace: string;
  pane_key: string;
  /** The pane's call-sign — "T3". */
  pane: string;
  agent: string;
  /** What the user reads — "Claude Code". */
  display_name: string;
  title: string;
  /** What that pane was last asked to do. May be empty. */
  detail: string;
  created_at: number;
  read: boolean;
}

export interface PaneNotificationsState {
  /** False when the background sweep is switched off in jarvis.toml. */
  enabled: boolean;
  unread: number;
  notifications: PaneNotification[];
}

/** Everything the bell shows, newest first, across every open workspace. */
export async function fetchPaneNotifications(): Promise<PaneNotificationsState> {
  const body = await getJson<Partial<PaneNotificationsState>>(
    "/api/agentic-ide/notifications",
  );
  return {
    enabled: body.enabled ?? true,
    unread: body.unread ?? 0,
    notifications: body.notifications ?? [],
  };
}

/** Stop the bell counting these entries. No ids means every one of them. */
export async function markPaneNotificationsRead(ids?: string[]): Promise<number> {
  const res = await fetch("/api/agentic-ide/notifications/read", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: ids ?? [] }),
  });
  if (!res.ok) throw new Error(await detail(res));
  const body = (await res.json()) as { unread?: number };
  return body.unread ?? 0;
}

/**
 * Throw entries away — one by id, or the whole list without one.
 *
 * Nothing about the pane changes: the agent behind a discarded entry keeps
 * running exactly as it was.
 */
export async function clearPaneNotifications(id?: string): Promise<void> {
  const url = id
    ? `/api/agentic-ide/notifications/${encodeURIComponent(id)}`
    : "/api/agentic-ide/notifications";
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) throw new Error(await detail(res));
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

/**
 * Tell the backend which view is on screen, which pane it stages, and which
 * one owns the next prompt.
 *
 * Ephemeral by design: this is grounding for "this terminal", not a display
 * preference. Grid view has no single visible terminal, but its selected prompt
 * chip remains an explicit target for the prompt bar, voice orb, and file drops.
 *
 * The view travels as a NAME rather than the `chat_view` boolean it replaced:
 * a name keeps reading correctly when a third mode is added, where a boolean
 * would have to be re-interpreted everywhere it is read.
 */
export async function syncAgenticIdeSurface(payload: {
  workspaceId: string;
  view: "grid" | "chat";
  onScreen: boolean;
  terminal: string | null;
  promptTarget: string | null;
}): Promise<void> {
  const res = await fetch("/api/agentic-ide/surface-context", {
    method: "PUT",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: payload.workspaceId,
      view: payload.view,
      on_screen: payload.onScreen,
      terminal: payload.terminal,
      prompt_target: payload.promptTarget,
    }),
  });
  if (!res.ok) throw new Error(await detail(res));
}

/**
 * The workspace display preferences the backend remembers for this machine.
 *
 * `stored` is false until a size has actually been chosen — the difference
 * between "the user picked 13" and "nobody ever picked anything", which is what
 * lets an older choice living only in this page's `localStorage` be handed over
 * rather than overwritten by the default.
 */
export interface TerminalUiPreferences {
  terminal_font_size: number;
  stored: boolean;
  min: number;
  max: number;
  default: number;
}

/**
 * Read the remembered terminal text size.
 *
 * Deliberately a backend read: the desktop window is an embedded WebView that
 * starts every run with empty browser storage, so a preference kept only in
 * `localStorage` is forgotten on each restart.
 */
export function fetchTerminalUiPreferences(): Promise<TerminalUiPreferences> {
  return getJson<TerminalUiPreferences>("/api/agentic-ide/ui-preferences");
}

/** Remember a terminal text size until it is changed again. */
export async function saveTerminalFontSize(
  size: number,
): Promise<TerminalUiPreferences> {
  const res = await fetch("/api/agentic-ide/ui-preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ terminal_font_size: size }),
  });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as TerminalUiPreferences;
}

/**
 * One dropped file after the backend has read what is actually in it.
 *
 * `detail` is the whole point: for an image it is a description written by a
 * model that could see it, for a document the extracted text. That is what
 * makes dropping a screenshot useful against a coding agent that cannot open
 * one — the file reference alone would leave it guessing.
 */
export interface DropAttachment {
  name: string;
  /** How the agent should refer to the file — `@path` or a quoted path. */
  reference: string;
  kind: "image" | "text" | "pdf" | "other";
  /** The description or extracted text. Empty when neither could be produced. */
  detail: string;
  /** Which layer produced `detail`. */
  described_by: "vision" | "extraction" | "none";
  /** Why `detail` is empty or shortened. Empty on the happy path. */
  note: string;
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
  /** False when `deliver: false` held the files back instead of typing them. */
  delivered?: boolean;
  /** Present only when `analyze` was asked for. */
  analysis?: DropAttachment[];
  /** Analysed files queued for this pane's next spoken prompt. */
  staged_for_voice?: number;
  /** Stable identity used to verify or cancel the pending orb drop. */
  voice_batch_id?: string | null;
}

export interface VoiceAttachmentBatch {
  batch_id: string;
  files: string[];
  reserved: boolean;
}

export interface OwnedVoiceAttachmentBatch extends VoiceAttachmentBatch {
  terminal: string;
}

export interface VoiceAttachmentsResponse {
  terminal: string;
  batches: VoiceAttachmentBatch[];
}

export interface AllVoiceAttachmentsResponse {
  batches: OwnedVoiceAttachmentBatch[];
}

/**
 * Put dropped or pasted files in front of the agent in one pane.
 *
 * Two inputs because a browser gives you one or the other, never reliably both:
 * `paths` are the real locations an Explorer/Finder drag usually carries, and
 * `files` are raw bytes for everything else (a pasted screenshot has no path at
 * all). Sending both lets the backend skip copying whatever already exists.
 *
 * `analyze` and `deliver` are what separate a drop on a PANE from a drop on the
 * prompt bar. A pane wants the path typed and nothing else. The prompt bar wants
 * the file read — described or extracted — and held, because the user is still
 * writing the sentence that explains it.
 */
export async function attachToTerminal(
  name: string,
  payload: {
    files?: File[];
    paths?: string[];
    note?: string;
    submit?: boolean;
    /** Read the files' contents and return them under `analysis`. */
    analyze?: boolean;
    /** Default true. False stores and analyses without typing into the pane. */
    deliver?: boolean;
    /** Queue the analysis for this pane's next spoken prompt. */
    stageForVoice?: boolean;
  },
): Promise<AttachResult> {
  const form = new FormData();
  for (const file of payload.files ?? []) form.append("files", file, file.name);
  if (payload.paths?.length) form.append("paths", payload.paths.join("\n"));
  if (payload.note) form.append("note", payload.note);
  if (payload.submit) form.append("submit", "true");
  if (payload.analyze) form.append("analyze", "true");
  if (payload.deliver === false) form.append("deliver", "false");
  if (payload.stageForVoice) form.append("stage_for_voice", "true");

  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/attach`,
    { method: "POST", body: form },
  );
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as AttachResult;
}

/** Read authoritative orb drops still waiting for one pane. */
export function fetchVoiceAttachments(
  name: string,
): Promise<VoiceAttachmentsResponse> {
  return getJson<VoiceAttachmentsResponse>(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/voice-attachments`,
  );
}

/** Hydrate every pending orb receipt after navigation or a panel remount. */
export function fetchAllVoiceAttachments(): Promise<AllVoiceAttachmentsResponse> {
  return getJson<AllVoiceAttachmentsResponse>(
    "/api/agentic-ide/voice-attachments",
  );
}

/** Cancel one pending orb drop; the copied workspace file is left intact. */
export async function removeVoiceAttachment(
  name: string,
  batchId: string,
): Promise<void> {
  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/voice-attachments/${encodeURIComponent(batchId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await detail(res));
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
 * prompt with `@file` references attached before typing it in. The typed
 * prompt bar sends with `compose` ON — the identical treatment a spoken
 * "prompt Mika …" gets, in the same single request. An approval preview used
 * to sit between the two steps; the maintainer retired it (2026-08-12)
 * because its fallback paths delivered the raw text.
 *
 * `attachments` travel with the instruction either way. With `compose` the
 * backend folds their descriptions into the brief it writes; without it the
 * contents are appended underneath the caller's own words.
 */
export async function promptTerminal(
  name: string,
  prompt: string,
  options: { compose?: boolean; attachments?: DropAttachment[] } = {},
): Promise<PromptResult> {
  const res = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(name)}/prompt`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        compose: Boolean(options.compose),
        attachments: options.attachments ?? [],
      }),
    },
  );
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as PromptResult;
}

// --------------------------------------------------------- prompt writer
/** One choice for who writes Agentic-IDE task briefs. */
export type PromptWriterOption = {
  id: string;
  label: string;
  /** False when this option cannot write right now — an unconnected CLI, or
   *  a Tool Model that is unset or missing its key. The picker disables it
   *  rather than hiding it, because "why can I not pick that" is the question
   *  the user needs answered on this screen. */
  connected: boolean;
};

export type PromptWriterState = {
  prompt_writer: string;
  options: PromptWriterOption[];
};

export function fetchPromptWriter(): Promise<PromptWriterState> {
  return getJson<PromptWriterState>("/api/agentic-ide/prompt-writer");
}

/** Persist who writes the briefs. Rejects with the server's own reason — it
 *  names the actual blocker (a CLI that is not signed in, a Tool Model with no
 *  key), which is what the user has to act on. */
export async function savePromptWriter(id: string): Promise<PromptWriterState> {
  const res = await fetch("/api/agentic-ide/prompt-writer", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_writer: id }),
  });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as PromptWriterState;
}
