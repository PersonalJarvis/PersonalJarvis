// Dedicated /ws/audio client for browser-owned voice. Separate from the
// JSON-only WSClient: this socket carries raw mono PCM16 in both directions.

import { LevelMeter } from "./levelMeter";
import { playListeningCue } from "./listeningCue";
import { MediaActivity, type MediaLevels } from "./mediaLevels";
import { requestConnect } from "./connectBudget";
import { mintWsTicket } from "./ws";
import { beginSpeechPlayback, clearSpeechPlayback, setTimedSpeechSession, spokenWordEnd, updateSpeechPlayback } from "./speechPlayback";
import { audibleContextTime, DevicePlaybackTimeline, type AudioInterval, type RenderedAudioInterval } from "./playbackTimeline";
import { TimedAudioFrame, TimedTextFrame, TimedSpeechTracker } from "./timedSpeech";
import { acquireRealtimeAudio, releaseRealtimeAudio, type PreparedRealtimeAudio } from "./realtimeAudioPreparation";
import { translate } from "@/i18n";

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
  onPlaybackState?: (active: boolean) => void;
  /** Normalized 0..1 microphone input level, ~30 Hz while capturing. */
  onInputLevel?: (level: number) => void;
  /**
   * Normalized 0..1 level of the ASSISTANT's voice, ~30 Hz while a session
   * is open, measured on the samples the playback worklet writes. `null`
   * when the tap goes away, so a renderer can tell "silent" from "not
   * observable here" (see lib/voiceOutputLevel).
   */
  onOutputLevel?: (level: number | null) => void;
};

export type RealtimeAudioOptions = {
  browserAudio?: boolean;
  /** The active provider needs a WebRTC offer to open its subscription transport. */
  requiresWebRtcOffer?: boolean;
  /** False for audio-only peers that acknowledge startup by connecting. */
  webRtcStartEventRequired?: boolean;
  /**
   * How long one start attempt may take before the surface calls it dead.
   *
   * Comes from the backend's declared provider capability, never from a
   * provider name. Omitted (older backend, failed probe) keeps the historical
   * fixed budget.
   */
  startBudgetMs?: number;
};

/**
 * How long after the last PCM packet the playback tap still counts as the
 * source of what is audible. Comfortably longer than the worklet's jitter
 * reserve, so the tail of a sentence keeps its levels, and far shorter than
 * a turn, so the browser-speech fallback never inherits them.
 */
const OUTPUT_TAP_TTL_MS = 600;

/** Historical fixed budget for one realtime start attempt. */
const DEFAULT_START_BUDGET_MS = 20_000;
// This starts only AFTER microphone permission is granted. A suspended audio
// device or stuck worklet must not leave the call connecting indefinitely.
const LOCAL_AUDIO_READY_TIMEOUT_MS = 10_000;

function awaitLocalStartup<T>(operation: Promise<T>, signal: AbortSignal, timeoutMs?: number): Promise<T> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = (error?: unknown, value?: T) => {
      if (settled) return;
      settled = true;
      if (timer !== undefined) clearTimeout(timer);
      signal.removeEventListener("abort", cancel);
      if (error !== undefined) reject(error); else resolve(value as T);
    };
    const cancel = () => finish(new Error("Voice start cancelled"));
    signal.addEventListener("abort", cancel, { once: true });
    if (timeoutMs !== undefined) {
      timer = setTimeout(() => finish(new Error("Local voice audio device did not become ready within 10 seconds")), timeoutMs);
    }
    void operation.then(value => finish(undefined, value), error => finish(error));
    if (signal.aborted) cancel();
  });
}

function awaitMicrophone(operation: Promise<MediaStream>, signal: AbortSignal): Promise<MediaStream> {
  const opened = awaitLocalStartup(operation, signal);
  if (typeof navigator.permissions?.query !== "function") return opened;
  try {
    // Read-only and off the critical path. When permission is already granted,
    // getUserMedia is waiting for hardware, not for a person to answer a prompt.
    const permission = navigator.permissions.query({ name: "microphone" as PermissionName }).then(
      status => status.state === "granted"
        ? awaitLocalStartup(opened, signal, LOCAL_AUDIO_READY_TIMEOUT_MS)
        : opened,
      () => opened, // Engines without microphone-query support retain normal permission behavior.
    );
    return Promise.race([opened, permission]);
  } catch {
    // Older permission implementations may reject this descriptor synchronously.
    return opened;
  }
}

/**
 * Consecutive quiet analyser frames before the WebRTC voice tap reports
 * listening (~700 ms at 30 Hz). Word gaps must not flip the Jarvis bar
 * back to listening mid-sentence.
 */
const REMOTE_SILENCE_HANGOVER_FRAMES = 21;

/** Bounded startup pre-roll, mirroring the desktop's 30 s replay window.
 *
 * Captured microphone PCM used to be DISCARDED until the backend answered
 * `audio_ready`. On a cold subscription transport that window is 15-25 s, so
 * whatever the user said first was simply gone — and because that transport
 * generates its responses from its own turn detection, nothing ever asked for
 * a repeat. Retaining the opening in order and replaying it once the socket
 * accepts audio gives the browser the same contract the desktop already has.
 * The per-connection cap fails explicitly if exceeded; it never silently
 * removes the beginning of a command.
 */
const STARTUP_PREROLL_SECONDS = 30;

export type BrowserSpeechOutcome = "ended" | "error" | "unavailable" | "cancelled";

export type BrowserRealtimeSupportIssue =
  | "secure_context"
  | "microphone_unavailable"
  | "audio_worklet_unavailable";

const ICE_GATHER_TIMEOUT_MS = 1_500;

/** Direct browser media for GPT-Live, or signalling-only for legacy PCM peers.
 * API Live uses a session.started datachannel acknowledgement. Subscription
 * Live accepts audio-only SDP and becomes ready when its peer connects.
 */
