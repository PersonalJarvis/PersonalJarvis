/**
 * The reload that heals a rebuilt bundle must never become the reload loop.
 *
 * The loop is the live failure this module was written for (2026-07-28 18:51):
 * a rebuild deletes the old chunks before writing the new ones, so a reload
 * lands on the same missing chunk, asks for another reload, and the window
 * flickers between "Checking access…", the boot shell and a brief
 * "Ready for commands" for as long as the build runs.
 */

import { describe, expect, it, vi } from "vitest";

import {
  RELOAD_GUARD_KEY,
  SETTLE_MS,
  handlePreloadError,
  installPreloadRecovery,
  isChunkLoadError,
  type PreloadRecoveryDeps,
} from "./preloadRecovery";

/** A sessionStorage stand-in that survives across simulated reloads. */
function makeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => {
      data.set(k, v);
    },
    removeItem: (k: string) => {
      data.delete(k);
    },
  };
}

function makeDeps(storage = makeStorage()) {
  const reload = vi.fn();
  const deferred: Array<{ fn: () => void; ms: number }> = [];
  const deps: PreloadRecoveryDeps = {
    storage,
    reload,
    defer: (fn, ms) => {
      deferred.push({ fn, ms });
    },
  };
  return { deps, storage, reload, deferred };
}

describe("preload recovery", () => {
  it("reloads once so a rebuilt bundle is picked up", () => {
    const { deps, reload, storage } = makeDeps();

    expect(handlePreloadError(deps)).toBe(true);

    expect(reload).toHaveBeenCalledTimes(1);
    expect(storage.getItem(RELOAD_GUARD_KEY)).toBe("1");
  });

  it("does NOT reload again when the fresh page hits the same missing chunk", () => {
    // The guard is written before the reload, so the page that comes back
    // starts with it already set — this is the loop's second pass.
    const { deps, reload } = makeDeps(makeStorage({ [RELOAD_GUARD_KEY]: "1" }));

    expect(handlePreloadError(deps)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it("the guard is NOT released by the page finishing its load", () => {
    // The regression: the release used to hang off `window.load`, which fires
    // before any lazy chunk is imported and therefore before the error it was
    // supposed to bound. Installing must not clear the guard by itself.
    const { deps, storage, deferred } = makeDeps(
      makeStorage({ [RELOAD_GUARD_KEY]: "1" }),
    );
    const target = { addEventListener: vi.fn() };

    installPreloadRecovery(deps, target);

    expect(storage.getItem(RELOAD_GUARD_KEY)).toBe("1");
    // Released only on the settle timer, not on any page lifecycle event.
    expect(deferred).toHaveLength(1);
    expect(deferred[0].ms).toBe(SETTLE_MS);
  });

  it("releases the guard once the page has survived the settle window", () => {
    // A LATER rebuild in the same session must still heal on its own.
    const { deps, storage, deferred, reload } = makeDeps(
      makeStorage({ [RELOAD_GUARD_KEY]: "1" }),
    );

    installPreloadRecovery(deps, { addEventListener: vi.fn() });
    deferred[0].fn();

    expect(storage.getItem(RELOAD_GUARD_KEY)).toBeNull();
    expect(handlePreloadError(deps)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("listens for the preload error and reloads on it", () => {
    const { deps, reload } = makeDeps();
    const listeners: EventListener[] = [];
    installPreloadRecovery(deps, {
      addEventListener: ((_name: string, fn: EventListener) => {
        listeners.push(fn);
      }) as Window["addEventListener"],
    });

    const preventDefault = vi.fn();
    listeners[0]({ preventDefault } as unknown as Event);

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("NEVER cancels the event, so the failed import still rejects", () => {
    // The regression this pins (2026-08-23): the listener cancelled the event
    // unconditionally. Vite's preload helper is installed as
    // `import(...).catch(handler)` and only rethrows when the event was not
    // cancelled — so cancelling made every failed dynamic import RESOLVE WITH
    // `undefined`. The caller's `.then((mod) => ({ default: mod.WikiGraph }))`
    // then died as "Cannot read properties of undefined (reading
    // 'WikiGraph')", which is how a rebuild during an open window was reported
    // as a crash in the wiki system.
    //
    // Both passes matter: the one that reloads and the one the guard stops.
    const { deps } = makeDeps();
    const listeners: EventListener[] = [];
    installPreloadRecovery(deps, {
      addEventListener: ((_name: string, fn: EventListener) => {
        listeners.push(fn);
      }) as Window["addEventListener"],
    });

    const preventDefault = vi.fn();
    listeners[0]({ preventDefault } as unknown as Event);
    listeners[0]({ preventDefault } as unknown as Event);

    expect(preventDefault).not.toHaveBeenCalled();
  });

  describe("isChunkLoadError", () => {
    it.each([
      // Chromium — the message the desktop WebView actually produces.
      "Failed to fetch dynamically imported module: http://127.0.0.1:47821/assets/WikiGraph-ZXJsF_pi.js",
      // Firefox.
      "error loading dynamically imported module",
      // WebKit.
      "Importing a module script failed.",
      // Vite's own preload helper.
      "Unable to preload CSS for /assets/AgenticIdeView-CFbL2ovg.css",
    ])("recognises %s", (message) => {
      expect(isChunkLoadError(new Error(message))).toBe(true);
    });

    it("does not claim a real component bug is a stale chunk", () => {
      expect(
        isChunkLoadError(
          new TypeError("Cannot read properties of undefined (reading 'nodes')"),
        ),
      ).toBe(false);
      expect(isChunkLoadError(undefined)).toBe(false);
      expect(isChunkLoadError("boom")).toBe(false);
    });
  });

  it("never reloads when the guard cannot be remembered", () => {
    // No storage means no way to bound a loop, so no loop is started.
    const reload = vi.fn();
    const blocked: PreloadRecoveryDeps = {
      storage: {
        getItem: () => null,
        setItem: () => {
          throw new Error("storage is full");
        },
        removeItem: () => undefined,
      },
      reload,
      defer: () => undefined,
    };

    expect(handlePreloadError(blocked)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
