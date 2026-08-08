import { RealtimeWebRtcTransport } from "./realtimeAudio";
import {
  publishRealtimeTransportIssue,
  type RealtimeTransportIssue,
} from "./realtimeTransportIssue";
import { fetchRealtimeOfferRequirement } from "./voiceApi";
import { mintWsTicket } from "./ws";

declare global {
  interface Window {
    __JARVIS_REALTIME_BROKER_TOKEN?: string;
  }
}

/** First retry delay; each further failure doubles it up to the cap. */
const RECONNECT_DELAY_MS = 1_500;
const RECONNECT_DELAY_MAX_MS = 30_000;
/**
 * Consecutive failures after which the broker stops and says so.
 *
 * A fixed 1.5 s loop against a socket that answers 4401/1008 is a spin, not a
 * retry: the backend refuses until `[voice].mode` is realtime, and a wrong
 * capability refuses forever. Backing off and then reporting an honest
 * terminal state beats hammering a socket that will never accept us.
 */
const RECONNECT_MAX_ATTEMPTS = 6;
const WS_OPEN = 1;

type OfferTransport = Pick<
  RealtimeWebRtcTransport,
  "createOffer" | "applyAnswer" | "close"
>;

type BrokerDeps = {
  createTransport: () => OfferTransport;
  createSocket: (url: string) => WebSocket;
  mintTicket: () => Promise<string | null>;
  readDesktopCapability: () => string;
  readOfferRequirement: () => Promise<boolean | null>;
  createOfferId: () => string;
  schedule: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  cancelSchedule: (timer: ReturnType<typeof setTimeout>) => void;
};

function defaultOfferId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `rtc-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function buildRealtimeTransportSocketUrl(ticket?: string | null): string {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const base = `${protocol}://${window.location.host}/ws/realtime-transport`;
  return ticket ? `${base}?ticket=${encodeURIComponent(ticket)}` : base;
}

const DEFAULT_DEPS: BrokerDeps = {
  createTransport: () => new RealtimeWebRtcTransport(),
  createSocket: (url) => new WebSocket(url),
  mintTicket: mintWsTicket,
  readDesktopCapability: () =>
    window.__JARVIS_REALTIME_BROKER_TOKEN?.trim() ?? "",
  readOfferRequirement: fetchRealtimeOfferRequirement,
  createOfferId: defaultOfferId,
  schedule: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
  cancelSchedule: (timer) => globalThis.clearTimeout(timer),
};

/** Keeps one offer registered for native subscription voice sessions.
 *
 * The broker runs only in the embedded desktop WebView after UI
 * authentication. It does not request a microphone or play the provider's RTP
 * media; Codex's sideband PCM remains authoritative so Jarvis can scrub it
 * before playback. A native voice session consumes the registered offer, then
 * releases it so the broker can publish a fresh one on the same socket.
 */
export class RealtimeTransportBroker {
  private readonly deps: BrokerDeps;
  private socket: WebSocket | null = null;
  private transport: OfferTransport | null = null;
  private offerId: string | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  private generation = 0;
  private failures = 0;

