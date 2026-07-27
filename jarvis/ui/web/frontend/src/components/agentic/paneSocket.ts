/**
 * The socket that carries one Agentic-IDE pane: keystrokes out, the agent's
 * screen back.
 *
 * It exists as its own module because a pane's connection is the one place
 * where "the agent is broken" and "the wire is broken" get confused. The
 * server keeps a pane's agent running across viewers on purpose — a socket
 * that goes away means the VIEWER left, never that the work stopped, and the
 * next socket is handed the replay buffer that redraws the screen. A pane that
 * connected once and then gave up therefore reports a dead terminal while the
 * agent behind it is alive and still working.
 *
 * Two failure modes are handled here rather than in the component:
 *
 *  - **A rejected handshake (4401).** WebKit engines (WKWebView on macOS,
 *    WebKitGTK on Linux) do not attach the HttpOnly session cookie to a
 *    WebSocket handshake — BUG-065 — so a perfectly healthy session is turned
 *    away. Every other socket in the app already answers this by proving the
 *    session over plain HTTP and reconnecting with a one-time ticket; a pane
 *    that skipped it simply could not open a terminal on those platforms.
 *  - **A dropped connection.** A backend restart, a reload, a moment of
 *    unreachability. One failed attempt used to be final, which is why a
 *    whole grid of panes could go red at once and stay that way.
 *
 * Both are bounded: a pane never hammers a backend that is genuinely gone, and
 * whatever it ends up believing, it says out loud.
 */

import { mintWsTicket } from "@/lib/ws";

/** Server close code: the handshake credential was missing or invalid. */
const CLOSE_UNAUTHORIZED = 4401;
/** Server close code: no such pane in the addressed workspace. */
const CLOSE_NO_SUCH_PANE = 4404;

const MIN_BACKOFF = 500;
const MAX_BACKOFF = 8_000;

/**
 * Consecutive failed attempts before a pane declares itself unreachable.
 * Eight covers the ~20 s a backend restart takes to answer again, and stops
 * well short of retrying at a dead server for the rest of the session.
 */
const MAX_ATTEMPTS = 8;

/**
 * Consecutive 4401s that still count as a transient authorization hiccup.
 * Beyond this, fresh tickets are evidently not unlocking the socket and the
 * ordinary backoff takes over instead of minting on a 500 ms loop forever.
 */
const MAX_TICKET_RETRIES = 3;

export interface PaneSocketOptions {
  /** Call-sign of the pane, as the server knows it. */
  name: string;
  cols: number;
  rows: number;
  /**
   * The workspace this pane belongs to. Several can be open and the front one
   * changes while sockets are alive: without it the server resolves the pane
   * against whichever workspace happens to be showing, which is a pane in a
   * different folder.
   */
  workspaceId?: string | null;
  /**
   * The ground this pane draws on — `"light"` or `"dark"`.
   *
   * Sent because the SERVER answers the agent's "what colour is your screen?"
   * query, not this browser: the reply has to reach the CLI within a
   * millisecond or two of it asking, and one produced here would arrive a full
   * round trip late — as visible junk in the agent's prompt. The server can
   * only answer correctly if it knows what the pane looks like.
   */
  appearance?: string | null;
}

export interface PaneSocketHandlers {
  /** The socket is up; the pane may send its measured size. */
  onOpen?: () => void;
  onOutput: (text: string) => void;
  /** The agent is attached — freshly started, resumed, or re-joined. */
  onReady: (info: { resumed: boolean; reattached: boolean }) => void;
  /** The agent itself finished. Final: nothing reconnects after this. */
  onExit: (code: number) => void;
  /**
   * The pane could not be reached. ``retrying`` is true while another attempt
   * is already scheduled — the pane is not dead yet, and painting it red would
   * be wrong.
   */
  onTrouble: (message: string, retrying: boolean) => void;
}

export interface PaneSocket {
  send(payload: unknown): void;
  close(): void;
}

function paneUrl(opts: PaneSocketOptions, ticket: string | null): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({
    cols: String(opts.cols),
    rows: String(opts.rows),
  });
  if (opts.workspaceId) query.set("workspace", opts.workspaceId);
  if (opts.appearance) query.set("appearance", opts.appearance);
  if (ticket) query.set("ticket", ticket);
  return `${proto}://${window.location.host}/api/agentic-ide/pty/${encodeURIComponent(
    opts.name,
  )}?${query.toString()}`;
}

