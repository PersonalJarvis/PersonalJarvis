/**
 * "The person came back to this window": one coalesced, jittered callback.
 *
 * A window that wakes from sleep fires `focus` and `visibilitychange` in the
 * same millisecond in EVERY window of EVERY instance, and a refetch on each is
 * how a wake storm starts (AP-33, BUG-215). This helper is the one place the
 * permission surfaces react to a return:
 *
 * - `focus` and `visibilitychange` (to visible) are coalesced: while a call is
 *   scheduled, further events are ignored.
 * - The call goes through the shared connect budget with a {@link spreadDelay}
 *   so N windows do not knock in the same tick.
 * - `minHiddenMs` skips returns after a short absence (the store seed only
 *   needs to catch up after a long one).
 * - There is NO interval: nothing runs while the person is away or staying.
 *
 * The callback is expected to be single-flight itself (the store seed and the
 * layer's row refetch both are).
 *
 * {@link onSharedReturnToWindow} is the entry the permission surfaces use: ONE
 * listener and ONE jittered timer for the whole window, fanned out to every
 * subscriber, so a wake with the card, the Privacy page and several shortcut
 * notes mounted is a single scheduled tick instead of one per surface.
 */
import { requestConnect, spreadDelay } from "./connectBudget";

/** Events closer together than this are one return. */
export const FOCUS_COALESCE_MS = 250;

export interface FocusRefreshOptions {
  /** Skip returns after a shorter absence than this. Default 0 (every return). */
  minHiddenMs?: number;
  /** Injected for tests. */
  random?: () => number;
  now?: () => number;
}

/** Subscribe to returns to the window. Returns the unsubscribe function. */
export function onReturnToWindow(
  run: (awayMs: number) => void,
  options: FocusRefreshOptions = {},
): () => void {
  const { minHiddenMs = 0, random = Math.random, now = Date.now } = options;
  let hiddenSince: number | null = document.visibilityState === "hidden" ? now() : null;
  let cancelScheduled: (() => void) | null = null;

  const markAway = () => {
    if (hiddenSince === null) hiddenSince = now();
  };

  const schedule = () => {
    if (document.visibilityState === "hidden") return;
    const awayFor = hiddenSince === null ? 0 : now() - hiddenSince;
    if (cancelScheduled) return; // already coming: coalesce
    hiddenSince = null;
    if (awayFor < minHiddenMs) return;
    cancelScheduled = requestConnect(() => {
      cancelScheduled = null;
      if (document.visibilityState !== "hidden") run(awayFor);
    }, spreadDelay(FOCUS_COALESCE_MS, random));
  };

  const onVisibility = () => {
    if (document.visibilityState === "hidden") markAway();
    else schedule();
  };

  window.addEventListener("blur", markAway);
  window.addEventListener("focus", schedule);
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    window.removeEventListener("blur", markAway);
    window.removeEventListener("focus", schedule);
    document.removeEventListener("visibilitychange", onVisibility);
    cancelScheduled?.();
    cancelScheduled = null;
  };
}

interface SharedListener {
  run: () => void;
  minHiddenMs: number;
}

const sharedListeners = new Set<SharedListener>();
let stopShared: (() => void) | null = null;

/**
 * Like {@link onReturnToWindow}, but every subscriber shares one listener and one
 * jittered timer: one return to the window is one tick that calls each of them.
 * `minHiddenMs` is per subscriber (a surface that only needs to catch up after
 * a long absence). Returns the unsubscribe function; the shared listener goes
 * away with its last subscriber.
 */
export function onSharedReturnToWindow(
  run: () => void,
  options: { minHiddenMs?: number } = {},
): () => void {
  const entry: SharedListener = { run, minHiddenMs: options.minHiddenMs ?? 0 };
  sharedListeners.add(entry);
  if (!stopShared) {
    stopShared = onReturnToWindow((awayMs) => {
      for (const listener of [...sharedListeners]) {
        if (awayMs < listener.minHiddenMs) continue;
        try {
          listener.run();
        } catch (exc) {
          // One surface's failure must not keep the others from refreshing.
          console.error("return-to-window listener failed", exc);
        }
      }
    });
  }
  return () => {
    sharedListeners.delete(entry);
    if (sharedListeners.size === 0 && stopShared) {
      stopShared();
      stopShared = null;
    }
  };
}