  constructor(deps: Partial<BrokerDeps> = {}) {
    this.deps = { ...DEFAULT_DEPS, ...deps };
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.failures = 0;
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.generation += 1;
    if (this.reconnectTimer !== null) {
      this.deps.cancelSchedule(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.releaseTransport();
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }

  /** Stop retrying and name the blocker on every status surface. */
  private fail(issue: RealtimeTransportIssue, detail: string): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) {
      this.deps.cancelSchedule(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    console.warn(`Realtime transport broker unavailable (${issue}): ${detail}`);
    void this.publishFailure(issue);
  }

  /**
   * Surface a terminal issue only when an offer is genuinely wanted.
   *
   * The broker is also mounted during the switch "prepare" window, before this
   * client can know whether the transport it is switching TO needs a browser
   * offer — and no built-in provider currently does. A failure published there
   * accuses a capability nothing uses and sends the reader after the wrong bug.
   * The backend capability decides (AP-21); an UNKNOWN answer still publishes,
   * because a hidden real blocker is the worse outcome.
   */
  private async publishFailure(issue: RealtimeTransportIssue): Promise<void> {
    const generation = this.generation;
    const required = await this.deps.readOfferRequirement();
    // Any restart or reconnect bumps the generation, so a failure that has
    // already been superseded must not land on the status row.
    if (generation !== this.generation) return;
    if (required === false) {
      console.info(
        `Realtime transport broker issue withheld (${issue}): no configured provider needs a browser WebRTC offer.`,
      );
      return;
    }
    publishRealtimeTransportIssue(issue);
  }

  private async connect(): Promise<void> {
    const generation = ++this.generation;
    try {
      const desktopCapability = this.deps.readDesktopCapability();
      if (!desktopCapability) {
        // The native host injects this shortly after the first paint, so an
        // empty value early on is a race, not a verdict. Retrying (with
        // backoff) is what turns it back into a working call; the previous
        // bare `return` left the broker permanently dead and every later
        // subscription call hung with nothing logged and nothing shown.
        this.noteFailure("capability_missing", "desktop capability not injected yet");
        return;
      }
      const ticket = await this.deps.mintTicket();
      if (this.stopped || generation !== this.generation) return;
      const socket = this.deps.createSocket(buildRealtimeTransportSocketUrl(ticket));
      this.socket = socket;
      socket.onopen = () => {
        if (this.stopped || this.socket !== socket) return;
        // The normal UI session is deliberately insufficient here. Only the
        // embedded desktop WebView receives this separate process capability.
        socket.send(
          JSON.stringify({
            type: "authenticate",
            desktop_capability: desktopCapability,
          }),
        );
        void this.publishOffer(socket);
      };
      socket.onmessage = (event) => this.handleMessage(socket, event);
      socket.onerror = () => socket.close();
      socket.onclose = (event?: CloseEvent) => {
        if (this.socket !== socket) return;
        this.socket = null;
        this.releaseTransport();
        // `event` is optional on purpose: a close can also be synthesised by
        // the host (and by test doubles) without a CloseEvent, and reading
        // `.code` off nothing would turn a reconnect into a crash.
        const code = event?.code;
        const reason = event?.reason;
        this.noteFailure(
          "socket_unreachable",
          `broker socket closed (${code ?? "no code"}${reason ? `: ${reason}` : ""})`,
        );
      };
    } catch (error) {
      this.noteFailure("socket_unreachable", `broker socket failed: ${String(error)}`);
    }
  }

  private async publishOffer(socket: WebSocket): Promise<void> {
    const generation = ++this.generation;
    this.releaseTransport();
    const transport = this.deps.createTransport();
    this.transport = transport;
    try {
      const sdp = await transport.createOffer();
      if (
        this.stopped ||
        this.socket !== socket ||
        generation !== this.generation ||
        socket.readyState !== WS_OPEN
      ) {
        if (this.transport === transport) this.releaseTransport();
        return;
      }
      if (!sdp) {
        socket.send(
          JSON.stringify({
            type: "unavailable",
            reason: "webrtc_offer_unavailable",
          }),
        );
        // A missing RTCPeerConnection is a page capability failure, not a
        // transient socket failure. Report it once and stop the reconnect loop.
        if (this.transport === transport) this.releaseTransport();
        this.fail("offer_unsupported", "this engine has no usable RTCPeerConnection");
        socket.close();
        return;
      }
      const offerId = this.deps.createOfferId();
      this.offerId = offerId;
      socket.send(
        JSON.stringify({
          type: "offer",
          offer_id: offerId,
          webrtc_offer_sdp: sdp,
        }),
      );
      // A published offer is the proof this broker works — clear any earlier
      // failure so a recovered client stops accusing itself on the status row.
      this.failures = 0;
      publishRealtimeTransportIssue(null);
    } catch (error) {
      console.info("Realtime transport broker could not create an offer.", error);
      if (this.transport === transport) this.releaseTransport();
      socket.close();
    }
  }

  private handleMessage(socket: WebSocket, event: MessageEvent): void {
    if (typeof event.data !== "string") return;
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(event.data) as Record<string, unknown>;
    } catch {
      // A malformed control frame cannot identify an offer and is safe to ignore.
      return;
    }
    if (message.offer_id !== this.offerId) return;
    if (message.type === "release") {
      void this.publishOffer(socket);
      return;
    }
    const answer = message.webrtc_answer_sdp;
    if (message.type !== "answer" || typeof answer !== "string" || !answer.trim()) {
      return;
    }
    const transport = this.transport;
    if (!transport) return;
    void transport.applyAnswer(answer).catch((error) => {
      console.warn("Realtime transport broker rejected the provider answer.", error);
      socket.close();
    });
  }

  private releaseTransport(): void {
    this.offerId = null;
    const transport = this.transport;
    this.transport = null;
    transport?.close();
  }

  /** Record one failed attempt: retry with backoff, or give up honestly. */
  private noteFailure(issue: RealtimeTransportIssue, detail: string): void {
    if (this.stopped) return;
    this.failures += 1;
    if (this.failures >= RECONNECT_MAX_ATTEMPTS) {
      this.fail(issue, detail);
      return;
    }
    console.info(
      `Realtime transport broker retry ${this.failures}/${RECONNECT_MAX_ATTEMPTS} (${issue}): ${detail}`,
    );
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer !== null) return;
    // Exponential, capped. A constant 1.5 s loop against a socket that answers
    // 4401/1008 is a spin against a backend that has already said no.
    const delay = Math.min(
      RECONNECT_DELAY_MS * 2 ** Math.max(0, this.failures - 1),
      RECONNECT_DELAY_MAX_MS,
    );
    this.reconnectTimer = this.deps.schedule(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, delay);
  }
}
