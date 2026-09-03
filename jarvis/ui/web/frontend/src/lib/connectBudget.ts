/**
 * One shared speed limit for every socket this tab opens.
 *
 * Every connection borrows an ephemeral port from a pool the operating system
 * owns, and a closed connection keeps that port through TIME_WAIT — 120
 * seconds on Windows, against a pool of 16 384. Empty that pool and NOTHING on
 * the machine can connect any more: not this app, not the file manager, not
 * the start menu, not the browser. The user does not report "the socket pool
 * is empty"; they report that the whole computer went unusable for a few
 * minutes after they came back to it, and then fixed itself. That is BUG-215.
 *
 * The shape of the storm that empties it:
 *
 * 1. Nobody touches the machine for a while. Windows are hidden, timers are
 *    clamped, and the open sockets quietly die without a `close` event — the
 *    peer is gone but `readyState` still says OPEN.
 * 2. The user comes back. `visibilitychange` and `online` fire in the same
 *    millisecond in every window.
 * 3. Every socket client reacts at once and with no delay: the app socket, one
 *    socket per terminal pane, the agent chat, the realtime transport —
 *    multiplied by the number of panes, the number of windows, and the number
 *    of running app instances.
 * 4. Whatever fails retries on a fixed grid, so the second wave lands together
 *    too, and the third.
 *
 * Each piece of that is individually reasonable. Together they are a burst of
 * hundreds of connections with nothing anywhere that can see the total, which
 * is the point: no single client can fix this, because no single client is the
 * problem. So the limit lives here, above all of them.
 *
 * Two mechanisms, and both are needed:
 *
 * - {@link jitteredDelay} spreads retries that would otherwise stay in
 *   lockstep. Exponential backoff alone does not help when twenty clients
 *   start their backoff in the same millisecond — they just collide at 0.5 s,
 *   then at 1 s, then at 2 s. Full jitter picks uniformly from `[0, delay]`,
 *   which is what actually breaks the grid. (Measured in this app on
 *   2026-07-27: five panes asked for 0.5/1/2/4 s and knocked at 10.174,
 *   11.174, 12.162, 13.170 — a flat one-second grid. See `paneSocket.ts`.)
 *
 * - {@link requestConnect} is a token bucket every client passes through.
 *   Jitter alone still allows twenty connections inside one second; the bucket
 *   is the hard ceiling that does not care how many callers there are.
 *
 * Nothing here is a queue you can starve: a caller that waits for a token
 * keeps its place and fires as soon as one frees. The budget delays
 * connections, it never drops them.
 */

/**
 * New connections per second, across every client in this tab.
 *
 * Six is far above what normal use needs — a window opens its app socket, its
 * panes and its transports once — and far below the hundreds a wake storm
 * produced. A reconnect that has to wait for a token is late by milliseconds,
 * which nobody can perceive; the freeze it prevents lasted minutes.
 */
export const CONNECTS_PER_SECOND = 6;

/** Burst allowance, so an ordinary page load is not paced at all. */
export const BURST = 8;

/** Default floor for a first retry, in ms. */
export const MIN_BACKOFF_MS = 500;

/** Default ceiling for a retry wait, in ms. */
export const MAX_BACKOFF_MS = 10_000;

/**
 * Full jitter: a uniform pick from `[0, min(cap, base * 2 ** attempt)]`.
 *
 * "Full" rather than "equal" or "decorrelated" on purpose. The failure here is
 * many clients starting together, and only full jitter puts them at genuinely
 * independent points; the half-range variants keep a floor that they all share
 * and so keep a (thinner) grid.
 *
 * @param attempt Zero-based retry number.
 * @param base Delay for attempt 0, before jitter.
 * @param cap Ceiling on the pre-jitter delay.
 * @param random Injected for tests; defaults to `Math.random`.
 */
