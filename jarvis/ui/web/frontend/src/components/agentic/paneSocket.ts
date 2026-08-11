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
 *  - **A backend that is up but not ready.** After a restart the panes of a
 *    restored workspace reconnect within milliseconds — reliably before the
 *    workspace itself is back. That answer (4503) is a "not yet" and is waited
 *    out; only "not here" (4404) ends a pane for good.
 *
 * All three are bounded: a pane never hammers a backend that is genuinely
 * gone — it falls back to knocking every half minute — and whatever it ends up
 * believing, it says out loud.
 *
 * **Every one of those waits is measured by a timer, and a timer is the one
 * thing a hidden window does not honour.** Chromium clamps `setTimeout` in a
 * document that is not visible to roughly once per second, and to once per
 * minute after a few minutes of that — so a 500 ms retry becomes a second, and
 * the half-minute knock becomes a minute or more. Measured live 2026-07-27
 * 20:11: five panes reconnecting into a workspace that was still opening
 * knocked at 10.174, 11.174, 12.162, 13.170 — a flat one-second grid where the
 * backoff asked for 0.5 s, 1 s, 2 s, 4 s. The workspace opened at 20:11:24 and
 * the panes, by then deep in the slow schedule, surfaced their agents around
 * two minutes later with the work long since running. The agents were fine and
 * the prompts had gone out on time; only the screen was late.
 *
 * So the reconnect does not rely on the timer alone: coming back into view
 * retries immediately (see `onDocumentVisible`). The user looking at the pane
 * is the most reliable signal there is that the wait should end now.
 */

import { mintWsTicket } from "@/lib/ws";

/** Server close code: the handshake credential was missing or invalid. */
const CLOSE_UNAUTHORIZED = 4401;
/** Server close code: no such pane in the addressed workspace. */
const CLOSE_NO_SUCH_PANE = 4404;
/**
 * Server close code: the workspace this pane belongs to is not open YET.
 *
 * Its own code because it is the opposite verdict from 4404 with the same
 * shape: the backend is up but still restoring, which is precisely the state
 * every pane of a full grid reconnects into after the app restarts. Until the
 * server distinguished them, that answer arrived as "no such pane" and a whole
 * workspace of terminals gave up for good (BUG-113).
 */
const CLOSE_NOT_READY = 4503;
/**
 * Server close code: this pane's workspace is closed, and another one is open.
 *
 * The third verdict, and the one that used to be missing. "Not here" and "not
 * yet" both assume the client's picture of the world is right; this one says it
 * is out of date. Retrying cannot fix that and neither can waiting — the answer
 * is to go and re-read which workspace is open, which is what
 * {@link WORKSPACE_CHANGED_EVENT} asks the view to do.
 */
const CLOSE_STALE_WORKSPACE = 4409;
/**
 * Server close code: the pane is here, but its agent refused to start.
 *
 * The fourth verdict, and the one 4404 used to be borrowed for. The distinction
 * matters because the two ask for opposite things: "not here" means the grid on
 * screen is stale and must be re-read, while this one means the grid is right
 * and the AGENT has a problem the user can fix — almost always a launch profile
 * with no key configured. Sharing a code meant the pane discarded the server's
 * explanation, replaced it with "no longer part of the open workspace", and sent
 * the view off to re-read a grid that was never wrong.
 */
const CLOSE_ATTACH_FAILED = 4500;

/**
 * The window event the Agentic-IDE view re-fetches its whole state on.
 *
 * Dispatched from here as well as from the app's WebSocket, because a pane is
 * often the FIRST place a divergence shows up: the view still believes in a
 * grid the backend no longer has, and the only component that finds out is the
 * socket being turned away.
 */
const WORKSPACE_CHANGED_EVENT = "jarvis:agentic-ide-changed";

/** Ask the view to re-read the workspace state. Safe to call more than once. */
function askForFreshState(reason: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(WORKSPACE_CHANGED_EVENT, { detail: { reason } }),
  );
}

const MIN_BACKOFF = 500;
const MAX_BACKOFF = 8_000;

/**
 * Consecutive failed attempts before a pane stops retrying quickly.
 * Eight covers the ~20 s a backend restart takes to answer again.
 */
