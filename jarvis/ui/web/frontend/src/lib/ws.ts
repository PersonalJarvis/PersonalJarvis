/**
 * WebSocket client with exponential-backoff reconnect (500ms → 10s cap).
 * Ping/Pong every 30s. Authentication uses the same-origin HttpOnly cookie —
 * with a one-time-ticket fallback for WebKit engines (Safari/WKWebView on
 * macOS, WebKitGTK on Linux), which do not attach HttpOnly cookies to a
 * WebSocket handshake: a 4401 close triggers a ticket mint over plain HTTP
 * (where the cookie IS sent) and a fast reconnect carrying `?ticket=`.
 *
 * Aside from the singleton onMessage callback (used by useWebSocket for the
 * Zustand store), additional consumers can attach via subscribe() — needed by
 * the TerminalView to receive raw frames including the terminal.spawned reply.
 */

export type WSHandler = (data: unknown) => void;

export interface WSCloseInfo {
  /** True while the client is about to retry a 4401 with a fresh ticket. */
  authRetryPending: boolean;
}

export interface WSClientOptions {
  url?: string;
  onMessage?: WSHandler;
  onOpen?: () => void;
  onClose?: (code?: number, info?: WSCloseInfo) => void;
}

/** Server close code: credential missing/invalid (readable since BUG-065). */
const WS_CLOSE_UNAUTHORIZED = 4401;

/**
 * Consecutive 4401 closes that still count as a transient auth retry. Beyond
 * this the ticket flow is evidently not unlocking the socket (server-side
 * regression, revoked session mid-mint, ...) — report the honest offline
 * state and fall back to the escalating backoff instead of hammering the
 * mint endpoint every 500 ms forever.
 */
const MAX_FAST_AUTH_RETRIES = 3;

/**
 * Mint a one-time WebSocket ticket over cookie-authenticated HTTP.
 * Returns null when the session itself is dead (401) or the fetch fails.
 */
export async function mintWsTicket(): Promise<string | null> {
  try {
    const response = await fetch("/api/ui/ws-ticket", {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) return null;
    const data: unknown = await response.json();
    const ticket = (data as { ticket?: unknown }).ticket;
    return typeof ticket === "string" && ticket.length > 0 ? ticket : null;
  } catch {
    return null;
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
  private pendingTicket: string | null = null;
  private authRetryStreak = 0;
  private readonly url: string;
  private readonly onMessage?: WSHandler;
  private readonly onOpen?: () => void;
  private readonly onClose?: (code?: number, info?: WSCloseInfo) => void;
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
    return `${proto}://${host}/ws`;
  }

  connect(): void {
    this.stopped = false;
    this.openSocket();
  }

  private openSocket(): void {
    // A pending ticket is single-use: consume it for exactly this attempt.
    const ticket = this.pendingTicket;
    this.pendingTicket = null;
    const target = ticket
      ? `${this.url}${this.url.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`
      : this.url;
    try {
      this.ws = new WebSocket(target);
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
      // Any real frame proves the handshake credential worked — the auth
      // retry streak only counts UNINTERRUPTED 4401 rejections.
      this.authRetryStreak = 0;
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
      if (this.lastCloseCode === WS_CLOSE_UNAUTHORIZED && !this.stopped) {
        // WebKit engines drop the HttpOnly session cookie from WS handshakes,
        // so the boundary answers 4401 even though the session is healthy.
        // Prove the session over plain HTTP instead and retry with the
        // one-time ticket. onClose is deferred until the mint settles so the
        // UI can distinguish "authenticating, retry imminent" from a dead
        // session (mint failed → honest offline).
        void this.handleAuthReject();
        return;
      }
      this.onClose?.(this.lastCloseCode);
      if (!this.stopped) this.scheduleReconnect();
    });

    this.ws.addEventListener("error", () => {
      this.ws?.close();
    });
  }

  private async handleAuthReject(): Promise<void> {
    this.authRetryStreak += 1;
    const ticket = await mintWsTicket();
    if (this.stopped) return;
    if (ticket) this.pendingTicket = ticket;
    const retrying = ticket !== null && this.authRetryStreak <= MAX_FAST_AUTH_RETRIES;
    this.onClose?.(this.lastCloseCode, { authRetryPending: retrying });
    if (retrying) {
      // Fixed short retry, like the 1013 warming path: the very next attempt
      // carries a fresh credential, so backoff escalation would only delay it.
      if (this.reconnectTimer) return;
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.openSocket();
      }, MIN_BACKOFF);
      return;
    }
    // The session is gone, the backend vanished mid-mint, or fresh tickets
    // keep getting rejected: report the close honestly and fall back to the
    // escalating reconnect (a still-minted ticket rides along regardless —
    // it is the only path back to a live socket if the server recovers).
    this.scheduleReconnect();
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
