/** Mirrors the persisted VoiceTranscriptUpdated payload. */
export interface VoiceTranscriptSnapshot {
  session_id: string;
  segment_id: string;
  role: "user" | "assistant";
  text: string;
  start_ms: number;
  end_ms: number;
  revision: number;
}

export interface ReasoningSummarySnapshot {
  response_id: string;
  text: string;
  done: boolean;
}

export function readVoiceTranscript(value: unknown): VoiceTranscriptSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const p = value as Record<string, unknown>;
  if (typeof p.session_id !== "string" || !p.session_id ||
      typeof p.segment_id !== "string" || !p.segment_id ||
      (p.role !== "user" && p.role !== "assistant") || typeof p.text !== "string" ||
      !Number.isInteger(p.revision) || Number(p.revision) < 1 ||
      !Number.isFinite(p.start_ms) || !Number.isFinite(p.end_ms)) return null;
  return p as unknown as VoiceTranscriptSnapshot;
}
