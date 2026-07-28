/**
 * Recover from a lazy chunk that is no longer on disk — exactly once.
 *
 * When the frontend is rebuilt while the desktop window is open, the running
 * main bundle still asks for lazy chunks by their previous content hash. Those
 * URLs are gone, the dynamic `import()` rejects, and whichever view tried to
 * load hard-crashes. Vite announces precisely this case as `vite:preloadError`,
 * and reloading once picks up the fresh bundle — `index.html` is served
 * `no-store`, so a reload really does get the new hashes.
 *
 * The danger is the other half: a rebuild is not instantaneous. While
 * `npm run build` runs, `dist/` passes through a state where the old chunks are
 * already deleted and the new ones are not written yet, so a reload lands on
 * the same missing chunk and asks for another reload. That is a loop measured
 * on 2026-07-28 18:51 — the window cycled through "Checking access…", the
 * boot shell and a brief "Ready for commands" several times within about three
 * seconds, which is what a user sees as a violently flickering app that never
 * finishes starting.
 *
 * A guard existed for that loop and could not work: it was cleared on the
 * window's `load` event, which fires when the document and its static
 * resources are done — always BEFORE a lazy chunk is imported, and therefore
 * always before the error it was meant to bound. Every pass through the loop
 * found the guard freshly cleared.
 *
 * So the release is tied to evidence instead of to `load`: the guard is lifted
 * only once the app has run {@link SETTLE_MS} without a preload error, which
 * means this page is healthy and a LATER rebuild may heal itself the same way.
 * A second failure inside that window is the loop, and it stops there — the
 * failed import surfaces through the view's error boundary, which is a
 * readable card instead of a flicker.
 */

/** sessionStorage key holding "a reload has already been spent on this". */
export const RELOAD_GUARD_KEY = "jarvis:preload-reloaded";

/**
 * How long a page must survive before another automatic reload is allowed.
 *
 * Long enough to cover a rebuild in progress (Vite's window with chunks
 * deleted and not yet rewritten is seconds, not tens of seconds), short enough
 * that a second rebuild later in the same session still heals on its own.
 */
export const SETTLE_MS = 30_000;

export interface PreloadRecoveryDeps {
  /** Where the one-reload-per-incident mark lives. Survives a reload. */
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem">;
  /** What to do when a reload is warranted. */
  reload: () => void;
  /** Defer the settle release. Returns nothing — this is never cancelled. */
  defer: (fn: () => void, ms: number) => void;
}

/**
 * Decide what a preload error means right now, and act on it.
 *
 * Returns whether a reload was triggered — the caller does not need it, but a
 * test does, and it keeps the decision inspectable.
 */
export function handlePreloadError(deps: PreloadRecoveryDeps): boolean {
  let alreadySpent: string | null = null;
  try {
    alreadySpent = deps.storage.getItem(RELOAD_GUARD_KEY);
  } catch {
    // A storage that refuses to be read (private mode, a locked profile) must
    // not turn a recoverable chunk error into an unrecoverable one. Treating
    // it as "already spent" is the safe side: no reload, no loop.
    return false;
  }
  if (alreadySpent) return false;
  try {
    deps.storage.setItem(RELOAD_GUARD_KEY, "1");
  } catch {
    // Cannot remember that a reload was spent → cannot bound the loop → do
    // not start one.
    return false;
  }
  deps.reload();
  return true;
}

/**
 * Wire the recovery up to the page.
 *
 * Registers the listener and schedules the settle release. Safe in any
 * environment: without `window` it does nothing at all.
 */
export function installPreloadRecovery(
  deps: PreloadRecoveryDeps,
  target: Pick<Window, "addEventListener"> | undefined = typeof window === "undefined"
    ? undefined
    : window,
): void {
  if (!target) return;

  target.addEventListener("vite:preloadError", ((event: Event) => {
    // Vite's default for an unhandled preload error is to reload the page
    // itself; preventing it keeps the decision here, where it is bounded.
    event.preventDefault();
    handlePreloadError(deps);
  }) as EventListener);

  // This page has been up for a while without a single preload failure, so
  // whatever was mid-rebuild is finished and the bundle it is running is
  // coherent. A future rebuild gets its own reload.
  deps.defer(() => {
    try {
      deps.storage.removeItem(RELOAD_GUARD_KEY);
    } catch {
      /* nothing to release */
    }
  }, SETTLE_MS);
}
