// Dedicated /ws/audio client for browser-owned voice. Separate from the
// JSON-only WSClient: this socket carries raw mono PCM16 in both directions.

import { LevelMeter } from "./levelMeter";

export function buildAudioSocketUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  const token = (window as unknown as { __JARVIS_TOKEN?: string }).__JARVIS_TOKEN;
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}://${host}/ws/audio${query}`;
}

export type RealtimeStatusPayload = Record<string, unknown>;

export type RealtimeCallbacks = {
  onTranscript?: (text: string, isFinal: boolean, role: string) => void;
  onStatus?: (status: string, payload: RealtimeStatusPayload) => void;
  onAudio?: () => void;
  /** Normalized 0..1 microphone input level, ~30 Hz while capturing. */
  onInputLevel?: (level: number) => void;
};

export type BrowserSpeechOutcome = "ended" | "error" | "unavailable";

type BrowserSpeechHandlers = {
  onStart?: () => void;
  onFinish: (outcome: BrowserSpeechOutcome) => void;
};

type SpeechSynthesisSurface = Pick<SpeechSynthesis, "cancel" | "speak">;

/** Keyless speech output for the headless/browser surface.
 *
 * The controller owns exactly one utterance. A new turn or barge-in invalidates
 * callbacks from the previous one, preventing a stale `onend` event from
 * acknowledging the wrong server turn.
 */
export class BrowserSpeechFallback {
  private generation = 0;
  private active = false;

  constructor(
    private readonly synthesis: SpeechSynthesisSurface | null =
      typeof window !== "undefined" && "speechSynthesis" in window
        ? window.speechSynthesis
        : null,
    private readonly createUtterance: ((text: string) => SpeechSynthesisUtterance) | null =
      typeof SpeechSynthesisUtterance === "function"
        ? (text) => new SpeechSynthesisUtterance(text)
        : null,
  ) {}

  speak(
    text: string,
    language: string,
    volume: number,
    handlers: BrowserSpeechHandlers,
  ): boolean {
    this.cancel();
    if (!this.synthesis || !this.createUtterance || !text.trim()) {
      handlers.onFinish("unavailable");
      return false;
    }

    const generation = ++this.generation;
    const utterance = this.createUtterance(text);
    let settled = false;
    const finish = (outcome: BrowserSpeechOutcome) => {
      if (settled || generation !== this.generation) return;
      settled = true;
      this.active = false;
      handlers.onFinish(outcome);
    };
    utterance.lang = language || "en-US";
    utterance.volume = Math.max(0, Math.min(1, Number.isFinite(volume) ? volume : 1));
    utterance.onstart = () => {
      if (generation === this.generation) handlers.onStart?.();
    };
    utterance.onend = () => finish("ended");
    utterance.onerror = () => finish("error");
    try {
      this.active = true;
      this.synthesis.speak(utterance);
      return true;
    } catch {
      finish("error");
      return false;
    }
  }

  cancel(): void {
    this.generation += 1;
    if (!this.active) return;
    this.active = false;
    try {
      this.synthesis?.cancel();
    } catch {
      // A browser may tear down its speech service during page navigation.
    }
  }
}

/** Stateful linear PCM16 resampler used for provider audio playback.
 *
 * Realtime providers currently emit 24 kHz PCM, while AudioContext commonly
 * runs at 44.1 or 48 kHz. Carrying one sample and the fractional source
 * position across WebSocket frames avoids pitch/speed errors and chunk-edge
 * discontinuities without a native dependency.
 */
export class StreamingPcm16Resampler {
  private readonly step: number;
  private tail: number | null = null;
  private position = 0;

  constructor(
    readonly fromRate: number,
    readonly toRate: number,
  ) {
    if (fromRate <= 0 || toRate <= 0) throw new Error("PCM sample rates must be positive");
    this.step = fromRate / toRate;
  }

