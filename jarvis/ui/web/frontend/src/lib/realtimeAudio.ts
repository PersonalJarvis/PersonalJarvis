// Dedicated /ws/audio client for browser-owned voice. Separate from the
// JSON-only WSClient: this socket carries raw mono PCM16 in both directions.

import { LevelMeter } from "./levelMeter";
import { mintWsTicket } from "./ws";
import pcmWorkletUrl from "./pcm-worklet.ts?worker&url";

export function buildAudioSocketUrl(ticket?: string | null): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  const base = `${proto}://${host}/ws/audio`;
  return ticket ? `${base}?ticket=${encodeURIComponent(ticket)}` : base;
}

export type RealtimeStatusPayload = Record<string, unknown>;

export type RealtimeCallbacks = {
  onTranscript?: (text: string, isFinal: boolean, role: string) => void;
  onStatus?: (status: string, payload: RealtimeStatusPayload) => void;
  onAudio?: () => void;
  /** Normalized 0..1 microphone input level, ~30 Hz while capturing. */
  onInputLevel?: (level: number) => void;
};

export type RealtimeAudioOptions = {
  /** The active provider needs a WebRTC offer to open its subscription transport. */
  requiresWebRtcOffer?: boolean;
  /**
   * How long one start attempt may take before the surface calls it dead.
   *
   * Comes from the backend's declared provider capability, never from a
   * provider name. Omitted (older backend, failed probe) keeps the historical
   * fixed budget.
   */
  startBudgetMs?: number;
};

/** Historical fixed budget for one realtime start attempt. */
const DEFAULT_START_BUDGET_MS = 20_000;

/** Bounded startup pre-roll, mirroring the desktop's 30 s replay window.
 *
 * Captured microphone PCM used to be DISCARDED until the backend answered
 * `audio_ready`. On a cold subscription transport that window is 15-25 s, so
 * whatever the user said first was simply gone — and because that transport
 * generates its responses from its own turn detection, nothing ever asked for
 * a repeat. Retaining the opening in order and replaying it once the socket
 * accepts audio gives the browser the same contract the desktop already has.
 * The cap is per-connection and drops the OLDEST frames, so a forgotten open
 * tab cannot grow memory without bound.
 */
const MAX_STARTUP_PREROLL_BYTES = 48_000 * 2 * 30; // 30 s of mono PCM16 @48 kHz

export type BrowserSpeechOutcome = "ended" | "error" | "unavailable";

export type BrowserRealtimeSupportIssue =
  | "secure_context"
  | "microphone_unavailable"
  | "audio_worklet_unavailable";

const ICE_GATHER_TIMEOUT_MS = 1_500;

/** One offer-only WebRTC transport for Codex subscription signalling.
 *
 * Audio capture and playback stay on Jarvis's PCM WebSocket. The peer exists
 * only to establish the provider transport: it receives no microphone track,
 * and its remote RTP track is deliberately not attached to an output element.
 * Codex mirrors output through `thread/realtime/outputAudio/delta`, allowing the
 * existing transcript scrub gate to approve PCM before it reaches speakers.
 */
export class RealtimeWebRtcTransport {
  private peer: RTCPeerConnection | null = null;

  async createOffer(): Promise<string | null> {
    this.close();
    if (typeof RTCPeerConnection !== "function") return null;

    const peer = new RTCPeerConnection();
    this.peer = peer;
    peer.addTransceiver("audio", { direction: "recvonly" });
    peer.createDataChannel("oai-events");
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await waitForIceGathering(peer);
    if (this.peer !== peer) return null;
    const sdp = peer.localDescription?.sdp ?? offer.sdp ?? "";
    return sdp.trim() || null;
  }

  async applyAnswer(sdp: string): Promise<void> {
    if (!this.peer) throw new Error("WebRTC answer arrived without an active offer");
    await this.peer.setRemoteDescription({ type: "answer", sdp });
  }

  close(): void {
    const peer = this.peer;
    this.peer = null;
    peer?.close();
  }
}

async function waitForIceGathering(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === "complete") return;
  await new Promise<void>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      globalThis.clearTimeout(timeout);
      peer.removeEventListener("icegatheringstatechange", onChange);
      resolve();
    };
    const onChange = () => {
      if (peer.iceGatheringState === "complete") finish();
    };
    const timeout = globalThis.setTimeout(finish, ICE_GATHER_TIMEOUT_MS);
    peer.addEventListener("icegatheringstatechange", onChange);
  });
}

export class RealtimeAudioSupportError extends Error {
  constructor(readonly issue: BrowserRealtimeSupportIssue) {
    super(`Browser Realtime Voice is unavailable: ${issue}`);
    this.name = "RealtimeAudioSupportError";
  }
}

