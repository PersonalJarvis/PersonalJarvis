import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openPaneSocket } from "./paneSocket";

/** Minimal WebSocket stand-in, mirroring the one in __tests__/ws.test.ts. */
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static last: MockWebSocket | null = null;
  static opened: MockWebSocket[] = [];

  readyState = MockWebSocket.OPEN;
  url: string;
  private listeners: Record<string, Array<(ev: unknown) => void>> = {};

  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
    MockWebSocket.opened.push(this);
  }

  addEventListener(type: string, fn: (ev: never) => void) {
    (this.listeners[type] ??= []).push(fn as (ev: unknown) => void);
  }

  send = vi.fn();

  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
  });

  fire(type: string, ev: unknown = {}) {
    (this.listeners[type] ?? []).forEach((fn) => fn(ev));
  }

  /** Server frame, as the pane protocol sends it. */
  deliver(payload: unknown) {
    this.fire("message", { data: JSON.stringify(payload) });
  }

  /** Close from the server side, with a code. */
  dropped(code: number) {
    this.readyState = MockWebSocket.CLOSED;
    this.fire("close", { code });
  }
}

/** Put the document in or out of view, the way a window switch does. */
function hidden(value: boolean) {
  Object.defineProperty(document, "hidden", {
    configurable: true,
    get: () => value,
  });
}

function handlers() {
  return {
    onOpen: vi.fn(),
    onOutput: vi.fn(),
    onReplay: vi.fn(),
    onReady: vi.fn(),
    onExit: vi.fn(),
    onTrouble: vi.fn(),
    onPrompt: vi.fn(),
  };
}

