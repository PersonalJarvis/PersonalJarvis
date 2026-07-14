// Standalone AudioWorklet module (loaded via addModule). Not part of the main
// bundle graph. tsconfig lib lacks AudioWorklet globals, so declare them here.
// `export {}` makes this file a module so the declarations below stay scoped
// to it (isolatedModules requires every file to be a module or a script; as a
// script these ambient declarations would otherwise leak into the rest of the
// app's type-check).
import {
  BoundedPcm16Queue,
  Pcm16Packetizer,
  playbackBufferSampleCount,
} from "./pcmWorkletBuffer";

export {};

declare const sampleRate: number;
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

class PcmPlayback extends AudioWorkletProcessor {
  private readonly queue = new BoundedPcm16Queue(
    playbackBufferSampleCount(sampleRate),
  );

  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent) => {
      const msg = e.data as { type: string; data?: ArrayBuffer };
      if (msg.type === "flush") this.queue.clear();
      else if (msg.type === "pcm" && msg.data) {
        // The bounded ring keeps the newest audio if a provider outruns
        // playback for more than ten seconds. enqueue() drops the oldest
        // samples on overrun, preventing stale latency and unbounded memory.
        this.queue.enqueue(new Int16Array(msg.data));
      }
    };
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0]?.[0];
    if (!out) return true;
    this.queue.dequeueInto(out);
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
registerProcessor("pcm-playback", PcmPlayback);