export class RealtimeWebRtcTransport {
  private peer: RTCPeerConnection | null = null;
  private player: HTMLAudioElement | null = null;
  private dataChannel: RTCDataChannel | null = null;
  private started: Promise<void> | null = null;
  private startupTimer: ReturnType<typeof setTimeout> | null = null;
  private armStartupTimeout: (() => void) | null = null;
  private finishStartup: ((error?: Error) => void) | null = null;
  private outputMuted = true;
  private outputVolume = 1;
  constructor(
    private onRemoteStream?: (stream: MediaStream) => void,
    private onStartupPhase?: (phase: string) => void,
  ) {}

  async createOffer(stream?: MediaStream, startEventRequired = true, startBudgetMs = 25_000): Promise<string | null> {
    this.close();
    if (typeof RTCPeerConnection !== "function") return null;

    const peer = new RTCPeerConnection();
    this.peer = peer;
    this.onStartupPhase?.("peer_created");
    if (stream) {
      for (const track of stream.getAudioTracks()) peer.addTrack(track, stream);
      this.player = new Audio();
      this.player.muted = this.outputMuted;
      this.player.volume = this.outputVolume;
      this.player.autoplay = true;
      peer.ontrack = (event) => {
        if (this.player) {
          this.player.srcObject = event.streams[0] ?? new MediaStream([event.track]);
          this.onRemoteStream?.(this.player.srcObject as MediaStream);
          void this.player.play().catch((error) => console.warn("Voice playback requires user activation", error));
        }
      };
    } else {
      peer.addTransceiver("audio", { direction: "recvonly" });
    }
    this.dataChannel = startEventRequired ? peer.createDataChannel("oai-events") : null;
    if (stream || !startEventRequired) {
      const channel = this.dataChannel;
      this.started = new Promise<void>((resolve, reject) => {
        let settled = false;
        const finish = (error?: Error) => {
          if (settled) return;
          settled = true;
          if (this.startupTimer) clearTimeout(this.startupTimer);
          this.startupTimer = null;
          this.armStartupTimeout = null;
          this.finishStartup = null;
          channel?.removeEventListener("message", onMessage);
          peer.removeEventListener("connectionstatechange", onConnectionChange);
          if (error) reject(error); else resolve();
        };
        const onMessage = (event: MessageEvent) => {
          try {
            if (JSON.parse(event.data).type === "session.started") finish();
          } catch { /* Non-JSON data cannot acknowledge startup. */ }
        };
        const onConnectionChange = () => {
          if (peer.connectionState === "failed" || peer.connectionState === "closed") {
            finish(new Error("GPT-Live media connection failed"));
          } else if (!startEventRequired && peer.connectionState === "connected") {
            finish();
          }
        };
        this.finishStartup = finish;
        this.armStartupTimeout = () => {
          this.startupTimer = setTimeout(() => finish(new Error("GPT-Live did not start")), startBudgetMs);
        };
        channel?.addEventListener("message", onMessage);
        peer.addEventListener("connectionstatechange", onConnectionChange);
        onConnectionChange();
      });
      void this.started.catch(() => undefined);
    }
    // Only the backend sideband executes tools or continues Responses work.
    const offerPending = peer.createOffer();
    this.onStartupPhase?.("offer_requested");
    const offer = await offerPending;
    await peer.setLocalDescription(offer);
    await waitForIceGathering(peer);
    if (this.peer !== peer) return null;
    const sdp = peer.localDescription?.sdp ?? offer.sdp ?? "";
    // SDP is a wire format: the terminal CRLF is required by Live's parser.
    return sdp.trim() ? sdp : null;
  }

  async applyAnswer(sdp: string): Promise<void> {
    const peer = this.peer;
    const started = this.started;
    if (!peer) throw new Error("WebRTC answer arrived without an active offer");
    // A permission prompt can outlive the media budget. Count media setup
    // only once an answer exists; no paid call opens during that prompt.
    this.armStartupTimeout?.();
    this.armStartupTimeout = null;
    await peer.setRemoteDescription({ type: "answer", sdp });
    if (this.peer !== peer) throw new Error("WebRTC connection closed during startup");
    if (started) await started;
  }

  muteOutput(): void {
    if (this.player) this.player.muted = true;
  }

  setOutputState(muted: boolean, volume: number): void {
    this.outputMuted = muted;
    this.outputVolume = volume;
    if (this.player) {
      // Keep the live RTP timeline running while silent; pause() would retain
      // old speech that could be heard after unmuting.
      this.player.muted = muted;
      this.player.volume = volume;
    }
  }