  process(pcm: ArrayBuffer): ArrayBuffer {
    if (pcm.byteLength === 0) return new ArrayBuffer(0);
    if (pcm.byteLength % 2 !== 0) throw new Error("PCM16 input contains a partial sample");
    if (this.fromRate === this.toRate) return pcm.slice(0);

    const incoming = new Int16Array(pcm);
    const samples = new Float64Array(incoming.length + (this.tail === null ? 0 : 1));
    let offset = 0;
    if (this.tail !== null) {
      samples[0] = this.tail;
      offset = 1;
    }
    for (let i = 0; i < incoming.length; i++) samples[i + offset] = incoming[i];
    if (samples.length < 2) {
      this.tail = samples[0] ?? null;
      return new ArrayBuffer(0);
    }

    const limit = samples.length - 1;
    if (this.position >= limit) {
      this.position -= limit;
      this.tail = samples[samples.length - 1];
      return new ArrayBuffer(0);
    }
    const count = Math.ceil((limit - this.position) / this.step);
    const output = new Int16Array(count);
    let sourcePosition = this.position;
    for (let i = 0; i < count; i++) {
      const left = Math.floor(sourcePosition);
      const fraction = sourcePosition - left;
      const value = samples[left] + (samples[left + 1] - samples[left]) * fraction;
      output[i] = Math.max(-32768, Math.min(32767, Math.round(value)));
      sourcePosition += this.step;
    }
    this.position = sourcePosition - limit;
    this.tail = samples[samples.length - 1];
    return output.buffer;
  }

  reset(): void {
    this.tail = null;
    this.position = 0;
  }
}

export class RealtimeAudioClient {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private captureSink: GainNode | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;
  private playbackResampler: StreamingPcm16Resampler | null = null;
  private connecting: Promise<void> | null = null;
  private ready = false;
  private intentionalClose = false;
  private inputMeter = new LevelMeter();
  private browserSpeech = new BrowserSpeechFallback();

  constructor(private cb: RealtimeCallbacks = {}) {}

  connect(): Promise<void> {
    if (this.ready) return Promise.resolve();
    if (this.connecting) return this.connecting;
    this.connecting = this.open().finally(() => {
      this.connecting = null;
    });
    return this.connecting;
  }

  private async open(): Promise<void> {
    this.intentionalClose = false;
    try {
      this.ctx = new AudioContext({ latencyHint: "interactive" });
      await this.ctx.audioWorklet.addModule(new URL("./pcm-worklet.ts", import.meta.url));
      await this.ctx.resume();

      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: { ideal: 1 },
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const source = this.ctx.createMediaStreamSource(this.stream);
      this.captureNode = new AudioWorkletNode(this.ctx, "pcm-capture");
      this.playbackNode = new AudioWorkletNode(this.ctx, "pcm-playback");
      // Keep the capture worklet in the active audio graph without feeding the
      // microphone back to the user. Browser AEC still sees the real playback
      // node connected below and can remove it from captured audio.
      this.captureSink = this.ctx.createGain();
      this.captureSink.gain.value = 0;
      source.connect(this.captureNode);
      this.captureNode.connect(this.captureSink);
      this.captureSink.connect(this.ctx.destination);
      this.playbackNode.connect(this.ctx.destination);

      this.ws = new WebSocket(buildAudioSocketUrl());
      this.ws.binaryType = "arraybuffer";
      this.captureNode.port.onmessage = (event) => {
        const data = event.data as ArrayBuffer | { type?: string; rms?: number };
        if (data instanceof ArrayBuffer) {
          if (this.ready && this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(data);
          }
          return;
        }
        if (data && data.type === "level" && typeof data.rms === "number") {
          this.cb.onInputLevel?.(this.inputMeter.push(data.rms));
        }
      };

      await this.waitUntilReady(this.ws);
    } catch (error) {
      await this.teardown(false);
      throw error instanceof Error ? error : new Error(String(error));
    }
  }

  private waitUntilReady(socket: WebSocket): Promise<void> {
    return new Promise((resolve, reject) => {
      let settled = false;
      const timeout = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error("Realtime voice connection timed out"));
      }, 20_000);

      const fail = (error: Error) => {
        if (!settled) {
          settled = true;
          window.clearTimeout(timeout);
          reject(error);
        }
      };

