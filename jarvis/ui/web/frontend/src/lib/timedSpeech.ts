import { z } from "zod";
import type { AudioInterval } from "./playbackTimeline";
import { publishTimedSpeechPlayback, spokenWordEnd } from "./speechPlayback";

const epoch = z.number().int().nonnegative();
const time = z.number().finite().nonnegative();
export const TimedAudioFrame = z.object({
  type: z.literal("audio_timed"), epoch,
  audio: z.string().max(1_400_000), sample_rate: z.number().int().min(8000).max(192000),
  start_ms: time, end_ms: time,
}).refine(v => v.end_ms > v.start_ms);
export const TimedTextFrame = z.object({
  type: z.literal("speech_timing"), epoch,
  line_id: z.string().min(1).max(512), text: z.string().max(128_000),
  char_start: z.number().int().nonnegative(), char_end: z.number().int().nonnegative(),
  start_ms: time, end_ms: time,
}).refine(v => v.end_ms > v.start_ms && v.char_end <= v.text.length && v.char_end > v.char_start);
export type TimedText = z.infer<typeof TimedTextFrame>;
type Line = { text: string; chars: number; cues: TimedText[] };

/** Match timestamped fragments against intervals the device actually played. */
export class TimedSpeechTracker {
  private lines = new Map<string, Line>();
  private heard: AudioInterval[] = [];

  clear(): void {
    // Published positions survive an interruption; pending cues never do.
    this.lines.clear();
    this.heard = [];
  }

  text(cue: TimedText): void {
    let line = this.lines.get(cue.line_id);
    if (!line || !cue.text.startsWith(line.text)) {
      line = { text: cue.text, chars: 0, cues: [] };
      this.lines.set(cue.line_id, line);
    }
    line.text = cue.text;
    if (!line.cues.some(old => old.char_start === cue.char_start && old.char_end === cue.char_end)) {
      line.cues.push(cue);
      line.cues.sort((a, b) => a.char_start - b.char_start);
    }
    while (this.lines.size > 128) this.lines.delete(this.lines.keys().next().value!);
    this.project(cue.line_id, line);
  }

  played(intervals: AudioInterval[]): void {
    for (const span of intervals) {
      const last = this.heard.at(-1);
      if (last && span.startMs <= last.endMs + 0.01 && span.startMs >= last.startMs) {
        last.endMs = Math.max(last.endMs, span.endMs);
      } else this.heard.push({ ...span });
    }
    // One minute covers late captions without retaining a call's audio history.
    const oldest = (this.heard.at(-1)?.endMs ?? 0) - 60_000;
    this.heard = this.heard.filter(span => span.endMs >= oldest).slice(-4096);
    for (const [id, line] of this.lines) this.project(id, line);
  }

  private project(id: string, line: Line): void {
    for (const cue of line.cues) {
      if (cue.char_start > line.chars) break;
      const span = this.heard.find(range => range.startMs <= cue.start_ms + 0.01 && range.endMs > cue.start_ms);
      if (!span) break;
      const fraction = Math.min(1, (span.endMs - cue.start_ms) / (cue.end_ms - cue.start_ms));
      // Provider intervals may span more than one word. Interpolate only
      // inside that measured fragment, never at a global characters/sec rate.
      const fragment = line.text.slice(cue.char_start, cue.char_end);
      const position = fraction === 1 ? fragment.length :
        spokenWordEnd(fragment, Math.min(fragment.length - 1, Math.floor(fraction * fragment.length)));
      line.chars = Math.max(line.chars, cue.char_start + position);
      if (fraction < 1) break;
    }
    publishTimedSpeechPlayback(id, line.text, line.chars);
  }
}
