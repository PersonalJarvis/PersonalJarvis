// 1:1 mirror of jarvis/sessions/models.py — Pydantic JSON output.
// Kept deliberately separate from the mission-types module, so the tab can
// be extended independently without API coupling.
//
// BUG-008 (three episodes): the backend uses ``str`` instead of ``Literal`` —
// hence ``string`` here too, not a union. The ``KNOWN_HANGUP_REASONS``
// constant documents the values expected today.

export const KNOWN_HANGUP_REASONS = [
  "",
  "voice_pattern",
  "hotkey",
  "client_stop",
  "ws_closed",
  "realtime_fallback",
  "idle_timeout",
  "shutdown",
  "error",
  "turn_complete",
] as const;

export type HangupReason = string;

export const KNOWN_VOICE_TIERS = [
  "",
  "router",
  "openclaw",
  "sub_jarvis",
  "trivial",
  "fast",
  "deep",
  "code",
] as const;

export type VoiceTier = string;

// Mirror of jarvis/sessions/constants.py SPOKEN_KINDS. Every phrase Jarvis
// VOICES that is not the brain's normal reply is recorded as a SpeechSpoken
// event tagged with one of these. Parity: tests/unit/sessions/
// test_spoken_kind_parity.py. Kept ``string`` (not a union) for the same
// BUG-008 reason as the others — an unknown kind must degrade, not crash.
export const KNOWN_SPOKEN_KINDS = [
  "clarify",
  "timeout",
  "unavailable",
  "stt_unavailable",
  "privacy",
  "completion",
  "subagent",
  "action_done",
  "backchannel",
  "announcement",
  "preamble",
  "progress",
  "withheld",
  "other",
] as const;

export type SpokenKind = string;

// One voiced phrase, extracted from the SpeechSpoken raw events of a session
// and grouped under its turn for rendering the "Spoken output" track.
export interface VoiceSpokenLine {
  turn_id: string | null;
  ts_ms: number;
  text: string;
  spoken_kind: SpokenKind;
  // Optional technical diagnostic that was NOT spoken aloud — e.g. the exit
  // code + harness reason behind a failed Computer-Use readback. The voice is
  // humanized; this is shown in the transcript for debugging (2026-06-16).
  detail?: string;
}

export interface VoiceEventRow {
  seq: number | null;
  session_id: string;
  turn_id: string | null;
  ts_ms: number;
  kind: string;
  payload: Record<string, unknown>;
}

export interface VoiceTurnRow {
  id: string;
  session_id: string;
  idx: number;
  started_ms: number;
  ended_ms: number | null;
  user_text: string;
  user_lang: string;
  jarvis_text: string;
  jarvis_lang: string;
  tier: VoiceTier;
  provider: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_total_ms: number;
  think_ms: number;
  speak_ms: number;
  tool_calls: string[];
  // True when the turn ended on a two-turn voice/chat confirmation
  // (finish_reason="voice_confirm_pending"): the reply is a pending yes/no
  // question, not a settled answer, so the transcript labels it distinctly.
  awaiting_confirmation: boolean;
}

export interface VoiceSessionRow {
  id: string;
  started_ms: number;
  ended_ms: number | null;
  hangup_reason: HangupReason;
  turn_count: number;
  total_cost_usd: number;
  total_tokens_in: number;
  total_tokens_out: number;
  providers_used: string[];
  language: string;
  wake_keyword: string;
}

export interface SessionListItem extends VoiceSessionRow {
  duration_s: number | null;
  preview: string;
}

export interface SessionDetail {
  session: VoiceSessionRow;
  turns: VoiceTurnRow[];
  events: VoiceEventRow[];
}
