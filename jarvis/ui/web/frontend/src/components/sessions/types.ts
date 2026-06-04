// 1:1 Spiegel zu jarvis/sessions/models.py — Pydantic JSON-Output.
// Halten wir absichtlich getrennt vom Mission-Types-Modul, damit der
// Tab eigenstaendig erweitert werden kann ohne API-Coupling.
//
// BUG-008 (drei Episoden): Backend nutzt ``str`` statt ``Literal`` —
// hier daher ebenfalls ``string``, nicht Union. Die Konstante
// ``KNOWN_HANGUP_REASONS`` dokumentiert die heute erwarteten Werte.

export const KNOWN_HANGUP_REASONS = [
  "",
  "voice_pattern",
  "hotkey",
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
