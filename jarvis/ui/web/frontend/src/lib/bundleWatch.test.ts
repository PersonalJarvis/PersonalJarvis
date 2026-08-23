/**
 * A rebuilt frontend has to reach an open window on its own.
 *
 * The failure this covers is not a crash — it is a window that keeps running
 * the previous build and gives no sign of it, which for a long time made
 * "restart the whole application" the routine answer to a one-file frontend
 * change. See lib/bundleWatch.
 */
import { describe, expect, it, vi } from "vitest";
import {
  HIDDEN_EVERY_N_TICKS,
  STALE_INFLIGHT_TICKS,
  bundleFingerprint,
  installBundleWatch,
  shouldReload,
  type BundleWatchDeps,
} from "./bundleWatch";

function indexHtml(hash: string): string {
  return [
    "<!doctype html><html><head>",
    `<script type="module" crossorigin src="/assets/index-${hash}.js"></script>`,
    `<link rel="stylesheet" crossorigin href="/assets/index-${hash}.css">`,
    "</head><body><div id=root></div></body></html>",
  ].join("");
}

interface Harness {
  deps: BundleWatchDeps;
  serve: (html: string) => void;
  idle: (ms: number | null) => void;
  hide: (hidden: boolean) => void;
  tick: () => Promise<void>;
  reload: ReturnType<typeof vi.fn>;
  fetches: () => number;
}

function harness(initial: string): Harness {
  let html = initial;
  let idleMs: number | null = null;
  let hidden = false;
  let calls = 0;
  let scheduled: (() => void) | null = null;
  const reload = vi.fn();
  const deps: BundleWatchDeps = {
    fetchIndex: () => {
      calls += 1;
      return Promise.resolve(html);
    },
    reload,
    idleFor: () => idleMs,
    visible: () => !hidden,
    every: (fn) => {
      scheduled = fn;
      return 7;
    },
    stop: () => {
      scheduled = null;
    },
  };
  return {
    deps,
    serve: (next) => {
      html = next;
    },
    idle: (ms) => {
      idleMs = ms;
    },
    hide: (next) => {
      hidden = next;
    },
    tick: async () => {
      scheduled?.();
      // Let the fetch chain settle; the watch is promise-based throughout.
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    },
    reload,
    fetches: () => calls,
  };
}

describe("bundleFingerprint", () => {
  it("identifies a build by its hashed assets and ignores their order", () => {
    expect(bundleFingerprint(indexHtml("AAA"))).toBe(
      "/assets/index-AAA.css /assets/index-AAA.js",
    );
    expect(bundleFingerprint(indexHtml("AAA"))).not.toBe(
      bundleFingerprint(indexHtml("BBB")),
    );
    const reordered =
      '<link href="/assets/index-AAA.css"><script src="/assets/index-AAA.js">';
    expect(bundleFingerprint(reordered)).toBe(
      bundleFingerprint(indexHtml("AAA")),
    );
  });

  it("has no fingerprint for a document with no bundle behind it", () => {
    // The server's placeholder page, or a failed request handed on as "".
    expect(bundleFingerprint("<html><body>starting…</body></html>")).toBe("");
    expect(bundleFingerprint("")).toBe("");
  });
});

describe("shouldReload", () => {
  const baseline = "old";

  it("waits for the same new build twice before acting on it", () => {
    // A build in progress can publish an index whose chunks are not all
    // written; reloading into that is the flicker loop preloadRecovery exists
    // for. One confirmation is the difference.
    expect(
      shouldReload({ baseline, seen: "new", confirmed: null, idleMs: null }),
    ).toBe(false);
    expect(
      shouldReload({ baseline, seen: "new", confirmed: "new", idleMs: null }),
    ).toBe(true);
    expect(
      shouldReload({ baseline, seen: "new", confirmed: "other", idleMs: null }),
    ).toBe(false);
  });

  it("does not interrupt someone who is typing", () => {
    expect(
      shouldReload({ baseline, seen: "new", confirmed: "new", idleMs: 200 }),
    ).toBe(false);
    expect(
      shouldReload({ baseline, seen: "new", confirmed: "new", idleMs: 5_000 }),
    ).toBe(true);
  });

  it("never reloads for an unchanged or unreadable answer", () => {
    expect(
      shouldReload({
        baseline,
        seen: baseline,
        confirmed: baseline,
        idleMs: null,
      }),
    ).toBe(false);
    expect(
      shouldReload({ baseline, seen: "", confirmed: "", idleMs: null }),
    ).toBe(false);
    expect(
      shouldReload({
        baseline: "",
        seen: "new",
        confirmed: "new",
        idleMs: null,
      }),
    ).toBe(false);
  });
});