  close(): void {
    const peer = this.peer;
    this.peer = null;
    this.finishStartup?.(new Error("GPT-Live media connection closed"));
    this.dataChannel?.close();
    this.dataChannel = null;
    this.started = null;
    if (this.startupTimer) clearTimeout(this.startupTimer);
    this.startupTimer = null;
    this.player?.pause();
    if (this.player) this.player.srcObject = null;
    this.player = null;
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
  private finishCancelled: (() => void) | null = null;
  private playbackId: number | null = null;

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
    const playbackId = beginSpeechPlayback(text);
    this.playbackId = playbackId;
    let settled = false;
    let playing = false;
    const finish = (outcome: BrowserSpeechOutcome) => {
      if (settled || generation !== this.generation) return;
      settled = true;
      this.active = false;
      this.finishCancelled = null;
      if (outcome === "ended" && utterance.volume > 0) {
        updateSpeechPlayback(playbackId, "ended", text.length);
      } else {
        updateSpeechPlayback(playbackId, "cancelled");
      }
      handlers.onFinish(outcome);
    };
    this.finishCancelled = () => finish("cancelled");
    if (language) utterance.lang = language;
    utterance.volume = Math.max(0, Math.min(1, Number.isFinite(volume) ? volume : 1));
    utterance.onstart = () => {
      if (settled || generation !== this.generation) return;
      playing = true;
      updateSpeechPlayback(playbackId, "playing");
      handlers.onStart?.();
    };
    // These boundaries come from the speech engine's playback, so changing
    // rate, pausing or waiting in its queue needs no timer or tempo estimate.
    utterance.onboundary = (event) => {
      if (!playing || settled || generation !== this.generation || utterance.volume <= 0) return;
      if (event.name !== "word") return;
      updateSpeechPlayback(playbackId, "playing", spokenWordEnd(text, event.charIndex));
    };
    utterance.onpause = () => {
      if (settled || generation !== this.generation) return;
      playing = false;
      updateSpeechPlayback(playbackId, "paused");
    };
    utterance.onresume = () => {
      if (settled || generation !== this.generation) return;
      playing = true;
      updateSpeechPlayback(playbackId, "playing");
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

  cancel(notify = false): void {
    const wasActive = this.active;
    if (notify) this.finishCancelled?.();
    this.finishCancelled = null;
    this.generation += 1;
    if (this.playbackId !== null) updateSpeechPlayback(this.playbackId, "cancelled");
    this.playbackId = null;
    if (!wasActive) return;
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
  private preparedAudio: PreparedRealtimeAudio | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private captureSink: GainNode | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;
  private startupNode: AudioWorkletNode | null = null;
  private rtcInput: MediaStreamAudioDestinationNode | null = null;
  private captureStartedAtMs = 0;
  private controlReady = false;
  private receivedPrefix = false;
  private playbackResampler: StreamingPcm16Resampler | null = null;
  private connecting: Promise<void> | null = null;
  private localStartupAbort: AbortController | null = null;
  private ready = false;
  private intentionalClose = false;
  private inputMeter = new LevelMeter();
  // Its own normalizer: the assistant's voice and the room's microphone have
  // different noise floors and dynamics, and one shared adaptive floor would
  // let whichever is louder decide the other one's scale.
  private outputMeter = new LevelMeter();
  // When PCM last reached the playback worklet. The worklet renders (and so
  // measures) whatever is in its queue, which is silence whenever the reply
  // is NOT coming through it — most importantly during the SpeechSynthesis
  // fallback, where the voice is audible but never passes this graph.
  // Reporting that silence would be a lie in the other direction, so the tap
  // only speaks while it is the thing making the sound.
  private lastPcmAt = Number.NEGATIVE_INFINITY;
  private browserSpeech = new BrowserSpeechFallback();
  private webRtcTransport: RealtimeWebRtcTransport;
  private webRtcOfferSdp: string | null = null;
  private earlyAnswer: { sdp: string; ready: Promise<void> } | null = null;
  private startupAt = 0;
  private startupMarks: Record<string, number> = {};
  private startupPreroll: ArrayBuffer[] = [];
  private startupPrerollBytes = 0;
  private finalized: (() => void) | null = null;
  private serverClosed = false;
  private reconnecting = false;
  // Jarvis's microphone mute (pet strip, Jarvis Bar, orb), sent by the
  // backend. A WebRTC call carries this track straight to the provider, so
  // the backend cannot drop its audio; a disabled track sends silence.
  private inputMuted = false;
  private inputStopped = false;
  private outputMuted = true;
  private outputVolume = 1;
  private outputRevision = -1;
  private listeningCueConsumed = false;
  private stopListeningCue: (() => void) | null = null;
  private timedOutput = false;
  private timedEpoch = 0;
  private playbackGeneration = 0;
  private lastTimedAudioEnd = -1;
  private readonly deviceTimeline = new DevicePlaybackTimeline();
  private readonly speechTimeline = new TimedSpeechTracker();

  private flushPlayback(): void {
    this.playbackGeneration += 1;
    this.deviceTimeline.clear();
    this.speechTimeline.clear();
    this.playbackNode?.port.postMessage({ type: "flush", generation: this.playbackGeneration });
  }

  private selectOutputTransport(value: unknown, session: unknown): void {
    if (value !== "timed_pcm" && value !== "webrtc") return;
    this.timedOutput = value === "timed_pcm";
    if (typeof session === "string") setTimedSpeechSession(this.timedOutput ? session : "");
    // One speaker only. The peer still carries microphone input; timed PCM
    // owns output and its source-to-device clock instead of parallel RTP.
    this.webRtcTransport.setOutputState(this.outputMuted || this.timedOutput, this.outputVolume);
    if (this.timedOutput) {
      this.remoteMeter?.disconnect();
      if (this.remoteMeter) this.remoteMeter.port.onmessage = null;
      this.remoteSource?.disconnect();
    }
  }

  private acceptPlaybackEpoch(epoch: number): boolean {
    if (!Number.isSafeInteger(epoch) || epoch < 0) return false;
    if (epoch < this.timedEpoch) return false;
    if (epoch > this.timedEpoch) {
      this.flushPlayback();
      this.timedEpoch = epoch;
    }
    return true;
  }

  private applyOutputState(muted: unknown, volume: unknown, revision: unknown): void {
    if (typeof muted !== "boolean") return;
    const effectiveMuted = muted || this.intentionalClose || this.inputStopped;
    if (typeof revision === "number" && revision < this.outputRevision) return;
    if (typeof revision === "number") this.outputRevision = revision;
    const changed = this.outputMuted !== effectiveMuted;
    this.outputMuted = effectiveMuted;
    if (effectiveMuted || volume === 0) this.stopListeningCue?.();
    if (typeof volume === "number" && Number.isFinite(volume)) {
      this.outputVolume = Math.max(0, Math.min(1, volume));
    }
    this.webRtcTransport.setOutputState(effectiveMuted || this.timedOutput, this.outputVolume);
    if (changed) this.flushPlayback();
    this.playbackNode?.port.postMessage({
      type: "output_state", muted: effectiveMuted, volume: this.outputVolume, generation: this.playbackGeneration,
    });
    if (changed) {
      this.browserSpeech.cancel(true);
      this.playbackResampler?.reset();
      this.outputActivity.reset();
      this.outputLevel = 0;
      this.cb.onPlaybackState?.(false);
    }
  }

  constructor(
    private cb: RealtimeCallbacks = {},
    private options: RealtimeAudioOptions = {},
  ) {
    this.webRtcTransport = new RealtimeWebRtcTransport(
      stream => this.observeRemoteAudio(stream),
      phase => this.markStartup(phase),
    );
  }

  private remoteMeter: AudioWorkletNode | null = null;
  private remoteSource: MediaStreamAudioSourceNode | null = null;
  private outputActivity = new MediaActivity(0.004, REMOTE_SILENCE_HANGOVER_FRAMES);
  private inputActivity = new MediaActivity(0.008, 8);
  private inputLevel = 0;
  private outputLevel = 0;
  private lastMediaSendAt = Number.NEGATIVE_INFINITY;

  private markStartup(phase: string): void {
    if (phase in this.startupMarks || this.reconnecting || this.intentionalClose) return;
    this.startupMarks[phase] = Math.round(performance.now() - this.startupAt);
    console.info(`Voice startup phase=${phase} elapsed_ms=${this.startupMarks[phase]}`);
    if (this.ws?.readyState === WebSocket.OPEN) {
      // A new frontend can load before its backend restarts. Older servers
      // bound each message to their smaller phase allowlist; flush early
      // milestones in bounded batches, then send only the newly reached one.
      const marks = phase === "socket_open" ? Object.entries(this.startupMarks) : [[phase, this.startupMarks[phase]]];
      for (let start = 0; start < marks.length; start += 8) {
        this.ws.send(JSON.stringify({ type: "startup_timing", marks_ms: Object.fromEntries(marks.slice(start, start + 8)) }));
      }
    }
  }

  private sendMediaLevels(force = false): void {
    if (!this.options.browserAudio || !this.controlReady || this.intentionalClose || this.ws?.readyState !== WebSocket.OPEN) return;
    // Drop obsolete meter snapshots when the control transport stalls. The
    // next audio-clock tick sends current measurements after it drains.
    if (this.ws.bufferedAmount > 1024) return;
    const now = performance.now();
    if (!force && now - this.lastMediaSendAt < 100) return;
    const snapshot: MediaLevels = {
      type: "media_levels",
      input_level: this.inputLevel,
      output_level: this.outputLevel,
      input_active: this.inputActivity.active,
      playback_active: this.outputActivity.active,
    };
    this.ws.send(JSON.stringify(snapshot));
    this.lastMediaSendAt = now;
  }

  private observeOutputLevel(rms: number): void {
    if (this.outputMuted) rms = 0;
    if (rms > 0) this.markStartup("first_output_audio");
    this.outputLevel = this.outputMeter.push(rms);
    this.cb.onOutputLevel?.(this.outputLevel);
    const changed = this.outputActivity.update(rms);
    if (changed) {
      const active = this.outputActivity.active;
      this.cb.onPlaybackState?.(active);
      if (active) {
        this.cb.onAudio?.();
        this.cb.onStatus?.("speaking", {});
      }
      // Older Live servers still consume the edge. The level snapshot adds
      // microphone/output meters; only the server resolves the quiet phase.
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "playback_state", active }));
    }
    this.sendMediaLevels(changed);
  }