export function openPaneSocket(
  opts: PaneSocketOptions,
  handlers: PaneSocketHandlers,
): PaneSocket {
  let ws: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  /** Set once nothing further should happen on this pane, for any reason. */
  let stopped = false;
  /** The agent reported its own exit — a later close is not a lost wire. */
  let agentExited = false;
  let attempts = 0;
  let ticketRetries = 0;
  let pendingTicket: string | null = null;

  const schedule = (delay: number) => {
    if (timer !== null || stopped) return;
    timer = setTimeout(() => {
      timer = null;
      if (!stopped) open();
    }, delay);
  };

  const giveUp = (message: string) => {
    stopped = true;
    handlers.onTrouble(message, false);
  };

  const retryLater = (message: string) => {
    attempts += 1;
    if (attempts > MAX_ATTEMPTS) {
      giveUp(message);
      return;
    }
    handlers.onTrouble(message, true);
    schedule(Math.min(MAX_BACKOFF, MIN_BACKOFF * 2 ** (attempts - 1)));
  };

  const authRetry = async (): Promise<void> => {
    ticketRetries += 1;
    // The cookie IS sent on plain HTTP, so this proves the session even on the
    // engines that withhold it from the handshake.
    const ticket = await mintWsTicket();
    if (stopped) return;
    if (ticket) pendingTicket = ticket;
    if (ticket !== null && ticketRetries <= MAX_TICKET_RETRIES) {
      handlers.onTrouble("Authorizing the terminal…", true);
      // Fixed short delay: the very next attempt carries a fresh credential,
      // so escalating the backoff would only postpone a working terminal.
      schedule(MIN_BACKOFF);
      return;
    }
    retryLater("Terminal authorization failed — retrying.");
  };

  const handleDrop = (code: number) => {
    if (code === CLOSE_NO_SUCH_PANE) {
      // The server is not saying "not right now", it is saying "not here".
      // No number of retries turns that into a terminal.
      giveUp("This terminal is no longer part of the open workspace.");
      return;
    }
    if (code === CLOSE_UNAUTHORIZED) {
      void authRetry();
      return;
    }
    retryLater(
      attempts === 0 && ws === null
        ? "Could not reach the terminal — retrying."
        : `Terminal connection lost (code ${code || "?"}) — reconnecting.`,
    );
  };

  function open(): void {
    const ticket = pendingTicket;
    pendingTicket = null;
    let socket: WebSocket;
    try {
      socket = new WebSocket(paneUrl(opts, ticket));
    } catch {
      retryLater("Could not reach the terminal — retrying.");
      return;
    }
    ws = socket;
    // One verdict per attempt: a socket that both errors and closes must not
    // be counted twice, or the attempt budget burns down at double speed.
    let settled = false;

    socket.addEventListener("open", () => {
      attempts = 0;
      handlers.onOpen?.();
    });

    socket.addEventListener("message", (ev) => {
      let msg: { t?: string; d?: string; code?: number; message?: string; resumed?: boolean; reattached?: boolean };
      try {
        msg = JSON.parse((ev as MessageEvent).data as string);
      } catch {
        return;
      }
      if (msg.t === "o") {
        handlers.onOutput(msg.d ?? "");
      } else if (msg.t === "ready") {
        // A live handshake also clears the auth streak: whatever credential
        // this attempt used, it worked.
        ticketRetries = 0;
        attempts = 0;
        handlers.onReady({
          resumed: Boolean(msg.resumed),
          reattached: Boolean(msg.reattached),
        });
      } else if (msg.t === "exit") {
        agentExited = true;
        handlers.onExit(msg.code ?? 0);
      } else if (msg.t === "error") {
        // The server refused the attach and is about to close; the close code
        // decides whether that is worth another attempt.
        handlers.onTrouble(msg.message ?? "The terminal could not be started.", true);
      }
    });

    socket.addEventListener("close", (ev) => {
      if (settled) return;
      settled = true;
      if (stopped || agentExited) return;
      handleDrop((ev as CloseEvent).code);
    });

    socket.addEventListener("error", () => {
      // The spec guarantees a close after an error; closing explicitly keeps
      // the two paths from diverging on engines that are less strict.
      try {
        socket.close();
      } catch {
        /* already gone */
      }
    });
  }

  open();

  return {
    send(payload: unknown): void {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
      }
    },
    close(): void {
      stopped = true;
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      try {
        ws?.close();
      } catch {
        /* already gone */
      }
      ws = null;
    },
  };
}
