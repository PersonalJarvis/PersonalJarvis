// Standalone AudioWorklet module (loaded via addModule). Not part of the main
// bundle graph. tsconfig lib lacks AudioWorklet globals, so declare them here.
// `export {}` makes this file a module so the declarations below stay scoped
// to it (isolatedModules requires every file to be a module or a script; as a
// script these ambient declarations would otherwise leak into the rest of the
// app's type-check).
import { Pcm16Packetizer } from "./pcmWorkletBuffer";
import { TimedPcmQueue, type RenderedAudioInterval } from "./playbackTimeline";
import { StartupAudioQueue } from "./startupAudio";

export {};

declare const sampleRate: number;
declare const currentTime: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor();
  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean;
}

class PcmCapture extends AudioWorkletProcessor {
  private levelSum = 0;
  private levelCount = 0;
  private readonly packetizer = new Pcm16Packetizer(sampleRate);
  private readonly emitPacket = (packet: ArrayBuffer): void => {
    this.port.postMessage(packet, [packet]);
  };

  process(inputs: Float32Array[][]): boolean {
    const ch = inputs[0]?.[0];
    if (ch && ch.length) {
      // Throttled (~30 Hz) input-level messages for the speaking indicator;
      // computed here where the float32 samples already live, so no extra
      // server round-trip. Separate message type; the audio path below is
      // untouched.
      for (let i = 0; i < ch.length; i++) this.levelSum += ch[i] * ch[i];
      this.levelCount += ch.length;
      if (this.levelCount >= sampleRate / 30) {
        const rms = Math.sqrt(this.levelSum / this.levelCount);
        this.port.postMessage({ type: "level", rms });
        this.levelSum = 0;
        this.levelCount = 0;
      }
      // AudioWorklet render quanta are commonly only 128 samples. Sending one
      // message per quantum would cross the thread boundary about 375 times/s
      // at 48 kHz, so coalesce them into exact ~20 ms packets (at most 50/s).
      this.packetizer.push(ch, this.emitPacket);
    }
    return true;
  }
}

class StartupCapture extends AudioWorkletProcessor {
  private queue = new StartupAudioQueue(sampleRate);
  private failed = false;

  constructor() {
    super();
    this.port.onmessage = (event: MessageEvent) => {
      try {
        if (event.data.type === "start") this.queue.start();
        else if (event.data.type === "suspend") this.queue.suspend();
        else if (event.data.type === "prefix") this.queue.prepend(event.data.samples);
      } catch (error) { this.fail(error); }
    };
  }

  private fail(error: unknown): void {
    this.failed = true;
    this.queue.suspend();
    this.port.postMessage({ type: "error", message: String(error) });
  }

  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const output = outputs[0]?.[0];
    if (!output) return true;
    if (this.failed) { output.fill(0); return true; }
    try { this.queue.process(inputs[0]?.[0] ?? new Float32Array(output.length), output); }
    catch (error) { this.fail(error); }
    return true;
  }
}

registerProcessor("pcm-startup", StartupCapture);

class PcmPlayback extends AudioWorkletProcessor {
  private readonly queue = new TimedPcmQueue(sampleRate);
  private spans: RenderedAudioInterval[] = [];
  private generation = 0;
  private muted = true;
  private volume = 1;
  private levelSum = 0;
  private levelCount = 0;

  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent) => {
      const msg = e.data as {
        type: string; data?: ArrayBuffer; muted?: boolean; volume?: number;
        generation?: number; startMs?: number; endMs?: number;
      };
      if (msg.type === "output_state") {
        const muted = msg.muted === true;
        if (muted !== this.muted) { this.queue.clear(); this.spans = []; }
        if (msg.generation !== undefined) this.generation = msg.generation;
        this.muted = muted;
        this.volume = msg.volume ?? 1;
      } else if (msg.type === "flush") {
        this.queue.clear();
        this.spans = [];
        this.generation = msg.generation ?? this.generation + 1;
      }
      else if (msg.type === "pcm" && msg.data && !this.muted) {
        // The bounded ring keeps the newest audio if a provider outruns
        // playback for more than ten seconds. enqueue() drops the oldest
        // samples on overrun, preventing stale latency and unbounded memory.
        this.queue.enqueue(new Int16Array(msg.data), msg.startMs !== undefined && msg.endMs !== undefined
          ? { startMs: msg.startMs, endMs: msg.endMs } : undefined);
      }
    };
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0]?.[0];
    if (!out) return true;
    // Banks a jitter reserve at a burst start and after an underrun, then
    // streams at wall clock. Zero-filling a starved quantum instead is what
    // chopped the voice on this surface.
    const rendered = this.queue.render(out, currentTime);
    if (!this.muted && this.volume > 0) this.spans.push(...rendered);
    for (let i = 0; i < out.length; i++) out[i] *= this.muted ? 0 : this.volume;
    // Throttled (~30 Hz) OUTPUT-level messages, the mirror of PcmCapture's.
    // Measured on the samples this processor just wrote, so the number is the
    // audio actually leaving for the speaker at that instant — the only
    // honest source for a "Jarvis is speaking" waveform, and the reason the
    // front-page bar can draw a real tape there instead of a stand-in sweep.
    // A silent quantum reports 0 rather than nothing: "the tap is alive and
    // there is nothing to hear" and "there is no tap here" must stay
    // distinguishable on the other end.
    for (let i = 0; i < out.length; i++) this.levelSum += out[i] * out[i];
    this.levelCount += out.length;
    if (this.levelCount >= sampleRate / 30) {
      const rms = Math.sqrt(this.levelSum / this.levelCount);
      this.port.postMessage({ type: "level", rms });
      this.port.postMessage({ type: "playback", generation: this.generation, spans: this.spans });
      this.spans = [];
      this.levelSum = 0;
      this.levelCount = 0;
    }
    return true;
  }
}

// A meter on the audio render thread keeps reporting while the WebView is
// hidden. requestAnimationFrame stops in background windows and cannot own
// the desktop's speaking/listening transitions.
class PcmLevel extends AudioWorkletProcessor {
  private sum = 0;
  private count = 0;

  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const input = inputs[0]?.[0];
    const size = input?.length ?? outputs[0]?.[0]?.length ?? 128;
    if (input) {
      for (const sample of input) this.sum += sample * sample;
    }
    this.count += size;
    if (this.count >= sampleRate / 30) {
      this.port.postMessage({ type: "level", rms: Math.sqrt(this.sum / this.count) });
      this.sum = 0;
      this.count = 0;
    }
    // Output remains silent; the HTML audio element owns audible playback.
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
registerProcessor("pcm-playback", PcmPlayback);
registerProcessor("pcm-level", PcmLevel);
