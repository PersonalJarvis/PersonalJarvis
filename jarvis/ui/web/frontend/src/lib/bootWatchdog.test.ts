import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

/**
 * The watchdog that lives in `index.html`, not in this bundle.
 *
 * It is the only thing standing between a crash above every error boundary
 * and a window that shows nothing at all (seen 2026-08-20: a rebuild while
 * the desktop window was open left `#root` empty and the app dark until it
 * was killed). Because it has to survive a bundle that never runs, it cannot
 * be a module — so the test pulls the script out of the HTML and runs it, and
 * a change that breaks it fails here rather than on somebody's screen.
 */

/** The Vite entry document, found from wherever the runner was started. */
function indexHtmlPath(): string {
  let dir = process.cwd();
  for (;;) {
    const candidate = resolve(dir, "index.html");
    if (existsSync(candidate)) return candidate;
    const up = dirname(dir);
    if (up === dir)
      throw new Error("index.html not found above " + process.cwd());
    dir = up;
  }
}

const HTML = readFileSync(indexHtmlPath(), "utf8");

/** The one inline script that owns the reload guard. */
function watchdogSource(): string {
  const scripts = HTML.match(/<script>([\s\S]*?)<\/script>/g) ?? [];
  const found = scripts.find((s) => s.includes("jarvis:blank-reloaded"));
  if (!found)
    throw new Error("index.html no longer carries the blank-window watchdog");
  return found.replace(/^<script>/, "").replace(/<\/script>$/, "");
}

interface Run {
  reloads: () => number;
  store: Map<string, string>;
}

/** A served index.html the watchdog would consider safe to reload into. */
const WHOLE_BUILD =
  '<!doctype html><html><head><script type="module" src="/assets/index-abc.js">' +
  '</script></head><body><div id="root"></div></body></html>';

/** What the server hands out while a rebuild is between two states on disk. */
const HOLDING = '<html><div data-state="jarvis-build-holding"></div></html>';

/**
 * Run the watchdog against this document, with `location`, `sessionStorage`
 * and `fetch` shadowed by test doubles (jsdom's own `location.reload` cannot
 * be spied on).
 *
 * `fetch` is a double rather than absent because the watchdog no longer
 * reloads blindly: it asks the server whether a whole build is on disk first,
 * which is the difference between healing the window and blanking it for
 * twenty seconds. `served` is the entry document; `assetOk` decides whether
 * the hashed files it points at answer.
 */
function runWatchdog(
  options: { served?: string; assetOk?: boolean } = {},
): Run {
  let reloads = 0;
  const store = new Map<string, string>();
  const sessionStorageStub = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };
  const locationStub = { reload: () => void (reloads += 1) };
  const served = options.served ?? WHOLE_BUILD;
  const assetOk = options.assetOk ?? true;
  const fetchStub = (_url: string, init?: { method?: string }) =>
    Promise.resolve(
      init?.method === "HEAD"
        ? { ok: assetOk, text: () => Promise.resolve("") }
        : { ok: true, text: () => Promise.resolve(served) },
    );
  // eslint-disable-next-line no-new-func
  new Function("sessionStorage", "location", "fetch", watchdogSource())(
    sessionStorageStub,
    locationStub,
    fetchStub,
  );
  return { reloads: () => reloads, store };
}

function setRoot(inner: string) {
  document.body.innerHTML = `<div id="root">${inner}</div>`;
}

const SPLASH = '<div id="jarvis-boot-splash"><div class="ring"></div></div>';