  private observeRemoteAudio(stream: MediaStream): void {
    if (!this.ctx || this.timedOutput) return;
    clearSpeechPlayback();
    // A suspended context measures only zeros: the assistant would talk
    // while the bar keeps showing listening. The call started from a user
    // gesture, so resuming here is allowed and makes the tap truthful.
    void this.ctx.resume().catch(error => console.warn("Voice meter could not resume", error));
    this.remoteMeter?.disconnect();
    if (this.remoteMeter) this.remoteMeter.port.onmessage = null;
    this.remoteSource?.disconnect();
    this.outputActivity.reset();
    this.cb.onPlaybackState?.(false);
    const meter = new AudioWorkletNode(this.ctx, "pcm-level");
    this.remoteMeter = meter;
    this.remoteSource = this.ctx.createMediaStreamSource(stream);
    this.remoteSource.connect(meter);
    meter.connect(this.ctx.destination);
    meter.port.onmessage = (event: MessageEvent) => {
      if (this.remoteMeter !== meter || this.intentionalClose) return;
      const rms = event.data?.rms;
      if (event.data?.type !== "level" || typeof rms !== "number" || !Number.isFinite(rms)) return;
      this.observeOutputLevel(rms);
    };
  }

  private syncInputTracks(): void {
    const enabled = !this.inputMuted && !this.inputStopped;
    this.stream?.getAudioTracks().forEach(track => { track.enabled = enabled; });
  }

  private applyInputMute(muted: unknown): void {
    if (typeof muted !== "boolean") return;
    const changed = this.inputMuted !== muted;
    this.inputMuted = muted;
    this.syncInputTracks();
    if (muted) {
      this.stopListeningCue?.();
      this.startupNode?.port.postMessage({ type: "suspend" });
      this.startupPreroll = [];
      this.startupPrerollBytes = 0;
    } else if (changed && !this.inputStopped) {
      this.startupNode?.port.postMessage({ type: this.ready ? "start" : "resume" });
    }
  }