describe("openPaneSocket", () => {
  const originalWs = globalThis.WebSocket;
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
    (globalThis as unknown as { window: unknown }).window = globalThis;
    (window as unknown as { location: unknown }).location = {
      protocol: "http:",
      host: "localhost:5173",
    };
    MockWebSocket.last = null;
    MockWebSocket.opened = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = originalWs;
    (globalThis as unknown as { fetch: unknown }).fetch = originalFetch;
  });

  it("addresses the pane by size and by the workspace holding it", () => {
    const socket = openPaneSocket(
      { name: "Mika", cols: 120, rows: 40, workspaceId: "ide_abc" },
      handlers(),
    );
    // The workspace id is not decoration: the front workspace can change while
    // this socket is alive, and the server resolves a pane without one against
    // whichever is showing — which is a different folder's pane.
    expect(MockWebSocket.last!.url).toBe(
      "ws://localhost:5173/api/agentic-ide/pty/Mika?cols=120&rows=40&claim=1&workspace=ide_abc",
    );
    socket.close();
  });

  it("marks a background viewer so it cannot steal the shared PTY size", () => {
    const socket = openPaneSocket(
      { name: "Mika", cols: 60, rows: 20, claimOwner: false },
      handlers(),
    );

    expect(MockWebSocket.last!.url).toContain("claim=0");
    socket.close();
  });

  it("mints a one-time ticket and retries a rejected handshake (BUG-065)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ticket: "one-time-abc", expires_in: 60 }),
    });
    (globalThis as unknown as { fetch: unknown }).fetch = fetchMock;
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    const first = MockWebSocket.last;

    first!.dropped(4401);
    await vi.advanceTimersByTimeAsync(500);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ui/ws-ticket",
      expect.objectContaining({ method: "POST", credentials: "same-origin" }),
    );
    expect(MockWebSocket.last).not.toBe(first);
    expect(MockWebSocket.last!.url).toContain("ticket=one-time-abc");
    // A retry is under way, so the pane is not dead — saying so would put a
    // red "error" on a terminal that is about to work.
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), true);
    socket.close();
  });

  it("keeps a re-joined screen off the live-output channel", () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");

    // The same bytes on the two channels mean opposite things: live output
    // continues the screen, a replay rebuilds it — and only the second one may
    // clear the terminal first. Routed to `onOutput` instead, the re-joined
    // interface is drawn on top of the copy already there and the two
    // interleave into unreadable text (2026-07-29).
    MockWebSocket.last!.deliver({ t: "replay", d: "\x1b[?1049h# the screen" });

    expect(cb.onReplay).toHaveBeenCalledWith("\x1b[?1049h# the screen");
    expect(cb.onOutput).not.toHaveBeenCalled();

    MockWebSocket.last!.deliver({ t: "o", d: "live" });
    expect(cb.onOutput).toHaveBeenCalledWith("live");
    expect(cb.onReplay).toHaveBeenCalledTimes(1);
    socket.close();
  });

  it("gives up when the pane is no longer part of the open workspace", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Ghost", cols: 80, rows: 24 }, cb);
    const first = MockWebSocket.last;

    // 4404 is the server saying the pane does not exist here. Retrying cannot
    // change that answer, so the pane reports honestly and stops.
    first!.dropped(4404);
    await vi.advanceTimersByTimeAsync(10_000);

    expect(MockWebSocket.opened).toHaveLength(1);
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), false);
    socket.close();
  });

  it("keeps the server's reason when the agent refused to start", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Tara", cols: 80, rows: 24 }, cb);
    const first = MockWebSocket.last;
    first!.fire("open");

    // The pane IS in the workspace; its agent would not start. That answer
    // carries the only sentence naming the fix, and it used to be discarded:
    // sent under 4404, the pane replaced it with "no longer part of the open
    // workspace" and sent the view off to re-read a grid that was never wrong.
    const reason =
      "GLM Coding Plan is not configured yet — add its API key on the API Keys page.";
    const reread = vi.fn();
    window.addEventListener("jarvis:agentic-ide-changed", reread);
    first!.deliver({ t: "error", message: reason });
    first!.dropped(4500);
    await vi.advanceTimersByTimeAsync(60_000);
    window.removeEventListener("jarvis:agentic-ide-changed", reread);

    expect(MockWebSocket.opened).toHaveLength(1);
    expect(cb.onTrouble).toHaveBeenLastCalledWith(reason, false);
    expect(reread).not.toHaveBeenCalled();
    socket.close();
  });

  it("re-joins a running agent after the connection drops", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: false, reattached: false });
    expect(cb.onReady).toHaveBeenCalledTimes(1);

    // The agent outlives its viewer by design — the server keeps the PTY and
    // replays the screen to the next one. A dropped socket therefore means
    // "reconnect", not "the agent died".
    MockWebSocket.last!.dropped(1006);
    await vi.advanceTimersByTimeAsync(500);

    expect(MockWebSocket.opened).toHaveLength(2);
    expect(cb.onExit).not.toHaveBeenCalled();
    socket.close();
  });

  it("slows down once the attempt budget is spent, and says so", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);

    for (let i = 0; i < 12; i += 1) {
      MockWebSocket.last!.dropped(1006);
      await vi.advanceTimersByTimeAsync(20_000);
    }

    // Bounded: a backend that is genuinely gone must not be hammered, and the
    // pane must end up saying so rather than spinning silently.
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), false);
    const attempts = MockWebSocket.opened.length;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(MockWebSocket.opened).toHaveLength(attempts);
    socket.close();
  });

  it("keeps knocking every half minute instead of dying for the session", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);

    for (let i = 0; i < 12; i += 1) {
      MockWebSocket.last!.dropped(1006);
      await vi.advanceTimersByTimeAsync(20_000);
    }
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), false);
    MockWebSocket.last!.dropped(1006);
    const spent = MockWebSocket.opened.length;

    // The reasons a pane cannot connect are nearly always temporary — an app
    // restarting, a machine waking up. A pane that gave up for good left a live
    // agent behind an unusable terminal until the whole workspace was rebuilt
    // by hand (BUG-113), so it stays quiet but never stops trying.
    await vi.advanceTimersByTimeAsync(5_000);
    expect(MockWebSocket.opened).toHaveLength(spent);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(MockWebSocket.opened.length).toBeGreaterThan(spent);

    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: false, reattached: true });
    expect(cb.onReady).toHaveBeenCalledTimes(1);
    socket.close();
  });

  it("waits out a backend that is up but has not restored the workspace yet", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);

    // 4503 is "not yet", the state every pane of a restored workspace connects
    // into for a second or two after the app restarts. Read as "no such pane"
    // it ended a whole grid at once; here it must cost nothing but patience.
    for (let i = 0; i < 12; i += 1) {
      MockWebSocket.last!.dropped(4503);
      await vi.advanceTimersByTimeAsync(30_000);
    }
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), true);

    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: true, reattached: false });
    expect(cb.onReady).toHaveBeenCalledTimes(1);
    socket.close();
  });

  it("retries at once when the window comes back, without waiting out the clamp", async () => {
    /*
     * The live 2026-07-27 20:11 failure. A hidden document has its timers
     * clamped to about once a second, and to once a minute after a few minutes
     * of that — so five panes whose backoff asked for 0.5/1/2/4 s knocked on a
     * flat one-second grid, ran their streak down while the workspace was
     * opening, and surfaced their agents around two minutes later. The prompts
     * had gone out on time; only the screen was late.
     *
     * Being looked at ends the wait. No timer is advanced in this test on
     * purpose: that is the whole point — the retry must not depend on one.
     */
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);

    for (let i = 0; i < 10; i += 1) {
      MockWebSocket.last!.dropped(4503);
      await vi.advanceTimersByTimeAsync(30_000);
    }
    const spent = MockWebSocket.opened.length;

    hidden(true);
    MockWebSocket.last!.dropped(4503);
    hidden(false);
    document.dispatchEvent(new Event("visibilitychange"));

    expect(MockWebSocket.opened.length).toBe(spent + 1);

    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: true, reattached: false });
    expect(cb.onReady).toHaveBeenCalledTimes(1);
    socket.close();
  });

  it("does not open a second socket for a pane that is already connected", async () => {
    /* Coming back into view may only ever pull a PENDING retry forward. */
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: false, reattached: false });
    const spent = MockWebSocket.opened.length;

    hidden(false);
    document.dispatchEvent(new Event("visibilitychange"));

    expect(MockWebSocket.opened.length).toBe(spent);
    socket.close();
  });

  it("stops listening for visibility once the pane is closed", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.dropped(4503);
    socket.close();
    const spent = MockWebSocket.opened.length;

    document.dispatchEvent(new Event("visibilitychange"));

    expect(MockWebSocket.opened.length).toBe(spent);
  });

  it("treats an agent exit as final, not as a dropped connection", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: true, reattached: false });
    MockWebSocket.last!.deliver({ t: "exit", code: 0 });

    MockWebSocket.last!.dropped(1000);
    await vi.advanceTimersByTimeAsync(20_000);

    expect(cb.onExit).toHaveBeenCalledWith(0);
    expect(MockWebSocket.opened).toHaveLength(1);
    socket.close();
  });

  it("does not reconnect after the pane closes its socket", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    socket.close();

    MockWebSocket.last!.dropped(1006);
    await vi.advanceTimersByTimeAsync(20_000);

    expect(MockWebSocket.opened).toHaveLength(1);
    expect(cb.onTrouble).not.toHaveBeenCalled();
  });

  it("passes output through and only sends while the socket is open", () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "o", d: "hello" });
    expect(cb.onOutput).toHaveBeenCalledWith("hello");

    socket.send({ t: "i", d: "ls\r" });
    expect(MockWebSocket.last!.send).toHaveBeenCalledWith(
      JSON.stringify({ t: "i", d: "ls\r" }),
    );

    MockWebSocket.last!.readyState = MockWebSocket.CLOSED;
    socket.send({ t: "i", d: "ignored" });
    expect(MockWebSocket.last!.send).toHaveBeenCalledTimes(1);
    socket.close();
  });
});

