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

/**
 * How long an OPEN socket may stay completely silent before it is declared
 * dead and replaced.
 *
 * A ping that nothing checks is decoration. The failure it has to catch is the
 * half-open socket: the peer is gone (the machine slept, the network changed,
 * the backend was killed rather than closed) but no FIN ever arrived, so
 * `readyState` stays OPEN, no `close` event fires, and the reconnect below is
 * never scheduled. Every live update in the app rides this one socket, so the
 * whole UI then freezes while claiming to be connected — and only a reload
 * brings it back. That is precisely the "nothing happens until I reload"
 * report.
 *
 * The backend answers every ping with a pong, so an idle-but-healthy socket
 * still produces a frame every {@link PING_INTERVAL}. Two and a half intervals
 * of total silence is therefore two missed answers, not a slow moment — and
 * generous enough that a hidden window's clamped timer (Chromium stretches
 * `setInterval` to about once a minute in a document nobody is looking at)
 * cannot trip it on its own.
 */
const SILENCE_LIMIT = PING_INTERVAL * 2.5;

export class WSClient {
  private ws: WebSocket | null = null;
  private backoff = MIN_BACKOFF;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private lastCloseCode: number | undefined;
  private pendingTicket: string | null = null;
  private authRetryStreak = 0;
  /** When the last frame of any kind arrived — the liveness evidence. */
  private lastFrameAt = 0;
  private wakeListening = false;
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
    this.listenForWake();
    this.openSocket();
  }

  /**
   * Two events that are better evidence than any timer: the window came back,
   * and the machine is on a network again.
   *
   * Both mean the socket may have died unnoticed while nobody could tell — a
   * laptop that slept, a Wi-Fi that changed, a window that sat behind an editor
   * for an hour with its timers clamped to a crawl. Checking here is what turns
   * "frozen until the user reloads" into "live again the moment they look".
   */
  private listenForWake(): void {
    if (this.wakeListening || typeof window === "undefined") return;
    this.wakeListening = true;
    window.addEventListener("online", this.wake);
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", this.wake);
    }
  }

  private stopListeningForWake(): void {
    if (!this.wakeListening || typeof window === "undefined") return;
    this.wakeListening = false;
    window.removeEventListener("online", this.wake);
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.wake);
    }
  }

  /**
   * Reconnect NOW rather than at the end of whatever wait is running.
   *
   * An arrow property so it can be added and removed as a listener by
   * identity. Deliberately does nothing while the document is hidden: this is
   * about the moment attention returns, and a background tab has no reason to
   * hurry.
   */
  private readonly wake = (): void => {
    if (this.stopped) return;
    if (typeof document !== "undefined" && document.hidden) return;
    if (this.ws?.readyState === WebSocket.OPEN) {
      // Open, but is it alive? A socket that has been silent past the limit is
      // the half-open case — replace it instead of trusting readyState.
      if (this.lastFrameAt > 0 && Date.now() - this.lastFrameAt > SILENCE_LIMIT) {
        this.dropDeadSocket();
      }
      return;
    }
    if (this.ws?.readyState === WebSocket.CONNECTING) return;
    // A wait that was scheduled while the window was hidden has been stretched
    // by an unknown amount and may still be minutes out. Being looked at is
    // fresh evidence: start over at the fast end of the backoff, now.
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.backoff = MIN_BACKOFF;
    this.openSocket();
  };

  /**
   * Give up on a socket that is open but no longer carrying anything.
   *
   * Closing it explicitly is what produces the `close` event the reconnect
   * hangs off — a half-open socket never fires one by itself, which is the
   * whole reason the UI could sit frozen for hours.
   */
  private dropDeadSocket(): void {
    const socket = this.ws;
    this.ws = null;
    this.stopPing();
    this.lastFrameAt = 0;
    try {
      socket?.close();
    } catch {
      /* already gone */
    }
    this.onClose?.(undefined);
    this.backoff = MIN_BACKOFF;
    this.scheduleReconnect();
  }

  private openSocket(): void {
    // A pending ticket is single-use: consume it for exactly this attempt.
    const ticket = this.pendingTicket;
    this.pendingTicket = null;
    const target = ticket
      ? `${this.url}${this.url.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`
      : this.url;
    let socket: WebSocket;
    try {
      socket = new WebSocket(target);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;
    /*
     * Every handler below belongs to THIS attempt.
     *
     * A socket the client has already given up on (see `dropDeadSocket`) still
     * fires its own close, and letting that run would report a second
     * disconnect and schedule a second reconnect for a connection nobody is
     * waiting on any more. Comparing against the current socket makes a retired
     * one inert without needing a flag per handler.
     */
    const current = (): boolean => this.ws === socket;

    socket.addEventListener("open", () => {
      if (!current()) return;
      this.backoff = MIN_BACKOFF;
      this.lastFrameAt = Date.now();
      this.startPing();
      this.onOpen?.();
    });

    socket.addEventListener("message", (ev) => {
      if (!current()) return;
      // Any real frame proves the handshake credential worked — the auth
      // retry streak only counts UNINTERRUPTED 4401 rejections.
      this.authRetryStreak = 0;
      // ...and proves the wire is still carrying, which is what the silence
      // watchdog in `startPing` measures against. A pong counts: it is the
      // only frame an idle backend produces.
      this.lastFrameAt = Date.now();
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

    socket.addEventListener("close", (ev) => {
      if (!current()) return;
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

    socket.addEventListener("error", () => {
      if (!current()) return;
      socket.close();
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
      if (this.ws?.readyState !== WebSocket.OPEN) return;
      // Answer first, question second: a socket that has not produced a single
      // frame since the last two pings is not slow, it is gone. Replacing it
      // here is the only way back — a half-open socket never closes itself, so
      // nothing else in this class would ever run again.
      if (this.lastFrameAt > 0 && Date.now() - this.lastFrameAt > SILENCE_LIMIT) {
        this.dropDeadSocket();
        return;
      }
      // Backend uses receive_json + WSCommand discriminator — a bare
      // "ping" string would trigger a JSONDecodeError and tip over the session.
      this.ws.send(JSON.stringify({ type: "command", action: "ping", payload: {} }));
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
    this.stopListeningForWake();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }
}
