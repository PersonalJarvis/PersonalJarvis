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

    // Real mouse-event click on the leaf element whose exact text == target
    // (React tab/tree handlers often need pointer/mouse events, not .click()).
    const realClick = (target) =>
      evalJs(
        call,
        `(() => {
          const t = ${JSON.stringify(target)};
          const norm = (e) => (e.textContent || '').replace(/\\s+/g,' ').trim();
          const all = [...document.querySelectorAll('*')];
          const el = all.filter(e => norm(e) === t && e.children.length <= 2).pop();
          if (!el) return 'notfound';
          const r = el.getBoundingClientRect();
          const opt = { bubbles: true, cancelable: true, clientX: r.x + r.width/2, clientY: r.y + r.height/2, view: window };
          for (const type of ['pointerdown','mousedown','pointerup','mouseup','click'])
            el.dispatchEvent(new MouseEvent(type, opt));
          return 'clicked@' + Math.round(r.x) + ',' + Math.round(r.y);
        })()`,
      );

    // diagnostics: what tabs / tree rows exist?
    console.log("tabs:", await visibleText(call, "[role=tab], button"));
    // Select a real vault page (tree-only names), then open the Page tab.
    for (const name of ["bridgemind.md", "obsidian.md", "claude-opus.md", "lena.md"]) {
      const r = await realClick(name);
      console.log("tree file", name, "->", r);
      if (r.startsWith("clicked")) break;
    }
    await sleep(1400);
    console.log("Page tab:", await realClick("Page"));
    await sleep(1800);
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