/*
 * A delivered prompt travels on a frame of its own instead of being left to be
 * recognised in the agent's output. The output is exactly what goes missing in
 * the cases that matter — a parked pane, an unpainted emulator, a socket that
 * was reconnecting — and each time the user was told a brief had been sent and
 * had no way to check.
 */
describe("openPaneSocket delivery receipts", () => {
  const originalWs = globalThis.WebSocket;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
    (globalThis as unknown as { window: unknown }).window = globalThis;
    (window as unknown as { location: unknown }).location = {
      protocol: "http:",
      host: "localhost:5173",
    };
    MockWebSocket.last = null;
    MockWebSocket.opened = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = originalWs;
  });

  it("reports a prompt the moment the server says it landed", () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");

    MockWebSocket.last!.deliver({
      t: "prompt",
      at: 1_700_000_000,
      chars: 2_400,
      preview: "## Task\nReview the ranking pipeline",
      submitted: true,
      prompts_sent: 2,
    });

    expect(cb.onPrompt).toHaveBeenCalledWith({
      at: 1_700_000_000,
      chars: 2_400,
      text: "## Task\nReview the ranking pipeline",
      submitted: true,
      prompts_sent: 2,
    });
    socket.close();
  });

  it("hands over the prompt that arrived BEFORE this socket existed", () => {
    // The viewer with real reason to doubt a delivery is the one that was not
    // connected when it happened — a reload, a reconnect, a second window.
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");

    MockWebSocket.last!.deliver({
      t: "ready",
      resumed: false,
      reattached: true,
      last_prompt_at: 1_700_000_000,
      last_prompt_chars: 900,
      last_prompt_preview: "Refactor the parser",
      submitted: false,
    });

    expect(cb.onReady).toHaveBeenCalledWith({
      resumed: false,
      reattached: true,
      lastPrompt: {
        at: 1_700_000_000,
        chars: 900,
        text: "Refactor the parser",
        submitted: false,
        prompts_sent: 0,
      },
    });
    socket.close();
  });

  it("reports no prior prompt as null rather than as a delivery at time zero", () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");

    MockWebSocket.last!.deliver({ t: "ready", resumed: false, reattached: false });

    expect(cb.onReady).toHaveBeenCalledWith({
      resumed: false,
      reattached: false,
      lastPrompt: null,
    });
    socket.close();
  });

  it("survives a client that registered no prompt handler", () => {
    // `onPrompt` is optional so an older embedder keeps working; an unhandled
    // frame must not take the pane's whole message loop down with it.
    const cb = handlers();
    const socket = openPaneSocket(
      { name: "Mika", cols: 80, rows: 24 },
      { ...cb, onPrompt: undefined },
    );
    MockWebSocket.last!.fire("open");

    MockWebSocket.last!.deliver({ t: "prompt", at: 1, chars: 3, preview: "hi" });
    MockWebSocket.last!.deliver({ t: "o", d: "still alive" });

    expect(cb.onOutput).toHaveBeenCalledWith("still alive");
    socket.close();
  });
  it("asks the view to re-read when its workspace has been closed", () => {
    // The leftover-window case (2026-07-28). Retrying cannot help and waiting
    // is waiting for nothing: what the pane is holding no longer exists, so the
    // only useful move is to make the view fetch the grid that does.
    const asked = vi.fn();
    window.addEventListener("jarvis:agentic-ide-changed", asked);
    const cb = handlers();
    const socket = openPaneSocket(
      { name: "T1", cols: 80, rows: 24, workspaceId: "ide_gone" },
      cb,
    );

    MockWebSocket.last!.dropped(4409);
    vi.advanceTimersByTime(60_000);

    expect(asked).toHaveBeenCalled();
    expect(MockWebSocket.opened).toHaveLength(1);
    window.removeEventListener("jarvis:agentic-ide-changed", asked);
    socket.close();
  });

  it("asks the view to re-read when the open workspace has no such pane", () => {
    // A pane the workspace does not have means the GRID is out of date — this
    // pane is merely where it became visible. Without the re-read it stays on
    // screen as a dead rectangle for the rest of the session.
    const asked = vi.fn();
    window.addEventListener("jarvis:agentic-ide-changed", asked);
    const cb = handlers();
    const socket = openPaneSocket({ name: "T6", cols: 80, rows: 24 }, cb);

    MockWebSocket.last!.dropped(4404);

    expect(asked).toHaveBeenCalled();
    expect(cb.onTrouble).toHaveBeenCalledWith(expect.any(String), false);
    window.removeEventListener("jarvis:agentic-ide-changed", asked);
    socket.close();
  });

  it("re-reads the state once before settling into the slow knock", async () => {
    // Nine "not yet" answers is the moment a pane drops to one attempt every
    // half minute. A workspace that opened meanwhile announces itself — but an
    // announcement missed while this socket was reconnecting would then take
    // thirty seconds to be noticed, or never.
    const asked = vi.fn();
    window.addEventListener("jarvis:agentic-ide-changed", asked);
    const cb = handlers();
    const socket = openPaneSocket({ name: "T1", cols: 80, rows: 24 }, cb);

    for (let i = 0; i < 9; i += 1) {
      MockWebSocket.last!.dropped(4503);
      await vi.advanceTimersByTimeAsync(30_000);
    }

    expect(asked).toHaveBeenCalledTimes(1);
    window.removeEventListener("jarvis:agentic-ide-changed", asked);
    socket.close();
  });
});