export function jitteredDelay(
  attempt: number,
  base: number = MIN_BACKOFF_MS,
  cap: number = MAX_BACKOFF_MS,
  random: () => number = Math.random,
): number {
  const exponent = Math.max(0, Math.floor(attempt));
  // 2 ** 1024 is Infinity, and Infinity * base is NaN — clamp before the shift
  // so a client that somehow reached attempt 2000 still gets `cap`.
  const grown = exponent > 40 ? cap : base * 2 ** exponent;
  const ceiling = Math.min(cap, grown);
  return Math.floor(random() * ceiling);
}

/**
 * Spread a steady interval around itself: a uniform pick from `[d/2, 3d/2]`.
 *
 * For a repeating knock rather than a backoff. Twenty panes that all settled
 * into "try again every 30 s" knock together forever otherwise, because they
 * settled together — the interval is right, the phase is the problem. Full
 * jitter would be wrong here: it halves the average interval and makes a slow
 * knock twice as fast. This keeps the mean and destroys the phase.
 *
 * @param delayMs The interval to keep on average.
 * @param random Injected for tests; defaults to `Math.random`.
 */
export function spreadDelay(delayMs: number, random: () => number = Math.random): number {
  return Math.floor(delayMs / 2 + random() * delayMs);
}

type Waiter = { readonly run: () => void; cancelled: boolean };

/**
 * The token bucket, module-scoped so every importer shares one.
 *
 * Refills continuously rather than on a timer: tokens are computed from the
 * time since the last take, so a tab whose timers were clamped while hidden
 * comes back with a full bucket instead of an empty one.
 */
let tokens = BURST;
let lastRefillAt = now();
const waiting: Waiter[] = [];
let drainTimer: ReturnType<typeof setTimeout> | null = null;

function now(): number {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

function refill(): void {
  const at = now();
  const elapsedS = Math.max(0, (at - lastRefillAt) / 1000);
  if (elapsedS <= 0) return;
  tokens = Math.min(BURST, tokens + elapsedS * CONNECTS_PER_SECOND);
  lastRefillAt = at;
}

/** ms until the bucket holds at least one token. */
function msUntilToken(): number {
  refill();
  if (tokens >= 1) return 0;
  return Math.ceil(((1 - tokens) / CONNECTS_PER_SECOND) * 1000);
}

function drain(): void {
  drainTimer = null;
  while (waiting.length > 0) {
    const head = waiting[0];
    if (head.cancelled) {
      waiting.shift();
      continue;
    }
    const wait = msUntilToken();
    if (wait > 0) {
      drainTimer = setTimeout(drain, wait);
      return;
    }
    waiting.shift();
    tokens -= 1;
    try {
      head.run();
    } catch {
      // A caller that throws on connect must not stall everyone behind it. The
      // client owns its own error reporting; the budget only owes it a turn.
    }
  }
}

/**
 * Ask to connect after `delayMs`, then as soon as the shared budget allows.
 *
 * @param open What to run when the turn comes. Should start ONE connection.
 * @param delayMs How long to wait first — pass {@link jitteredDelay} for a
 *   retry, or 0 for a first attempt (which the burst allowance absorbs).
 * @returns A cancel function. Safe to call more than once, and safe to call
 *   after `open` has already run.
 */
export function requestConnect(open: () => void, delayMs = 0): () => void {
  const waiter: Waiter = { run: open, cancelled: false };
  let timer: ReturnType<typeof setTimeout> | null = setTimeout(
    () => {
      timer = null;
      if (waiter.cancelled) return;
      waiting.push(waiter);
      if (drainTimer === null) drain();
    },
    Math.max(0, delayMs),
  );
  return () => {
    waiter.cancelled = true;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };
}

/** Drop every pending request and refill the bucket. Tests only. */
export function resetConnectBudgetForTests(): void {
  for (const waiter of waiting) waiter.cancelled = true;
  waiting.length = 0;
  if (drainTimer !== null) {
    clearTimeout(drainTimer);
    drainTimer = null;
  }
  tokens = BURST;
  lastRefillAt = now();
}
