import type { SessionDetail } from "@/components/sessions/types";

export function transcriptFixture(): SessionDetail {
  return {
    session: { id: "voice/one", started_ms: 1000, ended_ms: 31000, hangup_reason: "hotkey", turn_count: 1,
      total_cost_usd: 0, total_tokens_in: 0, total_tokens_out: 0, providers_used: [], language: "en", wake_keyword: "", voice_mode: "realtime" },
    turns: [{ id: "turn-a", session_id: "voice/one", idx: 0, started_ms: 2000, ended_ms: 8000,
      user_text: "um find [notes] please", user_text_polished: "Find [notes], please.", user_lang: "en",
      jarvis_text: "An unspoken draft", jarvis_lang: "en", tier: "fast", provider: "private-provider", model: "private-model",
      tokens_in: 123, tokens_out: 456, cost_usd: 0, latency_total_ms: 100, think_ms: 50, speak_ms: 50,
      tool_calls: ["private-tool"], awaiting_confirmation: true, voice_name: "", voice_provider: "" }],
    events: [
      { seq: 1, session_id: "voice/one", turn_id: "turn-a", ts_ms: 7000, kind: "SpeechSpoken", payload: { text: "Here are the [notes].", spoken_kind: "reply" } },
      { seq: 2, session_id: "voice/one", turn_id: "turn-a", ts_ms: 3000, kind: "SpeechSpoken", payload: { text: "One moment.", spoken_kind: "progress" } },
      { seq: 3, session_id: "voice/one", turn_id: null, ts_ms: 1100, kind: "SpeechSpoken", payload: { text: "Ready to listen.", spoken_kind: "announcement" } },
      { seq: 4, session_id: "voice/one", turn_id: "retired-turn", ts_ms: 20000, kind: "SpeechSpoken", payload: { text: "Your background task is complete.", spoken_kind: "completion" } },
    ],
  };
}
