import { describe, expect, test } from "vitest";
import {
  entryAssets,
  HOLDING_MARKER,
  looksBootable,
  reloadWhenServable,
  type SafeReloadDeps,
} from "./safeReload";

const INDEX = `<!doctype html><html><head>
<script type="module" crossorigin src="/assets/index-abc123.js"></script>
<link rel="stylesheet" href="/assets/index-def456.css">
</head><body><div id="root"></div></body></html>`;

/**
 * A deps double whose timers run immediately, so a test can watch the retry
 * loop converge without a clock. `defer` is counted rather than delayed —
 * what matters is that it keeps asking and does NOT reload.
 */
function harness(pages: string[], assets: (url: string) => boolean) {
  let reloads = 0;
  let waits = 0;
  const asked: string[] = [];
  const deps: SafeReloadDeps = {
    fetchIndex: () => Promise.resolve(pages.length > 1 ? pages.shift()! : pages[0]),
    assetOk: (url) => {
      asked.push(url);
      return Promise.resolve(assets(url));
    },
    reload: () => {
      reloads += 1;
    },
    defer: (fn) => {
      waits += 1;
      // Bounded so a test that never converges fails instead of hanging.
      if (waits < 12) void Promise.resolve().then(fn);
    },
  };
  return {
    deps,
    reloads: () => reloads,
    waits: () => waits,
    asked: () => asked,
  };
}

/** Let every queued microtask (the fetch chains) run. */
const settle = () => new Promise((r) => setTimeout(r, 0));

describe("entryAssets", () => {
  test("finds every hashed build output once", () => {
    expect(entryAssets(INDEX)).toEqual([
      "/assets/index-abc123.js",
      "/assets/index-def456.css",
    ]);
  });

  test("a document with no build outputs yields nothing", () => {
    expect(entryAssets("<html><body>nothing here</body></html>")).toEqual([]);
  });
});

describe("looksBootable", () => {
  test("a real index is bootable", () => {
    expect(looksBootable(INDEX)).toBe(true);
  });

  test("a failed request is not", () => {
    expect(looksBootable("")).toBe(false);
  });

  test("the server's holding page is not — it says a build is in flight", () => {
    expect(looksBootable(`<html><div data-state="${HOLDING_MARKER}"></div></html>`)).toBe(
      false,
    );
  });
});

describe("reloadWhenServable", () => {
  test("a whole build on disk is reloaded into", async () => {
    const h = harness([INDEX], () => true);
    reloadWhenServable(h.deps);
    await settle();
    expect(h.reloads()).toBe(1);
    expect(h.asked()).toEqual(["/assets/index-abc123.js", "/assets/index-def456.css"]);
  });

  test("an index whose entry bundle 404s is NOT reloaded into", async () => {
    // The 1.8 s window a real rebuild opens: index.html still on disk, its
    // assets already deleted. This is the black window, and the whole point.
    const h = harness([INDEX], (url) => !url.endsWith(".js"));
    reloadWhenServable(h.deps);
    await settle();
    expect(h.reloads()).toBe(0);
    expect(h.waits()).toBeGreaterThan(0);
  });

  test("the holding page is waited out, never reloaded into", async () => {
    const h = harness([`<html><div data-state="${HOLDING_MARKER}"></div></html>`], () => true);
    reloadWhenServable(h.deps);
    await settle();
    expect(h.reloads()).toBe(0);
  });

  test("a server that is not answering keeps the page that is on screen", async () => {
    const h = harness([""], () => true);
    reloadWhenServable(h.deps);
    await settle();
    expect(h.reloads()).toBe(0);
    expect(h.waits()).toBeGreaterThan(0);
  });

  test("it reloads as soon as the build lands, not before", async () => {
    const pages = [
      `<html><div data-state="${HOLDING_MARKER}"></div></html>`,
      `<html><div data-state="${HOLDING_MARKER}"></div></html>`,
      INDEX,
    ];
    const h = harness(pages, () => true);
    reloadWhenServable(h.deps);
    await settle();
    expect(h.reloads()).toBe(1);
  });

  test("a fetch that throws is a retry, never a reload", async () => {
    let reloads = 0;
    let waits = 0;
    const deps: SafeReloadDeps = {
      fetchIndex: () => Promise.reject(new Error("connection refused")),
      assetOk: () => Promise.resolve(true),
      reload: () => {
        reloads += 1;
      },
      defer: () => {
        waits += 1;
      },
    };
    reloadWhenServable(deps);
    await settle();
    expect(reloads).toBe(0);
    expect(waits).toBe(1);
  });
});
