/**
 * Notice that the frontend on disk is newer than the one this window is
 * running, and pick it up without anyone restarting anything.
 *
 * The desktop window has no reload key and no dev tools, so for a long time the
 * only way to see a rebuilt frontend was the in-app Restart button — which
 * stops and starts the whole application, voice stack and agent panes included,
 * to replace files the browser would have fetched on its own. `index.html` is
 * served `no-store` and every asset carries a content hash, so a plain reload
 * has always been enough. This module is the missing trigger.
 *
 * It complements ./preloadRecovery rather than replacing it: that one heals a
 * window that has ALREADY crashed on a chunk deleted by a rebuild, which only
 * happens when the user navigates into a view that was not loaded yet. A window
 * sitting on an open view never asks for a missing chunk and stays stale
 * forever. This one asks the server what a fresh window would load.
 *
 * Two rules keep it from being worse than the problem:
 *
 * * a change must survive two consecutive polls, because a build in progress
 *   publishes an `index.html` whose chunks are not all written yet, and
 *   reloading into that is the flicker loop preloadRecovery was written for;
 * * the user must be idle for a moment, because a reload in the middle of a
 *   sentence loses the sentence.
 */

/** How often the served bundle is checked, while the window is visible. */
export const POLL_MS = 5_000;

/** Quiet time required before a reload is allowed to interrupt. */
export const IDLE_MS = 2_000;

/**
 * What the running document would have to load to be current.
 *
 * The hashed asset URLs in `index.html` ARE the identity of a build, so a
 * rebuild that changes nothing produces the same fingerprint and nothing
 * happens. Sorted because the order of preload links is not a promise.
 */
export function bundleFingerprint(html: string): string {
  const assets = html.match(/\/assets\/[A-Za-z0-9._-]+/g);
  if (!assets) return "";
  return [...new Set(assets)].sort().join(" ");
}

export interface BundleWatchDeps {
  /** Fetch the SPA entry document the server would hand a fresh window. */
  fetchIndex: () => Promise<string>;
  /** Reload this window. */
  reload: () => void;
  /** Milliseconds since the last keystroke or pointer press, or null if none. */
  idleFor: () => number | null;
  /** Is this window on screen? A hidden window polls nothing. */
  visible: () => boolean;
  /** Schedule the repeating check. Returns a handle for {@link stop}. */
  every: (fn: () => void, ms: number) => number;
  /** Cancel a handle from {@link BundleWatchDeps.every}. */
  stop: (handle: number) => void;
}

/**
 * The decision, isolated from every timer and every fetch.
 *
 * `baseline` is what this window is running, `seen` the fingerprint just
 * fetched, `confirmed` the one already seen changed on the previous poll.
 */
export function shouldReload({
  baseline,
  seen,
  confirmed,
  idleMs,
}: {
  baseline: string;
  seen: string;
  confirmed: string | null;
  idleMs: number | null;
}): boolean {
  if (!baseline || !seen || seen === baseline) return false;
  // A build in flight can serve a half-written index; the same fingerprint
  // twice means the build finished and this is what it produced.
  if (confirmed !== seen) return false;
  // No input at all means nothing to interrupt.
  return idleMs === null || idleMs >= IDLE_MS;
}

/**
 * Start watching. Returns a function that stops the watch.
 *
 * The first successful fetch establishes what this window is running, so a
 * window that starts up during a rebuild adopts whatever it actually loaded
 * rather than reloading into it.
 */
export function installBundleWatch(deps: BundleWatchDeps): () => void {
  let baseline = "";
  let confirmed: string | null = null;
  let busy = false;

  const check = () => {
    if (busy || !deps.visible()) return;
    busy = true;
    void deps
      .fetchIndex()
      .then((html) => {
        const seen = bundleFingerprint(html);
        if (!seen) return;
        if (!baseline) {
          baseline = seen;
          return;
        }
        if (shouldReload({ baseline, seen, confirmed, idleMs: deps.idleFor() })) {
          deps.reload();
          return;
        }
        confirmed = seen === baseline ? null : seen;
      })
      .catch(() => {
        // The server restarting, a dropped socket, a machine asleep: the next
        // poll asks again. A window must never reload because a request failed.
      })
      .finally(() => {
        busy = false;
      });
  };

  check();
  const handle = deps.every(check, POLL_MS);
  return () => deps.stop(handle);
}
