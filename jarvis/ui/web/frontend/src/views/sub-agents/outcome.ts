/**
 * What happened to one agent run — derived from its mission event stream.
 *
 * The board row can only say "Failed". The durable event stream behind it
 * says WHY: which worker was killed and for what reason, what the provider
 * answered, what the reviewer objected to, how many revision rounds ran, and
 * what was left behind. This module reads that stream ONCE into two plain
 * shapes the insight page renders:
 *
 * - `deriveOutcome` — the verdict: terminal event, reason, error class, the
 *   upstream error text, kills, the last review verdict and correction, the
 *   workers that ran. Everything a "what went wrong" paragraph needs.
 * - `buildStory` — the run as it unfolded: dispatch → plan → worker spawn →
 *   the worker's own narration and tool calls (from `WorkerProgress` notes)
 *   → draft → review → correction → kill → terminal. State-machine chatter
 *   (PENDING→RUNNING, RUNNING→CRITIQUING) is dropped: the entries around it
 *   already tell that part of the story.
 *
 * Pure functions over the typed envelopes in `types/missions.ts`; no fetch,
 * no store, so every branch is unit-testable with hand-built events.
 */
import type {
  CriticVerdictReady,
  EventEnvelope,
  MissionFailed,
  WorkerCorrectionRequired,
  WorkerKilled,
} from "@/types/missions";

export type Terminal = "approved" | "failed" | "cancelled" | "timed_out";

export interface WorkerLane {
  worker_id: string;
  iteration: number;
  cli: string;
  model: string;
  session_id: string | null;
  spawned_ms: number;
  ended_ms: number | null;
  /** The `WorkerKilled.reason` when the worker was killed, else null. */
  ended_reason: string | null;
  /** Narration + tool notes this worker reported. */
  notes: number;
  tool_notes: number;
}

export interface AgentOutcome {
  terminal: Terminal | null;
  /** Raw mission-level reason (`task_error`, `critic_loop_exhausted`, …). */
  reason: string | null;
  error_class: string | null;
  error_detail: string | null;
  failed_provider: string | null;
  last_state: string | null;
  partial_artifacts: string[];
  /** The approved run's summary in the mission's own language. */
  summary: string | null;
  result_uri: string | null;
  cascade: boolean;
  deadline_ms: number | null;
  last_progress_ms: number | null;
  wall_ms: number | null;
  tokens_used: number;
  cost_usd: number;
  kills: WorkerKilled[];
  verdicts: CriticVerdictReady[];
  lastVerdict: CriticVerdictReady | null;
  lastCorrection: WorkerCorrectionRequired | null;
  workers: WorkerLane[];
  revisions: number;
  planWorkers: number | null;
  expectedOutput: string | null;
}

export type StoryKind =
  | "dispatched"
  | "plan"
  | "spawn"
  | "narration"
  | "tool"
  | "draft"
  | "verdict"
  | "correction"
  | "killed"
  | "budget"
  | "approved"
  | "failed"
  | "cancelled"
  | "timed_out";

export type StoryTone = "neutral" | "busy" | "ok" | "warn" | "error";

export interface StoryEntry {
  id: string;
  ts_ms: number;
  kind: StoryKind;
  tone: StoryTone;
  /** The raw text the entry carries: a note, a summary, an instruction, an error. */
  text: string;
  worker_id: string | null;
  iteration: number | null;
  /** Tool name for `tool` entries (`Read`, `Grep`, `PowerShell` …). */
  tool: string | null;
  /** Extra facts to print as small labels (cli, model, reason, …). */
  meta: Record<string, string>;
  verdict: CriticVerdictReady | null;
}