  private stopInput(): void {
    this.stopListeningCue?.();
    this.inputStopped = true;
    this.syncInputTracks();
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
    const localStartupAbort = new AbortController();
    this.localStartupAbort = localStartupAbort;
    const markLocalStartup = (phase: string) => {
      if (!localStartupAbort.signal.aborted) this.markStartup(phase);
    };
    // A reused client starts a new source timeline. Keep the local render
    // generation monotonic so callbacks from the old worklet stay obsolete.
    this.timedOutput = false;
    this.timedEpoch = 0;
    this.lastTimedAudioEnd = -1;
    this.flushPlayback();
    setTimedSpeechSession("");
    this.startupAt = performance.now();
    this.startupMarks = {};
    this.intentionalClose = false;
    this.listeningCueConsumed = false;
    this.serverClosed = false;
    this.reconnecting = false;
    this.inputMuted = false;
    this.inputStopped = false;
    this.outputRevision = -1;
    this.applyOutputState(true, this.outputVolume, undefined);
    try {
      const supportIssue = browserRealtimeSupportIssue();
      if (supportIssue) throw new RealtimeAudioSupportError(supportIssue);
      this.preparedAudio = acquireRealtimeAudio();
      this.ctx = this.preparedAudio.context;
      this.markStartup(this.preparedAudio.prepared ? "context_prepared" : "context_created");
      if (!this.ctx.audioWorklet) {
        throw new RealtimeAudioSupportError("audio_worklet_unavailable");
      }
      // The worklet load, the microphone open, the one-time WS ticket and
      // the shared connect-budget turn are independent: acquiring them
      // concurrently keeps three disk/network waits off the wake path
      // instead of stacked end to end. The silent WebRTC destination also
      // allows offer generation to overlap permission and device setup.
      const setupStartedAt = performance.now();
      const workletReady = Promise.all([
        this.preparedAudio.loaded.then(() => markLocalStartup("worklet_loaded")),
        this.ctx.resume().then(() => markLocalStartup("context_running")),
      ])
        .then(() => markLocalStartup("worklet_ready"));
      // Observed again below, but a rejected worklet must not become an
      // unhandled rejection while a permission prompt holds getUserMedia.
      void workletReady.catch(() => undefined);
      const micReady = navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: { ideal: 1 },
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      // getUserMedia has no cancellation API. Release a grant that arrives
      // after Stop without ever attaching it to a graph or provider.
      void micReady.then(stream => {
        if (localStartupAbort.signal.aborted) stream.getTracks().forEach(track => track.stop());
      }, () => undefined); // The awaited operation below reports permission errors.
      const ticketReady = mintWsTicket();
      const turnReady = new Promise<void>((resolve) => {
        const cancelTurn = requestConnect(resolve);
        localStartupAbort.signal.addEventListener("abort", cancelTurn, { once: true });
      });
      this.markStartup("local_requests_dispatched");
      // Native destination/peer construction can block the main thread. Start
      // the independent device and network requests before entering that work.
      // Promise callbacks can only run afterward; their timestamps alone do
      // not measure the underlying module/device operation's duration.
      if (this.options.browserAudio && this.options.requiresWebRtcOffer) {
        this.markStartup("rtc_destination_begin");
        // This destination has no source yet: negotiate a silent track while
        // the microphone opens. The startup worklet later owns its only input.
        this.rtcInput = this.ctx.createMediaStreamDestination();
        this.rtcInput.channelCount = 1;
        this.markStartup("rtc_destination_ready");
      }
      // No raw microphone track enters the peer: pcm-startup is the sole
      // source and stays silent until both media and control are ready.
      const webRtcOffer: Promise<{
        sdp: string | null;
        error: unknown | null;
      }> | null = this.options.requiresWebRtcOffer
        ? this.webRtcTransport.createOffer(this.rtcInput?.stream, this.options.webRtcStartEventRequired, this.options.startBudgetMs).then(
            (sdp) => {
              if (sdp) markLocalStartup("offer_ready");
              return { sdp, error: null };
            },
            (error: unknown) => ({ sdp: null, error }),
          )
        : null;
      this.stream = await awaitMicrophone(micReady, localStartupAbort.signal);
      if (this.intentionalClose) throw new Error("Voice start cancelled");
      this.markStartup("microphone_ready");
      await awaitLocalStartup(workletReady, localStartupAbort.signal, LOCAL_AUDIO_READY_TIMEOUT_MS);
      if (this.intentionalClose) throw new Error("Voice start cancelled");
      const setupMs = Math.round(performance.now() - setupStartedAt);
      const source = this.ctx.createMediaStreamSource(this.stream);
      this.captureNode = new AudioWorkletNode(this.ctx, "pcm-capture");
      // Install capture before any offer/ticket/network await. No opening
      // frame may depend on the handshake finishing first.
      this.captureNode.port.onmessage = (event) => this.handleCapture(event);
      this.playbackNode = new AudioWorkletNode(this.ctx, "pcm-playback");
      this.playbackNode.port.onmessage = (event: MessageEvent) => {
        // RTP is measured by pcm-level. The unused PCM queue produces zeros
        // and must not erase the real output meter thirty times per second.
        if (this.options.browserAudio && this.options.requiresWebRtcOffer && !this.timedOutput) return;
        if (event.data?.type === "playback") {
          if (!this.ctx || this.outputMuted || event.data.generation !== this.playbackGeneration) return;
          this.deviceTimeline.rendered(event.data.spans as RenderedAudioInterval[]);
          if (this.ctx.state !== "suspended" && this.ctx.state !== "closed") {
            this.speechTimeline.played(this.deviceTimeline.advance(audibleContextTime(this.ctx)));
          }
          return;
        }
        const data = event.data as { type?: string; rms?: number } | null;
        if (data && data.type === "level" && typeof data.rms === "number") {
          const live = performance.now() - this.lastPcmAt <= OUTPUT_TAP_TTL_MS;
          if (this.options.browserAudio) this.observeOutputLevel(live ? data.rms : 0);
          else this.cb.onOutputLevel?.(live ? this.outputMeter.push(data.rms) : null);
        }
      };
      // Keep the capture worklet in the active audio graph without feeding the
      // microphone back to the user. Browser AEC still sees the real playback
      // node connected below and can remove it from captured audio.
      this.captureSink = this.ctx.createGain();
      this.captureSink.gain.value = 0;
      this.captureStartedAtMs = Date.now();
      source.connect(this.captureNode);
      this.captureNode.connect(this.captureSink);
      this.captureSink.connect(this.ctx.destination);
      this.playbackNode.connect(this.ctx.destination);
      if (this.options.browserAudio && this.options.requiresWebRtcOffer) {
        this.startupNode = new AudioWorkletNode(this.ctx, "pcm-startup");
        source.connect(this.startupNode);
        this.startupNode.connect(this.rtcInput!);
        this.startupNode.port.onmessage = (event: MessageEvent) => {
          if (event.data?.type === "input_started") this.markStartup("input_started");
          else if (event.data?.type === "input_caught_up") this.markStartup("input_caught_up");
          if (event.data?.type !== "error" || this.intentionalClose) return;
          this.cb.onStatus?.("provider_error", { error: translate("voice.startup_audio_lost") });
          void this.disconnect();
        };
      }
      this.markStartup("capture_ready");

      // Required transport bootstrap for subscription-backed Realtime. API
      // providers do not set this option and continue to use the PCM socket
      // alone. A subscription session cannot be opened or billed correctly
      // without its WebRTC peer, so this path fails closed.
      if (this.options.requiresWebRtcOffer) {
        const result = await awaitLocalStartup(Promise.resolve(webRtcOffer), localStartupAbort.signal);
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
      // The ticket was minted concurrently with the microphone setup above,
      // and the budget turn was queued there too, so both are (nearly) free
      // by now instead of two more serial waits on the wake path.
      const ticket = await awaitLocalStartup(ticketReady, localStartupAbort.signal);
      await awaitLocalStartup(turnReady, localStartupAbort.signal);
      console.info(`Voice start setup took ${setupMs} ms (mic/worklet/ticket).`);
      if (this.intentionalClose) throw new Error("Voice start cancelled");
      this.ws = new WebSocket(buildAudioSocketUrl(ticket));
      this.ws.binaryType = "arraybuffer";
      await this.waitUntilReady(this.ws);
    } catch (error) {
      localStartupAbort.abort();
      await this.teardown(false);
      throw error instanceof Error ? error : new Error(String(error));
    }
  }

  private handleCapture(event: MessageEvent): void {
    if (this.intentionalClose) return;
    const data = event.data as ArrayBuffer | { type?: string; rms?: number };
    if (data instanceof ArrayBuffer) {
      this.markStartup("first_capture_frame");
      if (this.inputMuted || this.inputStopped) return;
      if (this.options.browserAudio && this.options.requiresWebRtcOffer) return;
      if (this.ready && this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(data);
      } else if (!this.ready && !this.reconnecting) {
        this.retainStartupFrame(data);
      }
      return;
    }
    if (data && data.type === "level" && typeof data.rms === "number") {
      this.inputLevel = this.inputMeter.push(data.rms);
      this.cb.onInputLevel?.(this.inputLevel);
      const changed = this.inputActivity.update(data.rms);
      this.sendMediaLevels(changed);
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
            capture_started_at_ms: this.captureStartedAtMs,
            ...(this.webRtcOfferSdp
              ? { webrtc_offer_sdp: this.webRtcOfferSdp }
              : {}),
          }),
        );
        this.markStartup("socket_open");
      };
      socket.onerror = () => fail(new Error("Realtime voice socket failed"));
      socket.onclose = (event) => {
        this.ready = false;
        this.flushPlayback();
        setTimedSpeechSession("");
        this.startupNode?.port.postMessage({ type: "suspend" });
        this.stopInput();
        this.webRtcTransport.close();
        fail(new Error(event.reason || "Voice connection closed"));
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
        if (type === "audio_timed") {
          const parsed = TimedAudioFrame.safeParse(message);
          if (!parsed.success || !this.timedOutput || !this.acceptPlaybackEpoch(parsed.data.epoch)) return;
          const frame = parsed.data;
          // A sideband reattach can repeat a reflected frame. Replaying it
          // would move audio backward even though the transcript is revisioned.
          if (frame.end_ms <= this.lastTimedAudioEnd) return;
          try {
            const raw = atob(frame.audio);
            if (raw.length % 2) throw new Error("Invalid PCM16 length");
            const bytes = Uint8Array.from(raw, char => char.charCodeAt(0));
            const overlap = Math.max(0, this.lastTimedAudioEnd - frame.start_ms);
            const skip = Math.min(bytes.length / 2, Math.ceil(
              bytes.length / 2 * overlap / (frame.end_ms - frame.start_ms),
            ));
            const startMs = frame.start_ms + (frame.end_ms - frame.start_ms) * skip / (bytes.length / 2);
            this.lastTimedAudioEnd = frame.end_ms;
            if (skip * 2 < bytes.length) {
              this.handleAudio(bytes.buffer.slice(skip * 2), { startMs, endMs: frame.end_ms });
            }
          } catch {
            console.warn("Invalid timed voice audio was discarded");
          }
          return;
        } else if (type === "speech_timing") {
          const parsed = TimedTextFrame.safeParse(message);
          if (parsed.success && this.timedOutput && this.acceptPlaybackEpoch(parsed.data.epoch)) {
            this.speechTimeline.text(parsed.data);
          }
          return;
        } else if (type === "transcript" && typeof message.text === "string") {
          this.cb.onTranscript?.(
            message.text,
            Boolean(message.is_final),
            typeof message.role === "string" ? message.role : "user",
          );
        } else if (type === "reconnecting") {
          this.lastTimedAudioEnd = -1;
          this.earlyAnswer = null;
          this.reconnecting = true;
          this.ready = false;
          this.controlReady = false;
          this.startupNode?.port.postMessage({ type: "suspend" });
          this.outputActivity.reset();
          this.inputActivity.reset();
          this.cb.onPlaybackState?.(false);
          this.startupPreroll = [];
          this.startupPrerollBytes = 0;
          this.flushPlayback();
        } else if (type === "reconnect_offer") {
          void this.webRtcTransport.createOffer(this.rtcInput?.stream ?? this.stream ?? undefined, this.options.webRtcStartEventRequired, this.options.startBudgetMs).then(sdp => {
            if (sdp && socket.readyState === WebSocket.OPEN && !this.intentionalClose) {
              socket.send(JSON.stringify({ type: "reconnect_offer", request_id: message.request_id, sdp }));
            }
          }).catch(error => {
            console.warn("Voice reconnection offer failed", error);
            this.cb.onStatus?.("provider_error", { error: translate("voice.reconnect_failed") });
          });
        } else if (type === "audio_stopping") {
          this.startupNode?.port.postMessage({ type: "suspend" });
          this.webRtcTransport.muteOutput();
          this.stopInput();
          this.applyOutputState(true, this.outputVolume, undefined);
        } else if (type === "output_state") {
          this.applyOutputState(message.muted, message.volume, message.revision);
        } else if (type === "input_mute") {
          this.applyInputMute(message.muted);
        } else if (type === "audio_closed") {
          this.serverClosed = true;
          this.finalized?.();
        } else if (type === "tts_cancel" || type === "audio_clear") {
          if (typeof message.epoch === "number" && !this.acceptPlaybackEpoch(message.epoch)) return;
          this.browserSpeech.cancel();
          this.playbackResampler?.reset();
          this.flushPlayback();
          if (this.options.browserAudio && (!this.options.requiresWebRtcOffer || this.timedOutput)) {
            this.outputActivity.reset();
            this.outputLevel = 0;
            this.cb.onPlaybackState?.(false);
            this.sendMediaLevels(true);
          }
        } else if (type === "input_prefix") {
          try { this.receiveInputPrefix(message); }
          catch (error) {
            fail(error instanceof Error ? error : new Error(String(error)));
            socket.close();
          }
          return;
        } else if (type === "audio_transport") {
          this.selectOutputTransport(message.output_transport, message.session_id);
          this.applyOutputState(message.output_muted, message.output_volume, message.output_revision);
          // SDP alone is not readiness. ICE/DTLS may overlap the server's
          // sideband attach, while pcm-startup continues to output silence.
          const sdp = message.webrtc_answer_sdp;
          if (!this.options.browserAudio || this.earlyAnswer || typeof sdp !== "string" || !sdp.trim()) {
            fail(new Error("Unexpected voice transport answer"));
            socket.close();
            return;
          }
          this.markStartup("answer_received");
          const ready = this.webRtcTransport.applyAnswer(sdp).then(() => this.markStartup("media_connected"));
          this.earlyAnswer = { sdp, ready };
          void ready.catch(error => {
            fail(error instanceof Error ? error : new Error(String(error)));
            socket.close();
          });
          return;
        } else if (type === "audio_ready") {
          this.markStartup("control_ready");
          this.selectOutputTransport(message.output_transport, message.session_id);
          // The local control channel can already relay levels while RTP
          // finishes its handshake. Native and browser bars share this input.
          this.controlReady = true;
          // Before the retained opening is released below.
          this.applyInputMute(message.input_muted);
          this.applyOutputState(message.output_muted ?? false, message.output_volume, message.output_revision);
          this.sendMediaLevels(true);
          this.setOutputRate(message.output_sample_rate);
          void this.finishAudioReady(message)
            .then(() => {
              if (this.intentionalClose || this.ws !== socket || socket.readyState !== WebSocket.OPEN) {
                throw new Error("Voice start cancelled");
              }
              this.ready = true;
              if (!this.inputMuted && !this.inputStopped) {
                this.startupNode?.port.postMessage({ type: "start" });
                this.markStartup("input_released");
              }
              this.reconnecting = false;
              this.sendMediaLevels(true);
              this.flushStartupPreroll();
              if (!this.listeningCueConsumed) {
                this.listeningCueConsumed = true;
                // An older backend cannot confirm the sound preference yet.
                if (this.ctx && message.sound_effects === true && !this.inputMuted &&
                    !this.inputStopped && !this.outputMuted) {
                  this.stopListeningCue = playListeningCue(this.ctx, this.outputVolume);
                }
              }
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
    if (this.startupPrerollBytes > (this.ctx?.sampleRate ?? 48000) * 2 * STARTUP_PREROLL_SECONDS) {
      this.cb.onStatus?.("provider_error", { error: translate("voice.startup_audio_lost") });
      void this.disconnect();
    }
  }

  private receiveInputPrefix(message: RealtimeStatusPayload): void {
    if (this.ready || this.reconnecting || this.receivedPrefix || !this.ctx) {
      throw new Error("Unexpected wake audio prefix");
    }
    const rate = message.sample_rate;
    const encoded = message.audio;
    if (typeof rate !== "number" || !Number.isInteger(rate) || rate < 8_000 || rate > 192_000 ||
        typeof encoded !== "string" || encoded.length > rate * 2 * 30 * 4 / 3 + 4) {
      throw new Error("Invalid wake audio prefix");
    }
    const raw = atob(encoded);
    if (raw.length % 2) throw new Error("Invalid wake PCM16 prefix");
    const bytes = Uint8Array.from(raw, char => char.charCodeAt(0));
    const resampler = new StreamingPcm16Resampler(rate, this.ctx.sampleRate);
    const pcm = resampler.process(bytes.buffer);
    this.receivedPrefix = true;
    if (this.inputMuted || this.inputStopped) return;
    if (this.startupNode) {
      const samples = Float32Array.from(new Int16Array(pcm), value => value / 32768);
      this.startupNode.port.postMessage({ type: "prefix", samples }, [samples.buffer]);
    } else {
      // Gemini/local use the existing PCM socket, with the same once-only
      // prefix ahead of the browser's already retained opening.
      this.startupPreroll.unshift(pcm);
      this.startupPrerollBytes += pcm.byteLength;
      if (this.startupPrerollBytes > this.ctx.sampleRate * 2 * STARTUP_PREROLL_SECONDS) throw new Error("Voice startup buffer exceeded");
    }
  }

  /** Replay the retained opening once the socket accepts audio. */
  private flushStartupPreroll(): void {
    const retained = this.startupPreroll;
    this.startupPreroll = [];
    this.startupPrerollBytes = 0;
    if (this.ws?.readyState !== WebSocket.OPEN || this.inputMuted || this.inputStopped) return;
    for (const frame of retained) {
      this.ws.send(frame);
    }
  }

  private async finishAudioReady(message: RealtimeStatusPayload): Promise<void> {
    // Reattaching the control socket keeps the established media peer and SDP.
    if (message.reuse_webrtc === true) return;
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
      if (this.earlyAnswer) {
        if (this.earlyAnswer.sdp !== answer) throw new Error("Voice transport answer changed during startup");
        await this.earlyAnswer.ready;
      } else {
        this.markStartup("answer_received");
        await this.webRtcTransport.applyAnswer(answer);
        this.markStartup("media_connected");
      }
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

  private handleAudio(pcm: ArrayBuffer, timing?: AudioInterval): void {
    if (pcm.byteLength) this.markStartup("first_output_packet");
    if (this.outputMuted) return;
    // Timed sessions mute the peer's RTP element before negotiation. Only
    // this worklet owns their audible output and its source-time mapping.
    this.browserSpeech.cancel();
    clearSpeechPlayback();
    if (!this.playbackResampler) this.setOutputRate(24_000);
    const converted = this.playbackResampler?.process(pcm) ?? pcm;
    if (converted.byteLength === 0) return;
    this.playbackNode?.port.postMessage({ type: "pcm", data: converted, ...timing }, [converted]);
    this.lastPcmAt = performance.now();
    if (!this.options.browserAudio) this.cb.onAudio?.();
  }

  private handleBrowserSpeech(message: RealtimeStatusPayload): void {
    const id = typeof message.id === "string" ? message.id : "";
    const text = typeof message.text === "string" ? message.text : "";
    if (!id || !text.trim()) return;
    if (this.outputMuted) {
      this.ws?.send(JSON.stringify({ type: "tts_browser_done", id, outcome: "cancelled" }));
      return;
    }

    this.playbackResampler?.reset();
    this.flushPlayback();
    // Passed through verbatim from the backend's single turn-language
    // resolver. No default is substituted here: inventing one would be a
    // second language decision in a layer that has no business making it.
    const language = typeof message.language === "string" ? message.language : "";
    const volume = this.outputVolume;
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
    this.localStartupAbort?.abort();
    this.applyOutputState(true, this.outputVolume, undefined);
    if (this.options.browserAudio && this.ready && !this.serverClosed && this.ws?.readyState === WebSocket.OPEN) {
      this.ready = false;
      this.webRtcTransport.muteOutput();
      this.stopInput();
      await new Promise<void>(resolve => {
        const timer = window.setTimeout(resolve, 16_000);
        this.finalized = () => { window.clearTimeout(timer); resolve(); };
        this.ws?.send(JSON.stringify({ type: "audio_stop" }));
      });
      this.finalized = null;
      await this.teardown(false);
    } else {
      await this.teardown(true);
    }
  }

  private async teardown(sendStop: boolean): Promise<void> {
    this.localStartupAbort?.abort();
    this.localStartupAbort = null;
    this.stopListeningCue?.();
    this.stopListeningCue = null;
    if (this.remoteMeter) this.remoteMeter.port.onmessage = null;
    this.remoteMeter?.disconnect();
    this.remoteMeter = null;
    this.remoteSource?.disconnect();
    this.remoteSource = null;
    this.outputActivity.reset();
    this.inputActivity.reset();
    this.inputLevel = 0;
    this.outputLevel = 0;
    this.lastMediaSendAt = Number.NEGATIVE_INFINITY;
    this.cb.onPlaybackState?.(false);
    const socket = this.ws;
    this.ws = null;
    this.ready = false;
    // A start that never reached `audio_ready` must not carry its retained
    // opening into the next connection attempt.
    this.startupPreroll = [];
    this.startupPrerollBytes = 0;
    this.controlReady = false;
    this.receivedPrefix = false;
    this.startupNode?.port.postMessage({ type: "suspend" });
    this.startupNode?.disconnect();
    this.rtcInput?.stream.getTracks().forEach(track => track.stop());
    this.startupNode = null;
    this.rtcInput = null;
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
    const preparedAudio = this.preparedAudio;
    this.preparedAudio = null;
    this.captureNode = null;
    this.captureSink = null;
    this.playbackNode = null;
    this.playbackResampler = null;
    this.webRtcTransport.close();
    this.webRtcOfferSdp = null;
    this.earlyAnswer = null;
    this.stream = null;
    this.ctx = null;
    this.inputMeter.reset();
    this.outputMeter.reset();
    this.cb.onInputLevel?.(0);
    // null, not 0: playback is gone, so nothing may keep drawing a voice.
    this.cb.onOutputLevel?.(null);
    // Native close can itself stall after device failure. Tracks, graph and
    // peer are already gone; retirement owns only this old context and never
    // delays Stop/start failure or mutates a later call. The pool prevents
    // idle replacement preparation until native cleanup actually settles.
    if (preparedAudio) void releaseRealtimeAudio(preparedAudio);
  }
}
