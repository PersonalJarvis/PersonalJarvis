/**
 * TypeScript-Mirror der Phase-6-Mission-Events aus jarvis/missions/events.py.
 *
 * Discriminated Union ueber `event_type`. Bei Pydantic-Drift (neuer Event-Type
 * hinzugefuegt, Feldsignatur geaendert) muss diese Datei nachgezogen werden,
 * sonst rejected die Frontend-Parser-Layer das Envelope.
 */

export type MissionState =
  | "PENDING"
  | "RUNNING"
  | "CRITIQUING"
  | "LOOPING"
  | "APPROVED"
  | "FAILED"
  | "CANCELLED"
  | "TIMED_OUT";

export type SourceActor =
  | "hauptjarvis"
  | "kontrollierer"
  | "worker"
  | "critic"
  | "ui"
  | "system";

export type WorkerCli = "claude" | "codex" | "python" | "browser";

export type EventType =
  | "MissionDispatched"
  | "MissionPlanReady"
  | "WorkerSpawned"
  | "WorkerProgress"
  | "WorkerDraftReady"
  | "CriticVerdictReady"
  | "WorkerCorrectionRequired"
  | "WorkerKilled"
  | "MissionApproved"
  | "MissionFailed"
  | "MissionCancelled"
  | "MissionTimedOut"
  | "MissionStateChanged"
  | "BusStats"
  | "MissionBudgetWarning";

export interface BasePayload {
  event_type: EventType;
}

export interface MissionDispatched extends BasePayload {
  event_type: "MissionDispatched";
  prompt: string;
  parent_mission_id: string | null;
  priority: number;
  language: "de" | "en";
}

export interface MissionPlanReady extends BasePayload {
  event_type: "MissionPlanReady";
  plan: Array<Record<string, unknown>>;
  n_workers: number;
  expected_output: string;
}

export interface WorkerSpawned extends BasePayload {
  event_type: "WorkerSpawned";
  worker_id: string;
  step: Record<string, unknown>;
  pid: number;
  cli: WorkerCli;
  model: string;
  worktree: string;
  session_id: string | null;
}

export interface WorkerProgress extends BasePayload {
  event_type: "WorkerProgress";
  worker_id: string;
  pct: number | null;
  note: string | null;
  stalled: boolean;
  tokens_so_far: number;
  cost_so_far: number;
}

export interface WorkerDraftReady extends BasePayload {
  event_type: "WorkerDraftReady";
  worker_id: string;
  artifact_uri: string;
  diff: string;
  tokens_used: number;
  cost_usd: number;
  session_id: string;
}

export type CriticVerdict = "approve" | "revise" | "reject";

export interface CriticAxisResult {
  pass?: boolean;
  evidence?: string[];
  notes?: string;
  [k: string]: unknown;
}

export interface CriticVerdictReady extends BasePayload {
  event_type: "CriticVerdictReady";
  worker_id: string;
  verdict: CriticVerdict;
  summary: string;
  confidence: number;
  axes: Record<string, CriticAxisResult>;
  iteration: number;
}

export interface WorkerCorrectionRequired extends BasePayload {
  event_type: "WorkerCorrectionRequired";
  worker_id: string;
  correction_instruction: string;
  iteration: number;
  next_model: string;
}

export type WorkerKilledReason =
  | "timeout"
  | "user"
  | "budget"
  | "parent_cancelled"
  | "injection_detected"
  | "path_guard"
  | "worker_error";

export interface WorkerKilled extends BasePayload {
  event_type: "WorkerKilled";
  worker_id: string;
  reason: WorkerKilledReason;
}

export interface MissionApproved extends BasePayload {
  event_type: "MissionApproved";
  result_uri: string;
  tokens_used: number;
  cost_usd: number;
  wall_ms: number;
  summary_de: string;
  summary_en: string;
}

export interface MissionFailed extends BasePayload {
  event_type: "MissionFailed";
  reason: string;
  error_class: string | null;
  last_state: string;
  partial_artifacts: string[];
}

export interface MissionCancelled extends BasePayload {
  event_type: "MissionCancelled";
  cascade: boolean;
  reason: string;
}

export interface MissionTimedOut extends BasePayload {
  event_type: "MissionTimedOut";
  deadline_ms: number;
  last_progress_ms: number;
}

export interface MissionStateChanged extends BasePayload {
  event_type: "MissionStateChanged";
  from_state: string;
  to_state: string;
  reason: string;
}

