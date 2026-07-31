import { RealtimeWebRtcTransport } from "./realtimeAudio";
import { mintWsTicket } from "./ws";

declare global {
  interface Window {
    __JARVIS_REALTIME_BROKER_TOKEN?: string;
  }
}

const RECONNECT_DELAY_MS = 1_500;
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

  constructor(deps: Partial<BrokerDeps> = {}) {
    this.deps = { ...DEFAULT_DEPS, ...deps };
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
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

  private async connect(): Promise<void> {
    const generation = ++this.generation;
    try {
      const desktopCapability = this.deps.readDesktopCapability();
      if (!desktopCapability) return;
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
      socket.onclose = () => {
        if (this.socket !== socket) return;
        this.socket = null;
        this.releaseTransport();
        this.scheduleReconnect();
      };
    } catch (error) {
      console.info("Realtime transport broker could not connect.", error);
      this.scheduleReconnect();
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
        !sdp ||
        this.stopped ||
        this.socket !== socket ||
        generation !== this.generation ||
        socket.readyState !== WS_OPEN
      ) {
        if (!sdp) {
          console.info("This browser cannot broker a WebRTC subscription transport.");
        }
        if (this.transport === transport) this.releaseTransport();
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

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer !== null) return;
    this.reconnectTimer = this.deps.schedule(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, RECONNECT_DELAY_MS);
  }
}
