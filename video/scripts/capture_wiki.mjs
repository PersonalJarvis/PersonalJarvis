// Capture real Wiki views of the running desktop app to PNGs on disk.
//
// Same DevTools-over-headless-Chrome approach as capture_app.mjs, but tailored
// to the Wiki view: open the app → click the "Wiki" sidebar item → optionally
// click a tab ("Memory Map" | "Page") and/or a vault-tree page entry by text →
// screenshot at 2x device scale for crisp text.
//
// Usage:
//   node scripts/capture_wiki.mjs <out.png> [tabText] [pageText]
//   e.g. node scripts/capture_wiki.mjs public/shot-wiki-map.png  "Memory Map"
//        node scripts/capture_wiki.mjs public/shot-wiki-page.png "Page" "ruben.md"
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const MAP_OUT = process.argv[2] ?? "public/shot-wiki-map.png";
const PAGE_OUT = process.argv[3] ?? "public/shot-wiki-page.png";
const APP_URL = process.argv[4] ?? "http://127.0.0.1:47821";
const PORT = 9224;
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeCaller(ws) {
  let id = 0;
  return (method, params = {}) =>
    new Promise((resolve, reject) => {
      const myId = ++id;
      const onMsg = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.id === myId) {
          ws.removeEventListener("message", onMsg);
          m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
        }
      };
      ws.addEventListener("message", onMsg);
      ws.send(JSON.stringify({ id: myId, method, params }));
    });
}

const evalJs = (call, expr) =>
  call("Runtime.evaluate", { expression: expr, returnByValue: true }).then((r) => r.result.value);

// Click a clickable element whose OWN text (leaf, not a big container) equals or
// starts with `text`. Prefers the smallest matching clickable node.
const clickByText = (call, text) =>
  evalJs(
    call,
    `(() => {
      const want = ${JSON.stringify(text)};
      const clickable = [...document.querySelectorAll('button, a, [role=button], [role=tab], [role=menuitem]')];
      const norm = (e) => (e.textContent || '').replace(/\\s+/g, ' ').trim();
      let el = clickable.find(e => norm(e) === want)
            || clickable.find(e => norm(e).startsWith(want))
            || clickable.find(e => norm(e).includes(want));
      if (!el) {
        // fall back to any leaf element with the text, click nearest clickable ancestor
        const leaves = [...document.querySelectorAll('span,div,li,p')].filter(e => norm(e) === want);
        if (leaves[0]) el = leaves[0].closest('button,a,[role=button],[role=tab],li') || leaves[0];
      }
      if (el) { el.click(); return norm(el).slice(0,40) || 'clicked'; }
      return 'NOTFOUND:' + want;
    })()`,
  );

const visibleText = (call, sel) =>
  evalJs(
    call,
    `[...document.querySelectorAll(${JSON.stringify(sel)})].map(e => (e.textContent||'').replace(/\\s+/g,' ').trim()).filter(Boolean).slice(0,40)`,
  );

async function shoot(call, out) {
  const shot = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  writeFileSync(out, Buffer.from(shot.data, "base64"));
  console.log("wrote", out);
}

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "jarvis-wiki-cap-"));
  const chrome = spawn(CHROME, [
    "--headless=new",
    `--remote-debugging-port=${PORT}`,
    "--window-size=1728,1080",
    "--force-device-scale-factor=2",
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    `--user-data-dir=${profile}`,
    APP_URL,
  ]);
  chrome.on("error", (e) => console.error("chrome spawn error", e));

  try {
    let target;
    for (let i = 0; i < 60; i++) {
      await sleep(300);
      try {
        const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
        target = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
        if (target) break;
      } catch {
        /* not up yet */
      }
    }
    if (!target) throw new Error("no devtools page target");

    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener("open", res, { once: true });
      ws.addEventListener("error", rej, { once: true });
    });
    const call = makeCaller(ws);
    await call("Page.enable");
    await call("Runtime.enable");

    // wait until the sidebar rendered
    for (let i = 0; i < 60; i++) {
      const n = await evalJs(call, "document.querySelectorAll('button, a, [role=button]').length");
      if (n > 8) break;
      await sleep(500);
    }
    await sleep(1200);

    // open the Wiki view (retry until a Wiki-only marker appears)
    for (let i = 0; i < 8; i++) {
      console.log("wiki nav click:", await clickByText(call, "Wiki"));
      await sleep(1500);
      const markers = await visibleText(call, "*");
      if (markers.some((t) => /wikilinks|Memory Map|VAULT/i.test(t))) break;
    }
    await sleep(2600); // let the graph / vault load
    await shoot(call, MAP_OUT);

    // Open a real vault page: click a tree file (candidates that live ONLY in the
    // vault tree, never in the sidebar nav), then ensure the "Page" tab is active.
    const clickTreeFile = (name) =>
      evalJs(
        call,
        `(() => {
          const want = ${JSON.stringify("")} || 0;
          const target = ${JSON.stringify(name)};
          const norm = (e) => (e.textContent || '').replace(/\\s+/g,' ').trim();
          // leaf elements only (no big containers) whose exact text is the filename
          const all = [...document.querySelectorAll('div,span,p,button,a,li,[role=treeitem]')];
          const leaf = all.filter(e => norm(e) === target && e.children.length <= 1);
          const el = leaf[0];
          if (el) { (el.closest('button,a,[role=button],[role=treeitem],li') || el).click(); return 'clicked'; }
          return 'notfound';
        })()`,
      );

    const candidates = ["bridgemind.md", "obsidian.md", "claude-opus.md", "ruben.md", "lena.md"];
    let opened = "";
    for (const name of candidates) {
      const r = await clickTreeFile(name);
      console.log("tree file", name, "->", r);
      if (r === "clicked") {
        opened = name;
        await sleep(1400);
        break;
      }
    }
    console.log("Page tab:", await clickByText(call, "Page"));
    await sleep(1600);
    if (!opened) console.warn("no vault tree file matched; page shot may show the map");
    await shoot(call, PAGE_OUT);
    ws.close();
  } finally {
    chrome.kill();
  }
}

main().catch((e) => {
  console.error("capture failed:", e.message);
  process.exit(1);
});