export interface BusStats extends BasePayload {
  event_type: "BusStats";
  queue_depths: Record<string, number>;
  dropped_count: Record<string, number>;
  active_subs: number;
}

export interface MissionBudgetWarning extends BasePayload {
  event_type: "MissionBudgetWarning";
  mission_id: string;
  pct_used: number;
  limit_usd: number;
}

export type AnyPayload =
  | MissionDispatched
  | MissionPlanReady
  | WorkerSpawned
  | WorkerProgress
  | WorkerDraftReady
  | CriticVerdictReady
  | WorkerCorrectionRequired
  | WorkerKilled
  | MissionApproved
  | MissionFailed
  | MissionCancelled
  | MissionTimedOut
  | MissionStateChanged
  | BusStats
  | MissionBudgetWarning;

export interface EventEnvelope {
  event_id: string;
  seq: number | null;
  mission_id: string;
  parent_event_id: string | null;
  worker_id: string | null;
  source_actor: SourceActor;
  ts_ms: number;
  schema_version: number;
  payload: AnyPayload;
}

export interface MissionSummary {
  id: string;
  prompt: string;
  state: MissionState;
  language: string;
  created_ms: number;
  iteration: number;
  cost_usd: number;
  parent_mission_id?: string | null;
}

export type OpenClawReattachStatus = "live" | "ended" | "killed" | "unknown";

export interface OpenClawWorkerSnapshot {
  worker_id: string;
  model: string;
  session_id: string | null;
  state_dir: string;
  log_path: string;
  cost_usd: number;
  tokens_used: number;
  reattach_status: OpenClawReattachStatus;
  spawned_ms: number;
  ended_ms: number | null;
  ended_reason: string | null;
  pid: number;
  worktree: string;
}

export interface MissionDetail {
  mission: MissionSummary;
  events: EventEnvelope[];
  verdicts: CriticVerdictReady[];
  openclaw_workers: OpenClawWorkerSnapshot[];
}

export interface MissionStateBadgeMeta {
  /**
   * i18n key for the human-readable badge label. The label is resolved at
   * render time (via `translate()` in the consumer) so a UI-language switch
   * updates it live — a module-level `translate()` here would freeze the label
   * to the language active at first import.
   */
  labelKey: string;
  className: string;
  iconName:
    | "Loader2"
    | "CheckCircle2"
    | "XCircle"
    | "Skull"
    | "AlertTriangle"
    | "Clock"
    | "Search"
    | "RotateCcw";
}

export const MISSION_STATE_BADGE: Record<MissionState, MissionStateBadgeMeta> = {
  PENDING: {
    labelKey: "mission_state.pending",
    className: "border-border text-muted-foreground bg-background/40",
    iconName: "Clock",
  },
  RUNNING: {
    labelKey: "mission_state.running",
    className: "border-primary/40 bg-primary/15 text-primary",
    iconName: "Loader2",
  },
  CRITIQUING: {
    labelKey: "mission_state.critiquing",
    className: "border-sky-400/40 bg-sky-400/10 text-sky-300",
    iconName: "Search",
  },
  LOOPING: {
    labelKey: "mission_state.looping",
    className: "border-amber-400/40 bg-amber-400/10 text-amber-300",
    iconName: "RotateCcw",
  },
  APPROVED: {
    labelKey: "mission_state.approved",
    className: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300",
    iconName: "CheckCircle2",
  },
  FAILED: {
    labelKey: "mission_state.failed",
    className: "border-destructive/50 bg-destructive/15 text-destructive",
    iconName: "XCircle",
  },
  CANCELLED: {
    labelKey: "mission_state.cancelled",
    className: "border-border text-muted-foreground bg-background/40",
    iconName: "XCircle",
  },
  TIMED_OUT: {
    labelKey: "mission_state.timed_out",
    className: "border-amber-500/40 bg-amber-500/10 text-amber-300",
    iconName: "Skull",
  },
};

export const TERMINAL_STATES: ReadonlySet<MissionState> = new Set([
  "APPROVED",
  "FAILED",
  "CANCELLED",
  "TIMED_OUT",
]);

export interface ServerHelloMessage {
  type: "hello";
  last_seq: number;
  token: string;
}

export interface PtyPauseMessage {
  type: "pause";
  worker_id: string;
}

export interface PtyResumeMessage {
  type: "resume";
  worker_id: string;
}