      socket.onopen = () => {
        socket.send(
          JSON.stringify({ type: "audio_start", sample_rate: this.ctx?.sampleRate ?? 48_000 }),
        );
      };
      socket.onerror = () => fail(new Error("Realtime voice socket failed"));
      socket.onclose = (event) => {
        this.ready = false;
        if (!this.intentionalClose) {
          this.cb.onStatus?.("disconnected", { code: event.code, reason: event.reason });
          fail(new Error(event.reason || `Realtime voice socket closed (${event.code})`));
        }
      };
      socket.onmessage = (event) => {
        if (typeof event.data !== "string") {
          this.handleAudio(event.data as ArrayBuffer);
          return;
        }
        let message: RealtimeStatusPayload;
        try {
          message = JSON.parse(event.data) as RealtimeStatusPayload;
        } catch {
          return;
        }
        const type = typeof message.type === "string" ? message.type : "unknown";
        if (type === "transcript" && typeof message.text === "string") {
          this.cb.onTranscript?.(
            message.text,
            Boolean(message.is_final),
            typeof message.role === "string" ? message.role : "user",
          );
        } else if (type === "tts_cancel") {
          this.browserSpeech.cancel();
          this.playbackResampler?.reset();
          this.playbackNode?.port.postMessage({ type: "flush" });
        } else if (type === "audio_ready") {
          this.setOutputRate(message.output_sample_rate);
          this.ready = true;
          if (!settled) {
            settled = true;
            window.clearTimeout(timeout);
            resolve();
          }
        } else if (type === "tts_start") {
          this.browserSpeech.cancel();
          this.setOutputRate(message.sample_rate);
        } else if (type === "tts_browser_fallback") {
          this.handleBrowserSpeech(message);
        } else if (type === "turn_complete" || type === "tts_end") {
          this.playbackResampler?.reset();
        }
        this.cb.onStatus?.(type, message);
      };
    });
  }

  private setOutputRate(value: unknown): void {
    const providerRate = typeof value === "number" && value > 0 ? value : 24_000;
    const contextRate = this.ctx?.sampleRate ?? 48_000;
    this.playbackResampler = new StreamingPcm16Resampler(providerRate, contextRate);
  }

  private handleAudio(pcm: ArrayBuffer): void {
    this.browserSpeech.cancel();
    if (!this.playbackResampler) this.setOutputRate(24_000);
    const converted = this.playbackResampler?.process(pcm) ?? pcm;
    if (converted.byteLength === 0) return;
    this.playbackNode?.port.postMessage({ type: "pcm", data: converted }, [converted]);
    this.cb.onAudio?.();
  }

  private handleBrowserSpeech(message: RealtimeStatusPayload): void {
    const id = typeof message.id === "string" ? message.id : "";
    const text = typeof message.text === "string" ? message.text : "";
    if (!id || !text.trim()) return;

    this.playbackResampler?.reset();
    this.playbackNode?.port.postMessage({ type: "flush" });
    const language = typeof message.language === "string" ? message.language : "en-US";
    const volume = typeof message.volume === "number" ? message.volume : 1;
    this.browserSpeech.speak(text, language, volume, {
      onStart: () => this.cb.onAudio?.(),
      onFinish: (outcome) => {
        if (outcome !== "ended") {
          this.cb.onStatus?.(`tts_browser_${outcome}`, { ...message, outcome });
        }
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: "tts_browser_done", id, outcome }));
        }
      },
    });
  }

  async disconnect(): Promise<void> {
    this.intentionalClose = true;
    await this.teardown(true);
  }

  private async teardown(sendStop: boolean): Promise<void> {
    const socket = this.ws;
    this.ws = null;
    this.ready = false;
    if (sendStop && socket?.readyState === WebSocket.OPEN) {
      try {
        socket.send(JSON.stringify({ type: "audio_stop" }));
      } catch {
        // The socket may close between readyState and send.
      }
    }
    socket?.close();
    this.browserSpeech.cancel();
    this.captureNode?.disconnect();
    this.captureSink?.disconnect();
    this.playbackNode?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    try {
      await this.ctx?.close();
    } catch {
      // Closing an already closed AudioContext is harmless.
    }
    this.captureNode = null;
    this.captureSink = null;
    this.playbackNode = null;
    this.playbackResampler = null;
    this.stream = null;
    this.ctx = null;
    this.inputMeter.reset();
    this.cb.onInputLevel?.(0);
  }
}