const MAX_ATTEMPTS = 8;

/**
 * How often a pane that has run out of fast attempts still knocks.
 *
 * It does keep knocking, and that is the point: the reasons a terminal cannot
 * be reached are overwhelmingly temporary — a backend restarting, a workspace
 * not restored yet, a laptop that was asleep — and every one of them used to
 * end with a pane that was dead for the rest of the session while the agent
 * behind it was alive and working. Half a minute apart costs nothing and is the
 * difference between a workspace that heals itself and one the user has to
 * rebuild by hand.
 */
const IDLE_BACKOFF = 30_000;

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
  /** Whether this viewer is foregrounded and may own the shared PTY size. */
  claimOwner?: boolean;
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

/**
 * A pane was handed a prompt: what was sent, when, and whether it started.
 *
 * Carried on its own frame rather than left to be recognised in the agent's
 * output, because the output is what goes missing in every case where this
 * matters — see the server's `on_prompt` for the list. `text` is an excerpt;
 * the unabridged prompt is one request away (`getLastPrompt`), which keeps a
 * multi-thousand-character brief off a channel that is otherwise keystrokes.
 */
export interface PromptDelivery {
  /** Epoch SECONDS, as the server stamps it. */
  at: number | null;
  /** Full length of the delivered prompt, not of `text`. */
  chars: number;
  /** The opening of the prompt — enough for a receipt line. */
  text: string;
  /** True = the agent started; false = still in its input box; null = unknown. */
  submitted: boolean | null;
  prompts_sent: number;
}

export interface PaneSocketHandlers {
  /** The socket is up; the pane may send its measured size. */
  onOpen?: () => void;
  onOutput: (text: string) => void;
  /**
   * The screen this pane is re-joining, as the raw bytes that drew it.
   *
   * Separate from `onOutput` because it is a different instruction, not a
   * bigger one: live output CONTINUES a screen, a replay REBUILDS it, and a
   * handler that appends it draws the agent's interface a second time on top
   * of the copy already there. That does not stack tidily — an Ink TUI skips
   * unchanged cells with cursor moves instead of overwriting them with spaces,
   * so the old copy shows through the new one character by character ("plus
   * everything new" came back as "plueverythingwnew", reported 2026-07-29).
   * Nothing repairs it either: the agent redraws its own visible rows and
   * never the scrollback above them, so the mess survives every later repaint.
   *
   * The implementation therefore has exactly one obligation — reset the
   * terminal before writing this — and it is required rather than optional so
   * that a new pane cannot quietly forget it.
   */
  onReplay: (text: string) => void;
  /**
   * The agent is attached — freshly started, resumed, or re-joined.
   *
   * `lastPrompt` is what this pane was told to do BEFORE this socket existed,
   * handed over with the handshake. It is what lets a reload, a reconnect or a
   * second window show the receipt for a prompt they were not connected for —
   * which is the viewer that most needs it, since the one who watched it
   * arrive never doubted it.
   */
  onReady: (info: {
    resumed: boolean;
    reattached: boolean;
    lastPrompt: PromptDelivery | null;
  }) => void;
  /** This pane was handed a prompt just now. */
  onPrompt?: (delivery: PromptDelivery) => void;
  /**
   * The agent is in a DIFFERENT size than this pane asked for.
   *
   * A resize is a request. The server turns one down when the tile is under the
   * viewer floor or when this socket no longer holds the pane, and it lifts a
   * pane that is stuck below the floor — so what a pane asked for and what its
   * agent got are not always the same number. Only the disagreements arrive
   * here; being granted what you asked for stays silent.
   *
   * Acting on it is not optional. The pane reflowed its own xterm the moment it
   * measured its tile, and a grid that disagrees with the agent's is worse than
   * a grid that is too small: a TUI moves its cursor RELATIVELY, so its next
   * repaint lands on rows holding other text and the two screens interleave
   * character by character.
   */
  onGeometry?: (size: { cols: number; rows: number }) => void;
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
  /**
   * Hand a frame to the server. Returns whether it actually went out — a
   * socket that is connecting, retrying or gone accepts nothing, and a caller
   * that must not lose what it was saying (a pane's measured size) needs to
   * know that rather than assume it arrived.
   */
  send(payload: unknown): boolean;
  close(): void;
}

