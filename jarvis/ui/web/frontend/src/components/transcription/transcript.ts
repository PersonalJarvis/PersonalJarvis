import type { SessionDetail, VoiceSpokenLine, VoiceTurnRow } from "@/components/sessions/types";

export interface TranscriptPassage {
  id: string;
  speaker: "you" | "assistant";
  text: string;
  original?: string;
  at: number;
  kind?: string;
  pending?: boolean;
}

export interface TranscriptExchange {
  id: string;
  number: number | null;
  passages: TranscriptPassage[];
}

/** SpeechSpoken is the playback record; never substitute an unspoken draft. */
export function transcriptExchanges(detail: SessionDetail): TranscriptExchange[] {
  const spoken = new Map<string, VoiceSpokenLine[]>();
  for (const event of detail.events) {
    if (event.kind !== "SpeechSpoken" || typeof event.payload.text !== "string" || !event.payload.text.trim()) continue;
    const key = event.turn_id ?? "";
    const lines = spoken.get(key) ?? [];
    lines.push({
      turn_id: event.turn_id, ts_ms: event.ts_ms, text: event.payload.text,
      spoken_kind: typeof event.payload.spoken_kind === "string" ? event.payload.spoken_kind : "other",
    });
    spoken.set(key, lines);
  }
  const exchanges: TranscriptExchange[] = detail.turns.map((turn, index) => ({
    id: turn.id, number: index + 1,
    passages: turnPassages(turn, spoken.get(turn.id) ?? []),
  }));
  // Announcements and background results may have no surviving turn. Keep them.
  const turnIds = new Set(detail.turns.map((turn) => turn.id));
  for (const [id, lines] of spoken) {
    if (turnIds.has(id)) continue;
    for (const [index, line] of lines.entries()) {
      exchanges.push({ id: `spoken-${id}-${index}`, number: null,
        passages: [spokenPassage(line, `${id}-${index}`)] });
    }
  }
  return exchanges
    .map((exchange) => ({ ...exchange, passages: exchange.passages.sort((a, b) => a.at - b.at) }))
    .filter((exchange) => exchange.passages.length > 0)
    .sort((a, b) => a.passages[0].at - b.passages[0].at);
}

function spokenPassage(line: VoiceSpokenLine, id: string): TranscriptPassage {
  return { id, speaker: "assistant", text: line.text, at: line.ts_ms, kind: line.spoken_kind };
}

function turnPassages(turn: VoiceTurnRow, spoken: VoiceSpokenLine[]): TranscriptPassage[] {
  const passages: TranscriptPassage[] = [];
  if (turn.user_text) {
    const polished = turn.user_text_polished?.trim();
    passages.push({
      id: `${turn.id}-you`, speaker: "you", at: turn.started_ms,
      text: polished || turn.user_text,
      original: polished && polished !== turn.user_text.trim() ? turn.user_text : undefined,
    });
  }
  for (const [index, line] of spoken.entries()) {
    passages.push({ ...spokenPassage(line, `${turn.id}-spoken-${index}`), pending: line.spoken_kind === "reply" && turn.awaiting_confirmation });
  }
  if (!spoken.some((line) => line.spoken_kind === "reply") && turn.jarvis_text) {
    passages.push({ id: `${turn.id}-reply`, speaker: "assistant", text: turn.jarvis_text,
      at: turn.ended_ms ?? turn.started_ms, pending: turn.awaiting_confirmation });
  }
  return passages;
}

export function passageText(passage: TranscriptPassage, original: boolean): string {
  return original && passage.original ? passage.original : passage.text;
}

export function elapsedTime(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function exchangeText(exchange: TranscriptExchange, original: boolean, you: string, assistant: string): string {
  return exchange.passages.map((p) => `${p.speaker === "you" ? you : assistant}\n${passageText(p, original)}`).join("\n\n");
}