describe("the blank-window watchdog in index.html", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  test("an app that mounts is left alone — and releases the guard for later", () => {
    setRoot(SPLASH);
    const run = runWatchdog();
    run.store.set("jarvis:blank-reloaded", "1");
    setRoot("<main>the deck</main>");
    vi.advanceTimersByTime(1_000);
    expect(run.reloads()).toBe(0);
    // A rebuild later in the same session gets its own reload again.
    expect(run.store.has("jarvis:blank-reloaded")).toBe(false);
  });

  test("a splash still spinning gets the long grace, then one reload", async () => {
    setRoot(SPLASH);
    const run = runWatchdog();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(run.reloads()).toBe(0);
    await vi.advanceTimersByTimeAsync(11_000);
    expect(run.reloads()).toBe(1);
  });

  test("it reloads into the REAL index.html, whose own script names the holding marker", async () => {
    // The regression of 2026-08-22/23: this very document spells out the
    // holding marker in the watchdog's source, so a test for the bare word
    // took every genuine build for the holding page and no window ever
    // reloaded again. The served document here is index.html itself, shaped
    // as the build ships it (a hashed entry asset in its head).
    const built = HTML.replace(
      "</head>",
      '<script type="module" crossorigin src="/assets/index-abc.js"></script></head>',
    );
    setRoot("");
    const run = runWatchdog({ served: built });
    await vi.advanceTimersByTimeAsync(3_000);
    expect(run.reloads()).toBe(1);
  });

  test("an emptied root is a crash, and does not wait twenty seconds for it", async () => {
    setRoot("");
    const run = runWatchdog();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(run.reloads()).toBe(1);
    expect(run.store.get("jarvis:blank-reloaded")).toBe("1");
  });

  test("it puts the splash back rather than waiting on an empty window", async () => {
    // While the server check runs — and it can run for as long as a rebuild
    // takes — #root is the one state with nothing on screen at all.
    setRoot("");
    runWatchdog({ served: HOLDING });
    await vi.advanceTimersByTimeAsync(3_000);
    const root = document.getElementById("root")!;
    expect(root.querySelector("#jarvis-boot-splash")).not.toBeNull();
    expect(root.textContent).toContain("Updating");
  });

  test("it does NOT reload into a build that is half-written", async () => {
    // The measured 1.8 s window: index.html is served, its entry bundle 404s.
    // Reloading here is the black window this whole guard exists to prevent.
    setRoot("");
    const run = runWatchdog({ assetOk: false });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(run.reloads()).toBe(0);
  });

  test("it does NOT reload while the server says a rebuild is in flight", async () => {
    setRoot("");
    const run = runWatchdog({ served: HOLDING });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(run.reloads()).toBe(0);
  });

  test("the second failure says what happened instead of reloading again", () => {
    setRoot("");
    const run = runWatchdog();
    run.store.set("jarvis:blank-reloaded", "1");
    vi.advanceTimersByTime(3_000);
    expect(run.reloads()).toBe(0);
    const root = document.getElementById("root")!;
    expect(root.textContent).toContain("The interface did not load.");
    expect(root.textContent).toContain("No reason was reported.");
    expect(root.querySelector("button")?.textContent).toBe("Reload");
  });

  test("it names the error it caught, so the window is never a mystery", () => {
    setRoot("");
    const run = runWatchdog();
    run.store.set("jarvis:blank-reloaded", "1");
    window.dispatchEvent(
      new ErrorEvent("error", {
        message: "Failed to fetch dynamically imported module",
      }),
    );
    vi.advanceTimersByTime(3_000);
    expect(run.reloads()).toBe(0);
    expect(document.getElementById("root")!.textContent).toContain(
      "Failed to fetch dynamically imported module",
    );
  });

  test("it keeps watching after the app is up, and catches a LATER crash", async () => {
    // The section-switch failure, in order: the app paints, the user works for
    // a while, then a lazy chunk throws above every error boundary and empties
    // #root. The watchdog used to clearInterval() on the first healthy poll,
    // so from that moment nothing was watching and the window just went dark.
    setRoot(SPLASH);
    const run = runWatchdog();
    setRoot("<main>the deck</main>");
    vi.advanceTimersByTime(60_000);
    expect(run.reloads()).toBe(0);

    setRoot("");
    await vi.advanceTimersByTimeAsync(3_000);
    expect(run.reloads()).toBe(1);
  });

  test("a healthy app that empties later still gets its full grace period", async () => {
    // The grace must be measured from when the window went blank, not from
    // page load — otherwise a stopwatch running since boot condemns the very
    // first poll after a crash and reloads on a momentary gap.
    setRoot(SPLASH);
    const run = runWatchdog();
    setRoot("<main>the deck</main>");
    vi.advanceTimersByTime(60_000);

    setRoot("");
    await vi.advanceTimersByTimeAsync(1_000);
    expect(run.reloads()).toBe(0);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(run.reloads()).toBe(1);
  });

  test("a later crash that has already spent its reload explains itself", () => {
    setRoot(SPLASH);
    const run = runWatchdog();
    setRoot("<main>the deck</main>");
    vi.advanceTimersByTime(1_000);
    run.store.set("jarvis:blank-reloaded", "1");

    setRoot("");
    vi.advanceTimersByTime(3_000);
    expect(run.reloads()).toBe(0);
    expect(document.getElementById("root")!.textContent).toContain(
      "The interface did not load.",
    );
  });

  test("the reload button clears the guard, so the fresh page may heal itself", async () => {
    setRoot("");
    const run = runWatchdog();
    run.store.set("jarvis:blank-reloaded", "1");
    await vi.advanceTimersByTimeAsync(3_000);
    document.getElementById("root")!.querySelector("button")!.click();
    await vi.advanceTimersByTimeAsync(0);
    expect(run.reloads()).toBe(1);
    expect(run.store.has("jarvis:blank-reloaded")).toBe(false);
  });
});