/** Mission-level failure reasons the orchestrator emits → i18n key. */
export const REASON_LABEL_KEYS: Readonly<Record<string, string>> = {
  task_error: "subagents_view.reason.task_error",
  attempts_timed_out: "subagents_view.reason.attempts_timed_out",
  budget_exceeded: "subagents_view.reason.budget_exceeded",
  worktree_setup_failed: "subagents_view.reason.worktree_setup_failed",
  critic_unavailable: "subagents_view.reason.critic_unavailable",
  critic_rejected: "subagents_view.reason.critic_rejected",
  review_time_budget_exhausted: "subagents_view.reason.review_time_budget_exhausted",
  critic_loop_exhausted: "subagents_view.reason.critic_loop_exhausted",
  decompose_failed: "subagents_view.reason.decompose_failed",
  interrupted: "subagents_view.reason.interrupted",
  mission_timed_out: "subagents_view.reason.mission_timed_out",
  ui_cancel: "subagents_view.reason.ui_cancel",
  user_cancelled: "subagents_view.reason.ui_cancel",
  parent_cancelled: "subagents_view.reason.parent_cancelled",
};

/** `WorkerKilled.reason` vocabulary → i18n key. */
export const KILL_REASON_LABEL_KEYS: Readonly<Record<string, string>> = {
  timeout: "subagents_view.kill.timeout",
  user: "subagents_view.kill.user",
  budget: "subagents_view.kill.budget",
  parent_cancelled: "subagents_view.kill.parent_cancelled",
  injection_detected: "subagents_view.kill.injection_detected",
  path_guard: "subagents_view.kill.path_guard",
  worker_error: "subagents_view.kill.worker_error",
};

/**
 * A mission reason may carry a detail after a colon (`decompose_failed: …`).
 * The label is looked up on the head; the tail is returned for display.
 */
export function splitReason(reason: string | null | undefined): {
  head: string | null;
  tail: string | null;
} {
  const raw = (reason ?? "").trim();
  if (!raw) return { head: null, tail: null };
  const idx = raw.indexOf(":");
  if (idx === -1) return { head: raw, tail: null };
  return { head: raw.slice(0, idx).trim(), tail: raw.slice(idx + 1).trim() || null };
}

/**
 * Registry trace ids are the mission UUID with its dashes stripped. The REST
 * detail endpoints want the dashed form, so put them back for a 32-hex id and
 * pass anything else through untouched.
 */
export function missionIdFromTraceId(traceId: string): string {
  const compact = traceId.replace(/-/g, "");
  if (!/^[0-9a-f]{32}$/i.test(compact)) return traceId;
  return [
    compact.slice(0, 8),
    compact.slice(8, 12),
    compact.slice(12, 16),
    compact.slice(16, 20),
    compact.slice(20),
  ].join("-");
}

// A worker progress note is either the worker's own commentary ("Ich schaue
// mir zuerst den Arbeitsbereich an …") or a tool call the runtime rendered
// as "Tool: argument". The known tool families are matched by name; any other
// single PascalCase word before a colon is treated as a tool too, so a new
// runtime tool still reads as an action rather than as prose.
const KNOWN_TOOLS = new Set(
  [
    "read", "write", "edit", "multiedit", "glob", "grep", "bash", "powershell",
    "shell", "exec", "task", "agent", "webfetch", "websearch", "notebookedit",
    "todowrite", "apply_patch", "list", "ls", "search", "fetch", "run",
  ].map((s) => s.toLowerCase()),
);
const TOOL_NOTE_RE = /^([A-Z][A-Za-z_]{1,24}):\s+(.+)$/s;

export function classifyNote(note: string): {
  kind: "tool" | "narration";
  tool: string | null;
  text: string;
} {
  const trimmed = note.trim();
  const m = TOOL_NOTE_RE.exec(trimmed);
  if (m) {
    const name = m[1];
    const known = KNOWN_TOOLS.has(name.toLowerCase());
    // A PascalCase single word before a colon reads as a tool call; a plain
    // sentence-initial capitalised word ("Note: …", "Hinweis: …") would too,
    // so only the known families and CamelCase compounds count.
    const camel = /^[A-Z][a-z]+[A-Z]/.test(name);
    if (known || camel) return { kind: "tool", tool: name, text: m[2].trim() };
  }
  return { kind: "narration", tool: null, text: trimmed };
}

