/**
 * useBrowserVoice — the browser half of the B2 voice bridge.
 *
 * Captures the microphone via an AudioWorklet (raw Float32 -> Int16 PCM at the
 * AudioContext rate), streams it as binary frames to the server's `/ws/audio`
 * WebSocket, and plays the 24 kHz int16 PCM that streams back via the Web Audio
 * API. The server (jarvis/browser_voice/) runs the STT -> Brain -> TTS turn loop
 * and resamples the capture rate down to 16 kHz, so the client sends at its own
 * context rate and does no resampling itself.
 *
 * RUNTIME IS BROWSER-ONLY: AudioWorklet + getUserMedia + Web Audio playback are
 * not exercisable in jsdom/Vitest. This module compiles and follows the standard
 * streaming-PCM pattern, but it needs a real-browser smoke test (localhost or
 * https — AudioWorklet requires a secure context).
 *
 * Lifecycle is generation-guarded: start() captures a generation id and abandons
 * itself (cleaning up only its own local stream/context) if stop() or a newer
 * start() bumped the generation across an await — so a Stop during a slow mic
 * permission prompt never leaks a dangling track.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface BrowserVoiceCallbacks {
  onTranscript?: (text: string, isFinal: boolean) => void;
  onTtsStart?: () => void;
  onTtsEnd?: () => void;
  onError?: (message: string) => void;
  /** BCP-47 / pin to forward to the server (empty -> server resolves). */
  languageCode?: string;
}

interface ControlMessage {
  type: string;
  text?: string;
  is_final?: boolean;
  sample_rate?: number;
}

/** TTS PCM streamed back from the server is always 24 kHz int16 mono. */
const TTS_SAMPLE_RATE = 24_000;

/**
 * The AudioWorklet processor source, loaded as a Blob module. It converts each
 * Float32 input block to Int16 and posts the raw buffer to the main thread; it
 * writes no output (stays connected to the graph only so `process` keeps firing).
 * The conversion is asymmetric ON PURPOSE: signed 16-bit spans -32768..+32767,
 * so the negative side scales by 0x8000 and the positive side by 0x7fff — do NOT
 * "simplify" it to one factor (that clips one polarity).
 */
const PCM_WORKLET_SRC = `
class PCMCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      const ch = input[0];
      const pcm = new Int16Array(ch.length);
      for (let i = 0; i < ch.length; i++) {
        let s = Math.max(-1, Math.min(1, ch[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor('pcm-capture', PCMCaptureProcessor);
`;

function browserVoiceWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/audio`;
}

export function useBrowserVoice(cb: BrowserVoiceCallbacks = {}) {
  const [active, setActive] = useState(false);

  const cbRef = useRef(cb);
  cbRef.current = cb;

  const wsRef = useRef<WebSocket | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);
  const micSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  // Playback scheduling cursor (seconds, in the AudioContext clock).
  const playCursorRef = useRef(0);
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  // Bumped on every start()/stop() so an in-flight start() can detect it was
  // superseded across an await and bail without touching the shared refs.
  const startGenRef = useRef(0);
  const activeRef = useRef(false);

  /** Drop everything still queued for playback (barge-in / tts_cancel / stop). */
  const flushPlayback = useCallback(() => {
    const queued = sourcesRef.current;
    sourcesRef.current = []; // clear BEFORE stopping so a deferred onended can't re-track
    for (const src of queued) {
      src.onended = null; // disarm the async onended filter (it would race a new chunk)
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
    }
    playCursorRef.current = ctxRef.current?.currentTime ?? 0;
  }, []);

  const stop = useCallback(() => {
    startGenRef.current++; // abandon any in-flight start()
    activeRef.current = false;
    const safe = (fn: () => void) => {
      try {
        fn();
      } catch {
        /* teardown is best-effort */
      }
    };
    // Stop queued playback BEFORE closing the context (avoids InvalidStateError).
    flushPlayback();
    safe(() => nodeRef.current?.disconnect());
    safe(() => micSourceRef.current?.disconnect());
    safe(() => streamRef.current?.getTracks().forEach((t) => t.stop()));
    safe(() => {
      const ws = wsRef.current;
      if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
        ws.close();
      }
    });
    safe(() => ctxRef.current?.close());
    nodeRef.current = null;
    micSourceRef.current = null;
    streamRef.current = null;
    wsRef.current = null;
    ctxRef.current = null;
    sourcesRef.current = [];
    playCursorRef.current = 0;
    setActive(false);
  }, [flushPlayback]);

  /** Schedule one inbound 24 kHz int16 PCM chunk gaplessly after the last. */
  const playPcmChunk = useCallback((buf: ArrayBuffer) => {
    const ctx = ctxRef.current;
    if (!ctx || buf.byteLength === 0) return;
    const i16 = new Int16Array(buf);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
    const audioBuf = ctx.createBuffer(1, f32.length, TTS_SAMPLE_RATE);
    audioBuf.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, playCursorRef.current);
    src.start(startAt);
    playCursorRef.current = startAt + audioBuf.duration;
    sourcesRef.current.push(src);
    src.onended = () => {
      sourcesRef.current = sourcesRef.current.filter((s) => s !== src);
    };
  }, []);

  const start = useCallback(async () => {
    if (activeRef.current) return;
    const myGen = ++startGenRef.current;
    const superseded = () => startGenRef.current !== myGen;

    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    const cleanupLocal = () => {
      try {
        stream?.getTracks().forEach((t) => t.stop());
      } catch {
        /* best-effort */
      }
      try {
        void ctx?.close();
      } catch {
        /* best-effort */
      }
    };

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (superseded()) return cleanupLocal();

      ctx = new AudioContext();
      // Created in a microtask continuation (after the await), so Chrome/Safari
      // start it suspended — resume or playback is silent.
      if (ctx.state === "suspended") await ctx.resume();
      if (superseded()) return cleanupLocal();

      const blobUrl = URL.createObjectURL(
        new Blob([PCM_WORKLET_SRC], { type: "application/javascript" }),
      );
      try {
        await ctx.audioWorklet.addModule(blobUrl);
      } finally {
        URL.revokeObjectURL(blobUrl);
      }
      if (superseded()) return cleanupLocal();

      // No awaits past this point: build the graph + sockets, then commit to the
      // shared refs atomically so a concurrent start()/stop() cannot interleave.
      const sampleRate = Math.round(ctx.sampleRate);
      const ws = new WebSocket(browserVoiceWsUrl());
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        ws.send(
          JSON.stringify({
            type: "audio_start",
            sample_rate: sampleRate,
            language: cbRef.current.languageCode ?? "",
          }),
        );
      };
      ws.onmessage = (ev: MessageEvent) => {
        if (ev.data instanceof ArrayBuffer) {
          playPcmChunk(ev.data);
          return;
        }
        let msg: ControlMessage;
        try {
          msg = JSON.parse(ev.data as string) as ControlMessage;
        } catch {
          return;
        }
        switch (msg.type) {
          case "transcript":
            cbRef.current.onTranscript?.(msg.text ?? "", !!msg.is_final);
            break;
          case "tts_start":
            cbRef.current.onTtsStart?.();
            break;
          case "tts_end":
            cbRef.current.onTtsEnd?.();
            break;
          case "tts_cancel":
            flushPlayback();
            break;
          default:
            break;
        }
      };
      ws.onerror = () => cbRef.current.onError?.("browser-voice connection error");
      // Only tear down if we still own this socket — prevents a double-stop when
      // stop() itself triggered the close (stop() nulls wsRef first).
      ws.onclose = () => {
        if (wsRef.current === ws) stop();
      };

      const node = new AudioWorkletNode(ctx, "pcm-capture");
      node.port.onmessage = (e: MessageEvent) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(e.data as ArrayBuffer);
      };
      const micSource = ctx.createMediaStreamSource(stream);
      micSource.connect(node);
      // Keep the node in the graph so `process` fires; it writes silence, so
      // routing it to the destination does not echo the microphone.
      node.connect(ctx.destination);

      streamRef.current = stream;
      ctxRef.current = ctx;
      wsRef.current = ws;
      nodeRef.current = node;
      micSourceRef.current = micSource;
      playCursorRef.current = ctx.currentTime;
      activeRef.current = true;
      setActive(true);
    } catch (err) {
      cbRef.current.onError?.(err instanceof Error ? err.message : String(err));
      cleanupLocal();
      if (!superseded()) stop();
    }
  }, [playPcmChunk, flushPlayback, stop]);

  /** Tear down on unmount. */
  useEffect(() => stop, [stop]);

  /** Ask the server to cut in-flight TTS (push-to-talk barge-in). */
  const bargeIn = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "barge_in" }));
    }
    flushPlayback();
  }, [flushPlayback]);

  return { active, start, stop, bargeIn };
}
