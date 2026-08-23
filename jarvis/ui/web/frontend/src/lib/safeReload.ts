/**
 * Reload only when a reload will land on something that runs.
 *
 * Three separate mechanisms in this app reload the window by themselves — the
 * bundle watch when a rebuild is published, the preload recovery when a lazy
 * chunk has been deleted under it, and the blank-window watchdog in
 * `index.html` when `#root` goes empty. Every one of them called
 * `location.reload()` unconditionally, and that is a one-way door: the moment
 * the navigation starts, the running bundle is gone. If the document that
 * comes back cannot boot, there is no JavaScript left to try again — the
 * window is whatever the browser painted, and it stays that way.
 *
 * That is not hypothetical. `npm run build` runs with `emptyOutDir`, so
 * `dist/` passes through a state where `dist/assets/` is already deleted while
 * the previous `index.html` is still on disk. Measured against the live server
 * with a 200 ms poll during a real rebuild (2026-08-22): 1.8 s of exactly that
 * document, then 5.2 s with no index at all, then the new build. A window that
 * reloads into that first window gets the boot splash and a `<script
 * type="module">` that 404s — no React, no bundle watch, no preload recovery —
 * and the only thing left is the watchdog's twenty-second clock. Twenty
 * seconds of a near-black window with a small spinner is what gets reported as
 * "it goes black when it reloads by itself".
 *
 * The server side now refuses to serve that document at all (see
 * `jarvis/ui/web/spa_build.py`). This is the other half, and it is the half
 * that also covers a server which is restarting, a machine that just woke, and
 * every future way the entry document can be briefly unavailable: ask what a
 * fresh window would load, confirm those files actually answer, and only then
 * spend the reload.
 *
 * It never gives up and it never reloads out of impatience. A window that keeps
 * showing the app it already has is strictly better than one showing nothing.
 */

/** Marks the server's holding page. Mirrors `HOLDING_MARKER` in spa_build.py. */
export const HOLDING_MARKER = "jarvis-build-holding";

/**
 * How the holding page carries its marker — and the ONLY form a document is
 * tested against.
 *
 * The bare word is not enough: the real `index.html` ships an inline watchdog
 * that names the marker in its own source, so a test for the word alone takes
 * every genuine build for the holding page and never reloads into it. That is
 * what kept every open window on a stale build from 2026-08-22 to 2026-08-23
 * (bundle watch, preload recovery and the stale-chunk card all funnel through
 * here). The attribute form appears on the holding page's own element and
 * nowhere else; `index.html` composes it at runtime for the same reason. See
 * the pinning test in safeReload.test.ts and bootWatchdog.test.ts.
 */
export const HOLDING_ATTRIBUTE = `data-state="${HOLDING_MARKER}"`;

/** Delay between checks while a build is in flight. */
export const RETRY_MS = 700;

/** Delay after {@link FAST_TRIES} checks, so a long outage polls gently. */
export const SLOW_RETRY_MS = 3_000;

/** How many quick checks before backing off to {@link SLOW_RETRY_MS}. */
export const FAST_TRIES = 20;

export interface SafeReloadDeps {
  /** Fetch the SPA entry document, or "" when the request failed. */
  fetchIndex: () => Promise<string>;
  /** Does this URL answer with a real file? */
  assetOk: (url: string) => Promise<boolean>;
  /** Perform the actual navigation. */
  reload: () => void;
  /** Schedule the next check. */
  defer: (fn: () => void, ms: number) => void;
}

/**
 * The hashed build outputs *html* tells a browser to load.
 *
 * Mirrors `referenced_assets` in spa_build.py — the same question asked from
 * the other side of the wire.
 */
export function entryAssets(html: string): string[] {
  return [...new Set(html.match(/\/assets\/[A-Za-z0-9._-]+/g) ?? [])];
}

/**
 * Could this document boot a window?
 *
 * False for an empty/failed response, for the server's holding page, and for
 * an index that references nothing — none of which is a build a window can
 * run. The files themselves are checked separately, because their absence is
 * exactly the failure this module exists for.
 */
export function looksBootable(html: string): boolean {
  if (!html || html.includes(HOLDING_ATTRIBUTE)) return false;
  return entryAssets(html).length > 0;
}

/**
 * Reload as soon as — and not before — the server can hand out a whole build.
 *
 * Returns immediately; the work continues on the deps' timer. Calling it twice
 * simply runs two checks, so callers that can fire repeatedly should guard
 * themselves (the bundle watch and the preload recovery both already do).
 */
export function reloadWhenServable(deps: SafeReloadDeps): void {
  let tries = 0;

  const again = () => {
    deps.defer(check, tries < FAST_TRIES ? RETRY_MS : SLOW_RETRY_MS);
  };

  const check = () => {
    tries += 1;
    void deps
      .fetchIndex()
      .then(async (html) => {
        if (!looksBootable(html)) {
          again();
          return;
        }
        for (const asset of entryAssets(html)) {
          if (!(await deps.assetOk(asset))) {
            // A rebuild is mid-flight: the index is new, its files are not all
            // written. Reloading now is the twenty-second black window.
            again();
            return;
          }
        }
        deps.reload();
      })
      .catch(() => {
        // The server restarting, a dropped socket, a sleeping machine: keep the
        // page that is on screen and ask again.
        again();
      });
  };

  check();
}

/** The deps a real browser window uses. Kept here so every caller agrees. */
export function browserSafeReloadDeps(): SafeReloadDeps {
  return {
    fetchIndex: () =>
      fetch("/", { cache: "no-store", headers: { Accept: "text/html" } })
        .then((response) => (response.ok ? response.text() : ""))
        .catch(() => ""),
    assetOk: (url) =>
      fetch(url, { method: "HEAD", cache: "no-store" })
        .then((response) => response.ok)
        .catch(() => false),
    reload: () => window.location.reload(),
    defer: (fn, ms) => {
      window.setTimeout(fn, ms);
    },
  };
}