function iterationOf(workerId: string | null | undefined): number | null {
  if (!workerId) return null;
  const m = /::iter(\d+)$/.exec(workerId);
  return m ? Number(m[1]) : null;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

export function deriveOutcome(
  events: EventEnvelope[],
  language: string,
): AgentOutcome {
  const out: AgentOutcome = {
    terminal: null,
    reason: null,
    error_class: null,
    error_detail: null,
    failed_provider: null,
    last_state: null,
    partial_artifacts: [],
    summary: null,
    result_uri: null,
    cascade: false,
    deadline_ms: null,
    last_progress_ms: null,
    wall_ms: null,
    tokens_used: 0,
    cost_usd: 0,
    kills: [],
    verdicts: [],
    lastVerdict: null,
    lastCorrection: null,
    workers: [],
    revisions: 0,
    planWorkers: null,
    expectedOutput: null,
  };
  const lanes = new Map<string, WorkerLane>();

  for (const env of events) {
    const p = env.payload;
    switch (p.event_type) {
      case "MissionPlanReady":
        out.planWorkers = p.n_workers;
        out.expectedOutput = p.expected_output || null;
        break;
      case "WorkerSpawned": {
        if (!lanes.has(p.worker_id)) {
          lanes.set(p.worker_id, {
            worker_id: p.worker_id,
            iteration: iterationOf(p.worker_id) ?? 0,
            cli: str(p.cli),
            model: str(p.model),
            session_id: p.session_id ?? null,
            spawned_ms: env.ts_ms,
            ended_ms: null,
            ended_reason: null,
            notes: 0,
            tool_notes: 0,
          });
        }
        break;
      }
      case "WorkerProgress": {
        const lane = lanes.get(p.worker_id);
        if (lane && p.note) {
          lane.notes += 1;
          if (classifyNote(p.note).kind === "tool") lane.tool_notes += 1;
        }
        if (p.tokens_so_far) out.tokens_used = Math.max(out.tokens_used, p.tokens_so_far);
        if (p.cost_so_far) out.cost_usd = Math.max(out.cost_usd, p.cost_so_far);
        break;
      }
      case "WorkerDraftReady": {
        if (p.tokens_used) out.tokens_used = Math.max(out.tokens_used, p.tokens_used);
        if (p.cost_usd) out.cost_usd = Math.max(out.cost_usd, p.cost_usd);
        break;
      }
      case "CriticVerdictReady":
        out.verdicts.push(p);
        out.lastVerdict = p;
        break;
      case "WorkerCorrectionRequired":
        out.lastCorrection = p;
        out.revisions = Math.max(out.revisions, p.iteration);
        break;
      case "WorkerKilled": {
        out.kills.push(p);
        const lane = lanes.get(p.worker_id);
        if (lane) {
          lane.ended_ms = env.ts_ms;
          lane.ended_reason = p.reason;
        }
        if (!out.error_class && p.error_class) out.error_class = p.error_class;
        if (!out.error_detail && p.error_detail) out.error_detail = p.error_detail;
        break;
      }
      case "MissionApproved":
        out.terminal = "approved";
        out.summary = (language === "de" ? p.summary_de : p.summary_en) || p.summary_en || p.summary_de || null;
        out.result_uri = p.result_uri || null;
        out.wall_ms = p.wall_ms || null;
        if (p.tokens_used) out.tokens_used = Math.max(out.tokens_used, p.tokens_used);
        if (p.cost_usd) out.cost_usd = Math.max(out.cost_usd, p.cost_usd);
        break;
      case "MissionFailed": {
        const f: MissionFailed = p;
        out.terminal = "failed";
        out.reason = f.reason || null;
        out.error_class = f.error_class ?? out.error_class;
        out.error_detail = f.error_detail ?? out.error_detail;
        out.failed_provider = f.failed_provider ?? null;
        out.last_state = f.last_state || null;
        out.partial_artifacts = Array.isArray(f.partial_artifacts) ? f.partial_artifacts : [];
        break;
      }
      case "MissionCancelled":
        out.terminal = "cancelled";
        out.reason = p.reason || null;
        out.cascade = Boolean(p.cascade);
        break;
      case "MissionTimedOut":
        out.terminal = "timed_out";
        out.reason = "mission_timed_out";
        out.deadline_ms = p.deadline_ms || null;
        out.last_progress_ms = p.last_progress_ms || null;
        break;
      default:
        break;
    }
  }

  // A worker that never got an explicit kill still ended when the mission
  // did; without this a finished run listed every worker as still live.
  if (out.terminal) {
    const endTs = events.length ? events[events.length - 1].ts_ms : null;
    for (const lane of lanes.values()) {
      if (lane.ended_ms == null) lane.ended_ms = endTs;
    }
  }
  out.workers = [...lanes.values()].sort((a, b) => a.spawned_ms - b.spawned_ms);
  return out;
}

const TERMINAL_TONE: Record<Terminal, StoryTone> = {
  approved: "ok",
  failed: "error",
  cancelled: "warn",
  timed_out: "error",
};

export function buildStory(events: EventEnvelope[]): StoryEntry[] {
  const story: StoryEntry[] = [];
  const push = (env: EventEnvelope, entry: Omit<StoryEntry, "id" | "ts_ms">) => {
    story.push({ id: `${env.event_id}-${story.length}`, ts_ms: env.ts_ms, ...entry });
  };
  const base = (env: EventEnvelope) => ({
    worker_id: env.worker_id ?? null,
    iteration: iterationOf(env.worker_id),
    tool: null as string | null,
    meta: {} as Record<string, string>,
    verdict: null as CriticVerdictReady | null,
  });

  for (const env of events) {
    const p = env.payload;
    switch (p.event_type) {
      case "MissionDispatched":
        push(env, { ...base(env), kind: "dispatched", tone: "neutral", text: "", meta: { language: p.language } });
        break;
      case "MissionPlanReady":
        push(env, {
          ...base(env),
          kind: "plan",
          tone: "neutral",
          text: p.expected_output || "",
          meta: { workers: String(p.n_workers) },
        });
        break;
      case "WorkerSpawned":
        push(env, {
          ...base(env),
          worker_id: p.worker_id,
          iteration: iterationOf(p.worker_id),
          kind: "spawn",
          tone: "busy",
          text: "",
          meta: {
            cli: str(p.cli),
            ...(p.model ? { model: str(p.model) } : {}),
          },
        });
        break;
      case "WorkerProgress": {
        if (!p.note) break;
        const c = classifyNote(p.note);
        push(env, {
          ...base(env),
          worker_id: p.worker_id,
          iteration: iterationOf(p.worker_id),
          kind: c.kind,
          tone: p.stalled ? "warn" : "neutral",
          text: c.text,
          tool: c.tool,
          meta: p.pct != null ? { pct: `${Math.round(p.pct)}%` } : {},
        });
        break;
      }
      case "WorkerDraftReady":
        push(env, {
          ...base(env),
          worker_id: p.worker_id,
          iteration: iterationOf(p.worker_id),
          kind: "draft",
          tone: "neutral",
          text: "",
          meta: { diff_lines: String(p.diff ? p.diff.split("\n").length : 0) },
        });
        break;
      case "CriticVerdictReady":
        push(env, {
          ...base(env),
          worker_id: p.worker_id,
          iteration: p.iteration,
          kind: "verdict",
          tone: p.verdict === "approve" ? "ok" : p.verdict === "revise" ? "warn" : "error",
          text: p.summary,
          meta: { verdict: p.verdict, confidence: `${Math.round(p.confidence * 100)}%` },
          verdict: p,
        });
        break;
      case "WorkerCorrectionRequired":
        push(env, {
          ...base(env),
          worker_id: p.worker_id,
          iteration: p.iteration,
          kind: "correction",
          tone: "warn",
          text: p.correction_instruction,
          meta: p.next_model ? { next_model: p.next_model } : {},
        });
        break;
      case "WorkerKilled":
        push(env, {
          ...base(env),
          worker_id: p.worker_id,
          iteration: iterationOf(p.worker_id),
          kind: "killed",
          tone: p.reason === "user" || p.reason === "parent_cancelled" ? "warn" : "error",
          text: p.error_detail ?? "",
          meta: {
            reason: p.reason,
            ...(p.error_class ? { error_class: p.error_class } : {}),
          },
        });
        break;
      case "MissionBudgetWarning":
        push(env, {
          ...base(env),
          kind: "budget",
          tone: "warn",
          text: "",
          meta: { pct: `${Math.round(p.pct_used)}%`, limit: `$${p.limit_usd}` },
        });
        break;
      case "MissionApproved":
        push(env, {
          ...base(env),
          kind: "approved",
          tone: TERMINAL_TONE.approved,
          text: p.summary_en || p.summary_de || "",
          meta: {},
        });
        break;
      case "MissionFailed":
        push(env, {
          ...base(env),
          kind: "failed",
          tone: TERMINAL_TONE.failed,
          text: p.error_detail ?? "",
          meta: {
            reason: p.reason,
            ...(p.error_class ? { error_class: p.error_class } : {}),
            ...(p.failed_provider ? { provider: p.failed_provider } : {}),
          },
        });
        break;
      case "MissionCancelled":
        push(env, {
          ...base(env),
          kind: "cancelled",
          tone: TERMINAL_TONE.cancelled,
          text: "",
          meta: { reason: p.reason, ...(p.cascade ? { cascade: "yes" } : {}) },
        });
        break;
      case "MissionTimedOut":
        push(env, {
          ...base(env),
          kind: "timed_out",
          tone: TERMINAL_TONE.timed_out,
          text: "",
          meta: {},
        });
        break;
      default:
        // MissionStateChanged / BusStats: bookkeeping, not story.
        break;
    }
  }
  return story;
}

/** A run of consecutive tool calls, folded into one timeline block. */
export interface ActionsBlock {
  kind: "actions";
  id: string;
  ts_ms: number;
  end_ms: number;
  entries: StoryEntry[];
  /** Tool name → how often it ran, insertion-ordered by first use. */
  counts: Array<{ tool: string; n: number }>;
}

export type StoryBlock = { kind: "entry"; entry: StoryEntry } | ActionsBlock;

/**
 * Fold the story for reading: a worker that ran sixty tools in a row becomes
 * ONE "ran 60 actions" block (expandable), while every narration, verdict,
 * kill and terminal event keeps its own line. Also drops a kill's or terminal
 * event's detail text when it merely repeats the note just before it — the
 * same upstream error arrives three times in a real quota failure.
 */
export function groupStory(story: StoryEntry[]): StoryBlock[] {
  const blocks: StoryBlock[] = [];
  let run: StoryEntry[] = [];
  let lastText = "";

  const flush = () => {
    if (run.length === 0) return;
    const counts: Array<{ tool: string; n: number }> = [];
    for (const e of run) {
      const tool = e.tool ?? "tool";
      const hit = counts.find((c) => c.tool === tool);
      if (hit) hit.n += 1;
      else counts.push({ tool, n: 1 });
    }
    blocks.push({
      kind: "actions",
      id: `actions-${run[0].id}`,
      ts_ms: run[0].ts_ms,
      end_ms: run[run.length - 1].ts_ms,
      entries: run,
      counts,
    });
    run = [];
  };

  for (const entry of story) {
    if (entry.kind === "tool") {
      run.push(entry);
      continue;
    }
    flush();
    let text = entry.text;
    if ((entry.kind === "killed" || entry.kind === "failed") && text && text === lastText) {
      text = "";
    }
    if (entry.text) lastText = entry.text;
    blocks.push({ kind: "entry", entry: text === entry.text ? entry : { ...entry, text } });
  }
  flush();
  return blocks;
}