describe("openPaneSocket geometry reconciliation", () => {
  const originalWs = globalThis.WebSocket;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
    (globalThis as unknown as { window: unknown }).window = globalThis;
    (window as unknown as { location: unknown }).location = {
      protocol: "http:",
      host: "localhost:5173",
    };
    MockWebSocket.last = null;
    MockWebSocket.opened = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = originalWs;
  });

  it("passes on the size the agent really got when a resize was refused", () => {
    // The pane already reflowed its own xterm to the tile it measured. If the
    // server turned that size down and the pane never hears so, the two grids
    // disagree for good — and a TUI moving its cursor RELATIVELY then finishes
    // its repaints into rows holding other text. That is the doubled,
    // character-by-character output a narrow pane showed (2026-08-11).
    const onGeometry = vi.fn();
    const socket = openPaneSocket(
      { name: "Mika", cols: 24, rows: 8 },
      { ...handlers(), onGeometry },
    );
    MockWebSocket.last!.fire("open");

    MockWebSocket.last!.deliver({ t: "size", cols: 96, rows: 30 });

    expect(onGeometry).toHaveBeenCalledWith({ cols: 96, rows: 30 });
    socket.close();
  });

  it("ignores a size frame that cannot be a real geometry", () => {
    // A grid of zero columns wrecks a TUI's drawing permanently, so a malformed
    // frame must not be able to do what the floors elsewhere exist to prevent.
    const onGeometry = vi.fn();
    const socket = openPaneSocket(
      { name: "Mika", cols: 80, rows: 24 },
      { ...handlers(), onGeometry },
    );
    MockWebSocket.last!.fire("open");

    MockWebSocket.last!.deliver({ t: "size", cols: 0, rows: 24 });
    MockWebSocket.last!.deliver({ t: "size", cols: 80 });
    MockWebSocket.last!.deliver({ t: "size", cols: "80", rows: "24" });

    expect(onGeometry).not.toHaveBeenCalled();
    socket.close();
  });

  it("survives a client that registered no geometry handler", () => {
    // Every older viewer is one of these: the frame is new, and a pane that
    // ignores it is no worse off than it was before the frame existed.
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, handlers());
    MockWebSocket.last!.fire("open");

    expect(() =>
      MockWebSocket.last!.deliver({ t: "size", cols: 96, rows: 30 }),
    ).not.toThrow();
    socket.close();
  });
});