/** Return the first browser capability that prevents microphone streaming. */
export function browserRealtimeSupportIssue(): BrowserRealtimeSupportIssue | null {
  if (typeof window === "undefined" || window.isSecureContext === false) {
    return "secure_context";
  }
  if (
    typeof navigator === "undefined" ||
    typeof navigator.mediaDevices?.getUserMedia !== "function"
  ) {
    return "microphone_unavailable";
  }
  if (typeof AudioContext !== "function" || typeof AudioWorkletNode !== "function") {
    return "audio_worklet_unavailable";
  }
  return null;
}

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

  /**
   * @param language BCP-47 tag resolved by the BACKEND's single turn-language
   *   resolver. Empty means "the backend did not say", and the engine's own
   *   default is then used — this layer must never invent one, because a
   *   second language decision here is exactly the per-layer re-derivation the
   *   output-language doctrine forbids.
   */
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
    if (language) utterance.lang = language;
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
  private webRtcTransport: RealtimeWebRtcTransport;
  private webRtcOfferSdp: string | null = null;
  private startupPreroll: ArrayBuffer[] = [];
  private startupPrerollBytes = 0;

  constructor(
    private cb: RealtimeCallbacks = {},
    private options: RealtimeAudioOptions = {},
  ) {
    this.webRtcTransport = new RealtimeWebRtcTransport();
  }

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
      const supportIssue = browserRealtimeSupportIssue();
      if (supportIssue) throw new RealtimeAudioSupportError(supportIssue);
      // Start ICE gathering before microphone/worklet setup. This hides nearly
      // all subscription signalling latency behind work the call already has
      // to do, including when subscription voice is an explicit fallback for
      // an API-first provider chain. Capture and scrubbed playback remain PCM.
      const webRtcOffer: Promise<{
        sdp: string | null;
        error: unknown | null;
      }> | null = this.options.requiresWebRtcOffer
        ? this.webRtcTransport.createOffer().then(
            (sdp) => ({ sdp, error: null }),
            (error: unknown) => ({ sdp: null, error }),
          )
        : null;
      this.ctx = new AudioContext({ latencyHint: "interactive" });
      if (!this.ctx.audioWorklet) {
        throw new RealtimeAudioSupportError("audio_worklet_unavailable");
      }
      await this.ctx.audioWorklet.addModule(pcmWorkletUrl);
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

      // Required transport bootstrap for subscription-backed Realtime. API
      // providers do not set this option and continue to use the PCM socket
      // alone. A subscription session cannot be opened or billed correctly
      // without its WebRTC peer, so this path fails closed.
      if (this.options.requiresWebRtcOffer) {
        const result = await webRtcOffer;
        if (result?.error) {
          this.webRtcTransport.close();
          this.webRtcOfferSdp = null;
          throw new Error("Subscription Realtime WebRTC signalling is unavailable", {
            cause: result.error,
          });
        }
        this.webRtcOfferSdp = result?.sdp ?? null;
        if (!this.webRtcOfferSdp) {
          this.webRtcTransport.close();
          throw new Error("Subscription Realtime requires WebRTC support");
        }
      }

      // Proactive one-time ticket: WebKit engines do not attach the HttpOnly
      // session cookie to a WS handshake (BUG-065). Minting over plain HTTP
      // first works on every engine; on a mint failure (e.g. older backend)
      // fall back to the cookie-only handshake, which Chromium still accepts.
      const ticket = await mintWsTicket();
      this.ws = new WebSocket(buildAudioSocketUrl(ticket));
      this.ws.binaryType = "arraybuffer";
      this.captureNode.port.onmessage = (event) => {
        const data = event.data as ArrayBuffer | { type?: string; rms?: number };
        if (data instanceof ArrayBuffer) {
          if (this.ready && this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(data);
          } else if (!this.ready) {
            this.retainStartupFrame(data);
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
    // Never shorter than the historical budget, and long enough for whatever
    // the active provider chain declared it needs. A cold subscription
    // transport spends 15-25 s spawning its app-server, verifying the live
    // account and negotiating WebRTC; giving up at a fixed 20 s reported that
    // legitimate negotiation to the user as a failed connection.
    const budgetMs = Math.max(
      DEFAULT_START_BUDGET_MS,
      Math.round(this.options.startBudgetMs ?? 0),
    );
    return new Promise((resolve, reject) => {
      let settled = false;
      const timeout = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error("Realtime voice connection timed out"));
      }, budgetMs);

      const fail = (error: Error) => {
        if (!settled) {
          settled = true;
          window.clearTimeout(timeout);
          reject(error);
        }
      };

      socket.onopen = () => {
        socket.send(
          JSON.stringify({
            type: "audio_start",
            sample_rate: this.ctx?.sampleRate ?? 48_000,
            ...(this.webRtcOfferSdp
              ? { webrtc_offer_sdp: this.webRtcOfferSdp }
              : {}),
          }),
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
          void this.finishAudioReady(message)
            .then(() => {
              this.ready = true;
              this.flushStartupPreroll();
              if (!settled) {
                settled = true;
                window.clearTimeout(timeout);
                resolve();
              }
              this.cb.onStatus?.(type, message);
            })
            .catch((error: unknown) => {
              const failure =
                error instanceof Error ? error : new Error(String(error));
              this.ready = false;
              fail(failure);
              socket.close();
            });
          return;
        } else if (type === "tts_start") {
          this.browserSpeech.cancel();
          this.setOutputRate(message.sample_rate);
        } else if (type === "tts_browser_fallback") {
          this.handleBrowserSpeech(message);
        } else if (type === "error_spoken") {
          // The realtime session's surface-TTS path: the trusted, scrub-clean
          // reply the provider itself did not (or must not) speak. The desktop
          // pipeline has always rendered it; this client dropped it silently,
          // which made a cancelled turn look like a dead call — and it is the
          // ONLY message that carries the turn's resolved output language.
          this.handleBrowserSpeech({
            ...message,
            id: typeof message.id === "string" ? message.id : "error_spoken",
          });
        } else if (type === "thinking" || type === "turn_complete" || type === "tts_end") {
          this.playbackResampler?.reset();
        }
        this.cb.onStatus?.(type, message);
      };
    });
  }

  /** Retain one captured frame while the transport is still negotiating. */
  private retainStartupFrame(frame: ArrayBuffer): void {
    this.startupPreroll.push(frame);
    this.startupPrerollBytes += frame.byteLength;
    while (
      this.startupPreroll.length > 1 &&
      this.startupPrerollBytes > MAX_STARTUP_PREROLL_BYTES
    ) {
      const dropped = this.startupPreroll.shift();
      this.startupPrerollBytes -= dropped?.byteLength ?? 0;
    }
  }

  /** Replay the retained opening once the socket accepts audio. */
  private flushStartupPreroll(): void {
    const retained = this.startupPreroll;
    this.startupPreroll = [];
    this.startupPrerollBytes = 0;
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    for (const frame of retained) {
      this.ws.send(frame);
    }
  }

  private async finishAudioReady(message: RealtimeStatusPayload): Promise<void> {
    const answer = message.webrtc_answer_sdp;
    const answerRequired =
      message.requires_webrtc_answer === true ||
      (message.requires_webrtc_answer === undefined &&
        this.options.requiresWebRtcOffer === true);
    if (typeof answer !== "string" || !answer.trim()) {
      this.webRtcTransport.close();
      if (answerRequired) {
        throw new Error("Subscription Realtime did not return a WebRTC answer");
      }
      return;
    }
    try {
      await this.webRtcTransport.applyAnswer(answer);
    } catch (error) {
      this.webRtcTransport.close();
      if (answerRequired) {
        throw new Error("Subscription Realtime returned an invalid WebRTC answer", {
          cause: error,
        });
      }
      // PCM remains authoritative. A broken or unsupported WebRTC answer must
      // not take API-backed browser voice down with it.
      console.warn("WebRTC answer could not be applied; continuing with PCM voice.", error);
      this.cb.onStatus?.("webrtc_transport_unavailable", {
        type: "webrtc_transport_unavailable",
      });
    }
  }

  private setOutputRate(value: unknown): void {
    const providerRate = typeof value === "number" && value > 0 ? value : 24_000;
    const contextRate = this.ctx?.sampleRate ?? 48_000;
    this.playbackResampler = new StreamingPcm16Resampler(providerRate, contextRate);
  }

  private handleAudio(pcm: ArrayBuffer): void {
    // This is authoritative for both API and subscription providers. The
    // subscription WebRTC peer intentionally does not play remote RTP; Codex's
    // documented sideband PCM therefore keeps Jarvis's scrub gate in the path.
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
    // Passed through verbatim from the backend's single turn-language
    // resolver. No default is substituted here: inventing one would be a
    // second language decision in a layer that has no business making it.
    const language = typeof message.language === "string" ? message.language : "";
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
    // A start that never reached `audio_ready` must not carry its retained
    // opening into the next connection attempt.
    this.startupPreroll = [];
    this.startupPrerollBytes = 0;
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
    this.webRtcTransport.close();
    this.webRtcOfferSdp = null;
    this.stream = null;
    this.ctx = null;
    this.inputMeter.reset();
    this.cb.onInputLevel?.(0);
  }
}
