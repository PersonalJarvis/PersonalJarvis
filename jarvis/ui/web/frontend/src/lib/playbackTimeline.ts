import { JitterBufferedPcm16Queue } from "./pcmWorkletBuffer";

/** Source time is preserved across buffering, resampling and device playout. */
export type AudioInterval = { startMs: number; endMs: number };
export type RenderedAudioInterval = AudioInterval & { contextStart: number; contextEnd: number };
type QueuedInterval = { frames: number; consumed: number; timing?: AudioInterval };

export function validAudioInterval(start: unknown, end: unknown): boolean {
  return typeof start === "number" && typeof end === "number" &&
    Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start;
}

/** Shares the audio ring's exact dequeue/drop decisions; silence is not progress. */
export class TimedPcmQueue {
  private readonly audio: JitterBufferedPcm16Queue;
  private spans: QueuedInterval[] = [];

  constructor(private readonly rate: number) { this.audio = new JitterBufferedPcm16Queue(rate); }

  clear(): void { this.audio.clear(); this.spans = []; }

  enqueue(samples: Int16Array, timing?: AudioInterval): void {
    if (!samples.length) return;
    this.spans.push({ frames: samples.length, consumed: 0,
      timing: timing && validAudioInterval(timing.startMs, timing.endMs) ? timing : undefined });
    this.consume(this.audio.enqueue(samples)); // Overrun discards audio, never counts it as heard.
  }

  render(output: Float32Array, contextStart: number): RenderedAudioInterval[] {
    const count = this.audio.render(output);
    return this.consume(count, contextStart);
  }

  private consume(count: number, contextStart?: number): RenderedAudioInterval[] {
    const result: RenderedAudioInterval[] = [];
    let offset = 0;
    while (count > 0 && this.spans.length) {
      const span = this.spans[0];
      const take = Math.min(count, span.frames - span.consumed);
      if (contextStart !== undefined && span.timing) {
        const duration = span.timing.endMs - span.timing.startMs;
        result.push({
          startMs: span.timing.startMs + duration * span.consumed / span.frames,
          endMs: span.timing.startMs + duration * (span.consumed + take) / span.frames,
          contextStart: contextStart + offset / this.rate,
          contextEnd: contextStart + (offset + take) / this.rate,
        });
      }
      span.consumed += take;
      offset += take;
      count -= take;
      if (span.consumed === span.frames) this.spans.shift();
    }
    return result;
  }
}

/** The hardware output clock, not AudioContext's ahead-of-device render clock. */
export function audibleContextTime(context: Pick<AudioContext,
  "state" | "currentTime" | "getOutputTimestamp" | "baseLatency" | "outputLatency"
>): number {
  const stamp = context.getOutputTimestamp?.();
  if (stamp && Number.isFinite(stamp.contextTime)) {
    return stamp.contextTime!;
  }
  // Older engines expose measured latency rather than getOutputTimestamp.
  const latency = (Number.isFinite(context.baseLatency) ? context.baseLatency : 0) +
    (Number.isFinite(context.outputLatency) ? context.outputLatency : 0);
  return Number.isFinite(context.currentTime) ? Math.max(0, context.currentTime - latency) : 0;
}

/** Retain rendered spans until the device clock reaches them, including the tail. */
export class DevicePlaybackTimeline {
  private pending: RenderedAudioInterval[] = [];

  clear(): void { this.pending = []; }

  rendered(spans: RenderedAudioInterval[]): void {
    for (const span of spans) {
      const last = this.pending.at(-1);
      if (last && Math.abs(last.endMs - span.startMs) < 0.00001 &&
          Math.abs(last.contextEnd - span.contextStart) < 0.0000001 &&
          Math.abs((last.endMs - last.startMs) / (last.contextEnd - last.contextStart) -
            (span.endMs - span.startMs) / (span.contextEnd - span.contextStart)) < 0.001) {
        last.endMs = span.endMs;
        last.contextEnd = span.contextEnd;
      } else this.pending.push({ ...span });
    }
  }

  advance(contextTime: number): AudioInterval[] {
    const heard: AudioInterval[] = [];
    while (this.pending.length) {
      const span = this.pending[0];
      if (contextTime <= span.contextStart) break;
      const fraction = Math.min(1, (contextTime - span.contextStart) / (span.contextEnd - span.contextStart));
      const endMs = span.startMs + fraction * (span.endMs - span.startMs);
      heard.push({ startMs: span.startMs, endMs });
      if (fraction === 1) this.pending.shift();
      else {
        this.pending[0] = { ...span, startMs: endMs, contextStart: contextTime };
        break;
      }
    }
    return heard;
  }
}
