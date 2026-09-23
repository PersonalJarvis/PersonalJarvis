/** Transient wake prefix; never persisted or reused across connections. */
export interface InputPrefix {
  type: "input_prefix";
  sample_rate: number;
  audio: string;
}

/** Capture gate for an RTP track: retain the opening until session.started.
 * Quiet tail samples are omitted only while catching up, preserving 300 ms
 * of silence at every speech boundary. The initial delay therefore drains
 * during the user's pause instead of delaying every later turn.
 */
export class StartupAudioQueue {
  private frames: Float32Array[] = [];
  private offset = 0;
  private samples = 0;
  private quiet = 0;
  private started = false;
  private suspended = false;
  private prefix: Float32Array | null = null;

  constructor(private readonly rate: number) {}

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
  }

  suspend(): void {
    this.frames = [];
    this.samples = this.offset = this.quiet = 0;
    this.prefix = null;
    this.started = false;
    this.suspended = true;
  }

  process(input: Float32Array, output: Float32Array): void {
    output.fill(0);
    if (this.suspended) return;
    let loud = false;
    for (const sample of input) if (Math.abs(sample) > 0.00001) { loud = true; break; }
    this.quiet = loud ? 0 : this.quiet + input.length;
    // Keep live audio untouched after catching up; only excess startup silence
    // may be skipped, and never speech or its first 300 ms of trailing silence.
    if (loud || this.quiet <= this.rate * 0.3 || (this.started && this.samples === 0)) {
      if (this.samples + input.length > this.rate * 30) throw new Error("Voice startup buffer exceeded");
      this.frames.push(input.slice());
      this.samples += input.length;
    }
    if (!this.started) return;
    let written = 0;
    while (written < output.length && this.frames.length) {
      const head = this.frames[0];
      const count = Math.min(output.length - written, head.length - this.offset);
      output.set(head.subarray(this.offset, this.offset + count), written);
      written += count;
      this.offset += count;
      this.samples -= count;
      if (this.offset === head.length) { this.frames.shift(); this.offset = 0; }
    }
  }
}
