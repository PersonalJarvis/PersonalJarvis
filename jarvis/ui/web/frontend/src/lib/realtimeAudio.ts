// Dedicated /ws/audio client for realtime voice. Separate from the JSON-only
// WSClient (src/lib/ws.ts): this one carries binary int16 PCM (arraybuffer).

export function buildAudioSocketUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  const token = (window as unknown as { __JARVIS_TOKEN?: string }).__JARVIS_TOKEN;
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}://${host}/ws/audio${query}`;
}

export type RealtimeCallbacks = {
  onTranscript?: (text: string, isFinal: boolean) => void;
  onStatus?: (status: string) => void;
};

export class RealtimeAudioClient {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;

  constructor(private cb: RealtimeCallbacks = {}) {}

  async connect(): Promise<void> {
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule(new URL("./pcm-worklet.ts", import.meta.url));

    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.captureNode = new AudioWorkletNode(this.ctx, "pcm-capture");
    this.playbackNode = new AudioWorkletNode(this.ctx, "pcm-playback");
    this.playbackNode.connect(this.ctx.destination);
    src.connect(this.captureNode);

    this.ws = new WebSocket(buildAudioSocketUrl());
    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = () => {
      this.ws?.send(JSON.stringify({ type: "audio_start", sample_rate: this.ctx?.sampleRate ?? 48000 }));
    };
    this.captureNode.port.onmessage = (e) => {
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(e.data as ArrayBuffer);
    };
    this.ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        const msg = JSON.parse(e.data);
        if (msg.type === "transcript") this.cb.onTranscript?.(msg.text, !!msg.is_final);
        else if (msg.type === "tts_cancel") this.playbackNode?.port.postMessage({ type: "flush" });
        else this.cb.onStatus?.(msg.type);
      } else {
        this.playbackNode?.port.postMessage({ type: "pcm", data: e.data }, [e.data as ArrayBuffer]);
      }
    };
  }

  async disconnect(): Promise<void> {
    try {
      this.ws?.send(JSON.stringify({ type: "audio_stop" }));
    } catch {
      // socket may already be closing
    }
    this.ws?.close();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.ws = null;
    this.ctx = null;
  }
}