describe("installBundleWatch", () => {
  it("reloads a window that is running a build the server has replaced", async () => {
    const app = harness(indexHtml("AAA"));
    installBundleWatch(app.deps);
    await app.tick();

    app.serve(indexHtml("BBB"));
    await app.tick();
    expect(app.reload).not.toHaveBeenCalled();

    await app.tick();
    expect(app.reload).toHaveBeenCalledOnce();
  });

  it("adopts whatever it loaded and stays quiet while the build does not change", async () => {
    const app = harness(indexHtml("AAA"));
    installBundleWatch(app.deps);
    await app.tick();
    await app.tick();
    await app.tick();

    expect(app.reload).not.toHaveBeenCalled();
  });

  it("waits for a typing user to pause", async () => {
    const app = harness(indexHtml("AAA"));
    installBundleWatch(app.deps);
    await app.tick();
    app.idle(150);
    app.serve(indexHtml("BBB"));
    await app.tick();
    await app.tick();
    expect(app.reload).not.toHaveBeenCalled();

    app.idle(9_000);
    await app.tick();
    expect(app.reload).toHaveBeenCalledOnce();
  });

  it("asks nothing of a hidden window and resumes when it comes back", async () => {
    const app = harness(indexHtml("AAA"));
    installBundleWatch(app.deps);
    await app.tick();
    const beforeHiding = app.fetches();

    app.hide(true);
    await app.tick();
    await app.tick();
    expect(app.fetches()).toBe(beforeHiding);

    app.hide(false);
    app.serve(indexHtml("BBB"));
    await app.tick();
    await app.tick();
    expect(app.reload).toHaveBeenCalledOnce();
  });

  it("still picks up a rebuild in a window that claims to be hidden forever", async () => {
    // The desktop shell's engine was measured reporting an on-screen window as
    // hidden for minutes (2026-08-23). A watch that trusts it never polls again
    // and the window runs a stale build through every later rebuild.
    const app = harness(indexHtml("AAA"));
    installBundleWatch(app.deps);
    await app.tick();

    app.hide(true);
    app.serve(indexHtml("BBB"));
    for (let i = 0; i < HIDDEN_EVERY_N_TICKS * 2; i += 1) {
      await app.tick();
    }

    expect(app.reload).toHaveBeenCalledOnce();
  });

  it("does not let one check that never answers hold the watch forever", async () => {
    const app = harness(indexHtml("AAA"));
    let first = true;
    const stalling: BundleWatchDeps = {
      ...app.deps,
      fetchIndex: () => {
        if (first) {
          first = false;
          return new Promise<string>(() => {
            /* never settles */
          });
        }
        return app.deps.fetchIndex();
      },
    };
    installBundleWatch(stalling);
    // The opening check is the one that hangs.
    for (let i = 0; i < STALE_INFLIGHT_TICKS; i += 1) {
      await app.tick();
    }
    // By now a fresh check has established the baseline; a rebuild gets seen.
    await app.tick();
    app.serve(indexHtml("BBB"));
    await app.tick();
    await app.tick();

    expect(app.reload).toHaveBeenCalledOnce();
  });

  it("treats a failed check as no information at all", async () => {
    const app = harness(indexHtml("AAA"));
    const failing: BundleWatchDeps = {
      ...app.deps,
      fetchIndex: () => Promise.reject(new Error("server restarting")),
    };
    installBundleWatch(failing);
    await app.tick();
    await app.tick();

    expect(app.reload).not.toHaveBeenCalled();
  });

  it("stops checking once the watch is torn down", async () => {
    const app = harness(indexHtml("AAA"));
    const stop = installBundleWatch(app.deps);
    await app.tick();
    const before = app.fetches();

    stop();
    await app.tick();
    expect(app.fetches()).toBe(before);
  });
});
