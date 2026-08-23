/**
 * The live transcript the voice stage shows: what was said to the assistant
 * and what it answered, as lines, newest last.
 *
 * Why its own reducer and not `messages`: a SPOKEN turn does not travel as
 * `MessageSent`. The pipeline publishes the heard words as `TranscriptFinal`
 * (and the last `TranscriptionUpdate` with `is_final`), and the answer as
 * `SpeechSpoken` — the authoritative "this was actually said out loud" track,
 * usually in sentence-sized pieces. Typed turns DO come as `MessageSent`.
 * The lane merges the two so it reads the same whether you spoke or typed.
 *
 * Pure: (lines, event) → lines, and the same array back for every event it
 * does not read, so the store can skip the update.
 */

export type TranscriptWho = "user" | "assistant";

export interface TranscriptLine {
  id: string;
  ts: number;
  who: TranscriptWho;
  text: string;
}

/** How many lines are kept. The lane shows the tail; this bounds memory. */
export const TRANSCRIPT_MAX = 40;
/** Consecutive spoken pieces of one answer are joined when this close. */
const JOIN_WITHIN_MS = 6_000;
/** A repeat of the same words within this window is the same utterance. */
const DUPLICATE_WITHIN_MS = 12_000;

let counter = 0;
function nextId(ts: number): string {
  counter = (counter + 1) % 1_000_000;
  return `t${ts}-${counter}`;
}

function clean(text: unknown): string {
  return typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
}

function isDuplicate(lines: TranscriptLine[], who: TranscriptWho, text: string, ts: number): boolean {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    if (ts - line.ts > DUPLICATE_WITHIN_MS) return false;
    if (line.who === who && (line.text === text || line.text.endsWith(text))) return true;
  }
  return false;
}

function push(lines: TranscriptLine[], who: TranscriptWho, text: string, ts: number): TranscriptLine[] {
  const next = [...lines, { id: nextId(ts), ts, who, text }];
  return next.length > TRANSCRIPT_MAX ? next.slice(next.length - TRANSCRIPT_MAX) : next;
}

export function reduceTranscript(
  lines: TranscriptLine[],
  name: string,
  payload: unknown,
  tsMs: number,
): TranscriptLine[] {
  const p = (payload ?? {}) as Record<string, unknown>;

  switch (name) {
    case "TranscriptFinal": {
      const transcript = (p.transcript ?? {}) as Record<string, unknown>;
      const text = clean(transcript.text);
      if (!text || isDuplicate(lines, "user", text, tsMs)) return lines;
      return push(lines, "user", text, tsMs);
    }
    case "TranscriptionUpdate": {
      if (p.is_final !== true) return lines;
      const text = clean(p.text);
      if (!text || isDuplicate(lines, "user", text, tsMs)) return lines;
      return push(lines, "user", text, tsMs);
    }
    case "SpeechSpoken": {
      const text = clean(p.text);
      if (!text || isDuplicate(lines, "assistant", text, tsMs)) return lines;
      const last = lines[lines.length - 1];
      if (last && last.who === "assistant" && tsMs - last.ts < JOIN_WITHIN_MS) {
        // One answer, said in pieces: grow the line instead of stacking pieces.
        const joined = [...lines];
        joined[joined.length - 1] = { ...last, ts: tsMs, text: `${last.text} ${text}` };
        return joined;
      }
      return push(lines, "assistant", text, tsMs);
    }
    case "MessageSent": {
      const role = p.role;
      if (role !== "user" && role !== "assistant") return lines;
      const text = clean(p.text);
      if (!text || isDuplicate(lines, role, text, tsMs)) return lines;
      return push(lines, role, text, tsMs);
    }
    default:
      return lines;
  }
}
