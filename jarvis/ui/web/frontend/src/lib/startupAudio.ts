/** Transient wake prefix; never persisted or reused across connections. */
export interface InputPrefix {
  type: "input_prefix";
  sample_rate: number;
  audio: string;
}

/** Capture gate for an RTP track: retain the opening until session.started.
 * A short, pitch-preserving overlap-add pass pays down the startup backlog.
 * It does not classify low-amplitude speech as silence. Once caught up, audio
 * passes through unchanged. Only the microphone input is time-compressed;
 * assistant playback never runs through this queue.
 */
export class StartupAudioQueue {
  private frames: Float32Array[] = [];
  private head = 0;
  private offset = 0;
  private samples = 0;
  private started = false;
  private suspended = false;
  private prefix: Float32Array | null = null;
  private catchingUp = false;
  private readonly grain: Float32Array;
  private readonly analysis: Float32Array;
  private grainOffset = 0;
  private grainLength = 0;

  constructor(private readonly rate: number) {
    if (!Number.isFinite(rate) || rate <= 0) throw new Error("Invalid microphone sample rate");
    this.grain = new Float32Array(Math.max(1, Math.round(rate * 0.02)));
    this.analysis = new Float32Array(this.grain.length + Math.max(1, Math.ceil(rate * 0.015)));
  }

  /** Unsent input, including the remaining rendered grain; useful for diagnostics. */
  get pendingMs(): number {
    return (this.samples + (this.prefix?.length ?? 0) + this.grainLength - this.grainOffset) / this.rate * 1000;
  }

  prepend(pcm: Float32Array): void {
    if (this.started || this.suspended || this.prefix) throw new Error("Unexpected wake prefix");
    if (pcm.length + this.samples > this.rate * 30) throw new Error("Voice startup buffer exceeded");
    this.prefix = pcm;
  }

  start(): void {
    if (this.started) return;
    if (this.prefix) {
      this.frames.unshift(this.prefix);
      this.samples += this.prefix.length;
      this.prefix = null;
    }
    this.suspended = false;
    this.started = true;
    // A tiny device quantum needs no speech processing. A real handshake
    // backlog does: waiting for exactly zero microphone samples stranded it
    // indefinitely in rooms with even very faint background noise.
    this.catchingUp = this.samples > this.rate * 0.1;
  }

  suspend(): void {
    this.frames = [];
    this.samples = this.offset = this.head = 0;
    this.grainOffset = this.grainLength = 0;
    this.grain.fill(0);
    this.analysis.fill(0);
    this.catchingUp = false;
    this.prefix = null;
    this.started = false;
    this.suspended = true;
  }

  process(input: Float32Array, output: Float32Array): void {
    output.fill(0);
    if (this.suspended) return;
    if (this.samples + (this.prefix?.length ?? 0) + input.length > this.rate * 30) throw new Error("Voice startup buffer exceeded");
    if (input.length) this.frames.push(input.slice());
    this.samples += input.length;
    if (!this.started) return;
    let written = 0;
    while (written < output.length) {
      if (this.grainOffset < this.grainLength) {
        const count = Math.min(output.length - written, this.grainLength - this.grainOffset);
        output.set(this.grain.subarray(this.grainOffset, this.grainOffset + count), written);
        this.grainOffset += count;
        written += count;
        continue;
      }
      if (!this.samples) break;
      const needed = output.length - written;
      const backlog = this.samples - needed;
      if (this.catchingUp && backlog > 0) {
        this.renderCatchup(backlog);
        continue;
      }
      this.catchingUp = false;
      const count = Math.min(needed, this.samples);
      this.peek(output.subarray(written, written + count));
      this.consume(count);
      written += count;
    }
  }

  private peek(target: Float32Array): void {
    let frameIndex = this.head, offset = this.offset, written = 0;
    while (written < target.length) {
      const frame = this.frames[frameIndex];
      const count = Math.min(frame.length - offset, target.length - written);
      target.set(frame.subarray(offset, offset + count), written);
      written += count;
      offset = 0;
      frameIndex++;
    }
  }

  private consume(count: number): void {
    this.samples -= count;
    while (count) {
      const available = this.frames[this.head].length - this.offset;
      if (count < available) { this.offset += count; break; }
      count -= available;
      this.head++;
      this.offset = 0;
    }
    if (this.head > 256 || !this.samples) {
      this.frames.splice(0, this.head);
      this.head = 0;
    }
  }

  private renderCatchup(backlog: number): void {
    // Remove 5–15 ms per 20 ms grain, aligned to a similar waveform. The
    // convex crossfade preserves amplitude bounds and avoids pitch shifts
    // from naive resampling. At the tail, consume only the outstanding lag.
    const maximum = Math.min(backlog, Math.floor(this.samples / 2), Math.max(1, Math.round(this.rate * 0.015)));
    const minimum = Math.min(maximum, Math.max(1, Math.round(this.rate * 0.005)));
    const desired = Math.min(maximum, Math.max(1, Math.round(this.rate * 0.01)));
    const length = Math.min(this.grain.length, this.samples - maximum);
    this.peek(this.analysis.subarray(0, length + maximum));
    const stride = Math.max(1, Math.floor(this.rate / 6000));
    const step = Math.max(1, Math.floor(this.rate / 12000));
    let chosen = desired, best = Number.NEGATIVE_INFINITY;
    for (let shift = minimum; shift <= maximum; shift += step) {
      let dot = 0, leftEnergy = 0, rightEnergy = 0;
      for (let i = 0; i < length; i += stride) {
        const left = this.analysis[i], right = this.analysis[i + shift];
        dot += left * right;
        leftEnergy += left * left;
        rightEnergy += right * right;
      }
      const energy = Math.sqrt(leftEnergy * rightEnergy);
      const similarity = energy > 1e-20 ? dot / energy : 1;
      // On silence/constant noise, choose the desired advance, not the first
      // candidate. Search cost and progress are bounded at every sample rate.
      const score = similarity - 0.001 * Math.abs(shift - desired) / Math.max(1, maximum);
      if (score > best) { best = score; chosen = shift; }
    }
    for (let i = 0; i < length; i++) {
      const blend = length > 1 ? i / (length - 1) : 0.5;
      this.grain[i] = this.analysis[i] * (1 - blend) + this.analysis[i + chosen] * blend;
    }
    this.consume(length + chosen);
    this.grainOffset = 0;
    this.grainLength = length;
  }
}
