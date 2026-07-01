/**
 * WebSocket client with exponential-backoff reconnect (500ms → 10s cap).
 * Ping/Pong every 30s. Token via window.__JARVIS_TOKEN as query param.
 *
 * Aside from the singleton onMessage callback (used by useWebSocket for the
 * Zustand store), additional consumers can attach via subscribe() — needed by
 * the TerminalView to receive raw frames including the terminal.spawned reply.
 */

export type WSHandler = (data: unknown) => void;

export interface WSClientOptions {
  url?: string;
  onMessage?: WSHandler;
  onOpen?: () => void;
  onClose?: (code?: number) => void;
}

declare global {
  interface Window {
    __JARVIS_TOKEN?: string;
  }
}

const MIN_BACKOFF = 500;
const MAX_BACKOFF = 10_000;
const PING_INTERVAL = 30_000;

export class WSClient {
  private ws: WebSocket | null = null;
  private backoff = MIN_BACKOFF;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private lastCloseCode: number | undefined;
  private readonly url: string;
  private readonly onMessage?: WSHandler;
  private readonly onOpen?: () => void;
  private readonly onClose?: (code?: number) => void;
  private readonly extraSubscribers = new Set<WSHandler>();

  constructor(opts: WSClientOptions = {}) {
    this.url = opts.url ?? this.defaultUrl();
    this.onMessage = opts.onMessage;
    this.onOpen = opts.onOpen;
    this.onClose = opts.onClose;
  }

  private defaultUrl(): string {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const host = window.location.host;
    const token = window.__JARVIS_TOKEN;
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    return `${proto}://${host}/ws${query}`;
  }

  connect(): void {
    this.stopped = false;
    this.openSocket();
  }

  private openSocket(): void {
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.addEventListener("open", () => {
      this.backoff = MIN_BACKOFF;
      this.startPing();
      this.onOpen?.();
    });

    this.ws.addEventListener("message", (ev) => {
      if (ev.data === "pong") return;
      try {
        const parsed = JSON.parse(ev.data);
        this.onMessage?.(parsed);
        for (const sub of this.extraSubscribers) {
          try {
            sub(parsed);
          } catch {
            // subscriber errors must not kill the client
          }
        }
      } catch {
        // ignore non-JSON frames
      }
    });

    this.ws.addEventListener("close", (ev) => {
      this.stopPing();
      this.lastCloseCode = (ev as CloseEvent).code;
      this.onClose?.(this.lastCloseCode);
      if (!this.stopped) this.scheduleReconnect();
    });

    this.ws.addEventListener("error", () => {
      this.ws?.close();
    });
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        // Backend uses receive_json + WSCommand discriminator — a bare
        // "ping" string would trigger a JSONDecodeError and tip over the session.
        this.ws.send(JSON.stringify({ type: "command", action: "ping", payload: {} }));
      }
    }, PING_INTERVAL);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    // The fast-boot bootstrap closes a warming WS with code 1013 ("try again
    // later"): the backend is still booting, this is NOT a failure. Retry at a
    // fixed short interval instead of escalating the backoff, so the window
    // reconnects within ~1s of the real app becoming ready.
    const warming = this.lastCloseCode === 1013;
    const delay = warming ? MIN_BACKOFF : this.backoff;
    if (!warming) this.backoff = Math.min(MAX_BACKOFF, this.backoff * 2);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay);
  }

  send(payload: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
    }
  }

  /** Add a raw-frame subscriber. Returns an unsubscribe-fn. */
  subscribe(fn: WSHandler): () => void {
    this.extraSubscribers.add(fn);
    return () => this.extraSubscribers.delete(fn);
  }

  close(): void {
    this.stopped = true;
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }
}
