// Standalone AudioWorklet module (loaded via addModule). Not part of the main
// bundle graph. tsconfig lib lacks AudioWorklet globals, so declare them here.
// `export {}` makes this file a module so the declarations below stay scoped
// to it (isolatedModules requires every file to be a module or a script; as a
// script these ambient declarations would otherwise leak into the rest of the
// app's type-check).
export {};

declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor();
  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean;
}

function floatToInt16(float32: Float32Array): ArrayBuffer {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out.buffer;
}

class PcmCapture extends AudioWorkletProcessor {
  process(inputs: Float32Array[][]): boolean {
    const ch = inputs[0]?.[0];
    if (ch && ch.length) {
      const buf = floatToInt16(ch);
      this.port.postMessage(buf, [buf]);
    }
    return true;
  }
}

class PcmPlayback extends AudioWorkletProcessor {
  private queue: Float32Array[] = [];
  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent) => {
      const msg = e.data as { type: string; data?: ArrayBuffer };
      if (msg.type === "flush") this.queue = [];
      else if (msg.type === "pcm" && msg.data) {
        const i16 = new Int16Array(msg.data);
        const f32 = new Float32Array(i16.length);
        for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
        this.queue.push(f32);
      }
    };
  }
  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0]?.[0];
    if (!out) return true;
    let filled = 0;
    while (filled < out.length && this.queue.length) {
      const head = this.queue[0];
      const n = Math.min(head.length, out.length - filled);
      out.set(head.subarray(0, n), filled);
      filled += n;
      if (n === head.length) this.queue.shift();
      else this.queue[0] = head.subarray(n);
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
registerProcessor("pcm-playback", PcmPlayback);

// (Note: the backend advertises 24 kHz TTS output while the browser
// AudioContext runs at its own rate — Phase 1 accepts minor pitch drift by
// playing 24 kHz samples through the context; a resampling playback pass is a
// Phase-2 polish item, logged here so it is not silently skipped.)