function paneUrl(opts: PaneSocketOptions, ticket: string | null): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const query = new URLSearchParams({
    cols: String(opts.cols),
    rows: String(opts.rows),
    claim: opts.claimOwner === false ? "0" : "1",
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
  /** Consecutive "not yet" answers — waiting, which is not failing. */
  let waits = 0;
  let ticketRetries = 0;
  let pendingTicket: string | null = null;
  /**
   * The last reason the SERVER gave, kept for the close that follows it.
   *
   * An `error` frame is never the end of the story — the server sends it and
   * closes in the same breath — so the close handler is where a pane decides
   * what it believes, and until it could read this it had only the close code
   * to go on. That is how a pane whose agent refused to start ("… is not
   * configured yet — add its API key …") ended up telling the user it was no
   * longer part of the workspace: a true sentence about the wrong problem.
   */
  let serverReason = "";

  const schedule = (delay: number) => {
    if (timer !== null || stopped) return;
    timer = setTimeout(() => {
      timer = null;
      if (!stopped) open();
    }, delay);
  };

  /**
   * The window came back — stop waiting and try now.
   *
   * A hidden document's timers are clamped, so whatever delay was scheduled has
   * been stretched by an unknown amount and may still be minutes out. Worse,
   * the streak counters kept growing while the clamp made every attempt look
   * slow, so a pane can arrive here believing the backend is gone when it was
   * merely unwatched. Being looked at is fresh evidence: the counters go back to
   * zero and the pending attempt happens immediately.
   *
   * Only ever pulls a retry FORWARD. A pane with no attempt scheduled is either
   * connected or finished, and neither wants a socket opened underneath it.
   */
  const onDocumentVisible = () => {
    if (stopped || typeof document === "undefined" || document.hidden) return;
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
    attempts = 0;
    waits = 0;
    open();
  };
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onDocumentVisible);
  }

  const giveUp = (message: string) => {
    stopped = true;
    handlers.onTrouble(message, false);
  };

  const retryLater = (message: string) => {
    attempts += 1;
    if (attempts > MAX_ATTEMPTS) {
      // Out of fast attempts, not out of hope: the pane says so (``retrying``
      // false paints it as unreachable and offers the restart button) and then
      // keeps knocking every half minute, so a backend that comes back finds
      // its terminals waiting rather than needing every one of them reopened.
      handlers.onTrouble(message, false);
      schedule(IDLE_BACKOFF);
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
    if (code === CLOSE_STALE_WORKSPACE) {
      // Not this pane's fault and not worth a red badge: the view is simply
      // showing a workspace that has been closed. Stop, and ask for the real
      // one — the grid that comes back brings its own panes with it.
      stopped = true;
      handlers.onTrouble("This workspace was closed — reloading…", true);
      askForFreshState("stale-workspace");
      return;
    }
    if (code === CLOSE_ATTACH_FAILED) {
      // The pane is where it belongs and the grid is current — the agent would
      // not start. Retrying cannot fix a missing key, so this ends the pane,
      // and it ends it with the server's OWN sentence: that sentence names the
      // problem and the page to fix it on, which no message written here could.
      giveUp(serverReason || "This terminal could not be started.");
      return;
    }
    if (code === CLOSE_NO_SUCH_PANE) {
      // The server is not saying "not right now", it is saying "not here".
      // No number of retries turns that into a terminal.
      giveUp("This terminal is no longer part of the open workspace.");
      // A pane that the open workspace does not have means the grid on screen
      // is out of date — one pane is merely where that became visible. Left
      // alone it stays visible as a dead rectangle for the rest of the session
      // (measured 2026-07-28: two panes from a six-pane workspace kept knocking
      // at a four-pane one for minutes). Re-reading the state is what removes
      // it, and it costs one request.
      askForFreshState("no-such-pane");
      return;
    }
    if (code === CLOSE_NOT_READY) {
      // The workspace is still coming up. Waiting is the correct answer, and it
      // is counted separately from failures: the backend is answering, doing
      // exactly what it said it was doing, so this must not burn down the
      // budget that decides whether the pane is UNREACHABLE. Fast while a
      // restart plays out, patient afterwards — a workspace restored a quarter
      // of an hour later still finds its panes waiting.
      waits += 1;
      handlers.onTrouble("Waiting for the workspace to come back…", true);
      // Before settling into the slow knock, ask once whether the world still
      // looks the way this pane thinks it does. A workspace that opened while
      // the panes were waiting announces itself, but a pane that has already
      // dropped to one attempt every half minute would take that long to
      // notice — and an announcement missed for any reason (a socket that was
      // reconnecting, a window that was hidden) would never be noticed at all.
      if (waits === MAX_ATTEMPTS + 1) askForFreshState("waited-out");
      schedule(
        waits > MAX_ATTEMPTS
          ? IDLE_BACKOFF
          : Math.min(MAX_BACKOFF, MIN_BACKOFF * 2 ** (waits - 1)),
      );
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
      let msg: {
        t?: string;
        d?: string;
        code?: number;
        message?: string;
        resumed?: boolean;
        reattached?: boolean;
        at?: number | null;
        chars?: number;
        preview?: string;
        submitted?: boolean | null;
        prompts_sent?: number;
        last_prompt_at?: number | null;
        last_prompt_chars?: number;
        last_prompt_preview?: string;
        cols?: number;
        rows?: number;
      };
      try {
        msg = JSON.parse((ev as MessageEvent).data as string);
      } catch {
        return;
      }
      if (msg.t === "o") {
        handlers.onOutput(msg.d ?? "");
      } else if (msg.t === "replay") {
        handlers.onReplay(msg.d ?? "");
      } else if (msg.t === "ready") {
        // A live handshake also clears the auth streak: whatever credential
        // this attempt used, it worked.
        ticketRetries = 0;
        attempts = 0;
        waits = 0;
        handlers.onReady({
          resumed: Boolean(msg.resumed),
          reattached: Boolean(msg.reattached),
          // Only when this pane has actually been sent something. A null here
          // means "nothing was ever delivered", which the receipt must not
          // render as a delivery with a missing timestamp.
          lastPrompt:
            typeof msg.last_prompt_at === "number"
              ? {
                  at: msg.last_prompt_at,
                  chars: msg.last_prompt_chars ?? 0,
                  text: msg.last_prompt_preview ?? "",
                  submitted: msg.submitted ?? null,
                  prompts_sent: 0,
                }
              : null,
        });
      } else if (msg.t === "prompt") {
        handlers.onPrompt?.({
          at: typeof msg.at === "number" ? msg.at : null,
          chars: msg.chars ?? 0,
          text: msg.preview ?? "",
          submitted: msg.submitted ?? null,
          prompts_sent: msg.prompts_sent ?? 0,
        });
      } else if (msg.t === "size") {
        // The size this pane's agent is really in, sent only when it differs
        // from what this pane asked for. Guarded rather than trusted: a grid of
        // zero columns wrecks a TUI's drawing permanently, so a malformed frame
        // must not be able to do what the floors elsewhere exist to prevent.
        if (
          typeof msg.cols === "number" &&
          typeof msg.rows === "number" &&
          Number.isFinite(msg.cols) &&
          Number.isFinite(msg.rows) &&
          msg.cols > 0 &&
          msg.rows > 0
        ) {
          handlers.onGeometry?.({ cols: msg.cols, rows: msg.rows });
        }
      } else if (msg.t === "exit") {
        agentExited = true;
        handlers.onExit(msg.code ?? 0);
      } else if (msg.t === "error") {
        // The server refused the attach and is about to close; the close code
        // decides whether that is worth another attempt. Kept as well as shown,
        // because the close handler runs a moment later and would otherwise
        // have nothing to say beyond what the bare code implies.
        serverReason = msg.message ?? "";
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
    send(payload: unknown): boolean {
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      try {
        ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
        return true;
      } catch {
        // The socket died between the check and the write. Reported as not
        // sent, which is exactly what happened.
        return false;
      }
    },
    close(): void {
      stopped = true;
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onDocumentVisible);
      }
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
