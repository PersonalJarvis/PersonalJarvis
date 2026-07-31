// 1:1 mirror of jarvis/runs/model.py + jarvis/runs/constants.py.
// Enum-like values are `string` (not unions) for the same BUG-008 reason as the
// sessions mirror — an unknown value must degrade, not crash. Parity guard:
// components/runs/__tests__/runEnumParity.test.ts + tests/unit/runs/test_constants_parity.py.

export const SLO_STATUSES = ["ok", "warn", "breach"] as const;
export const RUN_DECISION_KINDS = [
  "tier", "route", "risk", "brain", "mission", "fallback",
] as const;
export const RATIONALE_SOURCES = ["model", "rule"] as const;
export const TRANSCRIPT_ROLES = ["user", "jarvis", "system", "tool", "error"] as const;
export const RUN_OUTCOMES = ["success", "partial", "failed"] as const;
// Lanes of the raw developer event stream — colour + filter groups.
export const RUN_EVENT_CATEGORIES = [
  "lifecycle", "speech", "brain", "tool", "agent", "vision", "latency", "error", "system",
] as const;

export type SloStatus = string;
export interface RunActivity { tools: string[]; agents: string[]; }

/** One persisted bus event, verbatim — the developer's ground truth. */
export interface RawEvent {
  seq: number;
  kind: string;
  category: string;        // see RUN_EVENT_CATEGORIES
  ts_ms: number;
  offset_ms: number;
  summary: string;
  payload: Record<string, unknown>;
}

/** How the run was configured, read back from what was recorded. */
export interface RunEnvironment {
  voice_mode: string;
  surface: string;
  wake_source: string;
  wake_keyword: string;
  language: string;
  hangup_reason: string;
  providers: string[];
  models: string[];
  tiers: string[];
  voices: string[];
  input_sample_rate: number | null;
  output_sample_rate: number | null;
}

export interface TraceEvent { kind: string; offset_ms: number; ts_ms: number; summary: string; }
export interface TranscriptLine {
  role: string; kind: string; text: string;
  offset_ms: number; ts_ms: number; spoken_kind: string | null;
}
export interface ToolCall {
  name: string; caller: string; risk_tier: string;
  approved_by: string | null; duration_ms: number | null;
  exit_code: number | null; success: boolean; error_line: string | null;
  // What was actually run and what came back — both redacted + capped upstream.
  command: string; output: string;
}
export interface LatencyEntry { phase: string; duration_ms: number; slo_status: SloStatus; }
export interface DecisionStep {
  kind: string; label: string; detail: string | null;
  // The honest "why". rationale_source: "model" = the brain's own words,
  // "rule" = deterministic explanation from a captured fact, "" = none recorded.
  rationale: string; rationale_source: string;
}
export interface ErrorEntry { source: string; layer: string | null; message: string; recoverable: boolean | null; }
export interface TurnExtras {
  interrupted: boolean; cache_hit: boolean | null;
  endpoint_reason: string | null; context_tokens: number | null;
}
export interface MissionRef { mission_id: string; status: string; summary: string; }

export interface RunTurn {
  idx: number; trace_id: string; outcome: string;
  user_text: string; jarvis_text: string;
  tier: string; provider: string; model: string;
  tokens_in: number; tokens_out: number; cost_usd: number;
  think_ms: number; speak_ms: number;
  transcript: TranscriptLine[];
  latency: LatencyEntry[]; decision_path: DecisionStep[];
  tools: ToolCall[]; errors: ErrorEntry[]; extras: TurnExtras;
  activity: RunActivity;
  events: RawEvent[];
  event_counts: Record<string, number>;
  events_truncated: boolean;
  // false = no usage was measured for this turn (≠ "it cost zero").
  usage_recorded: boolean;
}
export interface RunAnalytics {
  total_duration_s: number | null; total_think_ms: number; total_speak_ms: number;
  total_tokens_in: number; total_tokens_out: number;
  cost_by_provider: Record<string, number>; tool_counts: Record<string, number>;
  interruptions: number; worst_slo_status: SloStatus;
}
export interface RunListItem {
  session_id: string; started_ms: number; ended_ms: number | null;
  duration_s: number | null; hangup_reason: string; wake_source: string;
  turn_count: number; total_cost_usd: number; error_count: number;
  outcome: string; slo_status: SloStatus; feature_tags: string[]; preview: string;
}
import type { VoiceSessionRow } from "@/components/sessions/types";
export interface Run {
  session: VoiceSessionRow;
  outcome: string;
  turns: RunTurn[];
  missions: MissionRef[];
  activity: RunActivity;
  analytics: RunAnalytics;
  environment: RunEnvironment;
  session_events: RawEvent[];
  event_counts: Record<string, number>;
}
