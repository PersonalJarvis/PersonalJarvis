// Capture a real view of the running desktop app to a PNG on disk.
//
// The app (http://127.0.0.1:47821) is a single-page React app with NO per-view
// URL — the active view lives in a Zustand store and is changed by clicking a
// sidebar item. So we drive a headless Chrome over the DevTools Protocol
// (Node 24 has a global WebSocket): launch → wait for the sidebar to render →
// click the named sidebar label → (optionally) scroll a target into view →
// screenshot. No external deps.
//
// Usage:  node scripts/capture_app.mjs "<sidebar label>" <out.png> ["scroll text"]
//   e.g.  node scripts/capture_app.mjs "Settings" public/shot-wake.png "Wake Word"
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const LABEL = process.argv[2] ?? "Outputs";
const OUT = process.argv[3] ?? "public/shot.png";
const SCROLL_TEXT = process.argv[4] ?? "";
const APP_URL = process.argv[5] ?? "http://127.0.0.1:47821";
const PORT = 9223;
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

async function main() {
  const profile = mkdtempSync(join(tmpdir(), "jarvis-cap-"));
  const chrome = spawn(CHROME, [
    "--headless=new",
    `--remote-debugging-port=${PORT}`,
    "--window-size=1600,1000",
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

    // poll until the sidebar has rendered (the SPA + its websocket can take a
    // while to warm — a blank capture means we screenshotted too early)
    let ready = false;
    for (let i = 0; i < 60; i++) {
      const n = await evalJs(call, "document.querySelectorAll('button, a, [role=button]').length");
      if (n > 8) {
        ready = true;
        break;
      }
      await sleep(500);
    }
    if (!ready) console.warn("sidebar never reached >8 controls; capturing anyway");
    await sleep(1200);

    // click the sidebar item whose trimmed text matches LABEL (retry a few times)
    let clicked = "";
    for (let i = 0; i < 6; i++) {
      clicked = await evalJs(
        call,
        `(() => {
          const els = [...document.querySelectorAll('button, a, [role=button]')];
          const el = els.find(e => (e.textContent || '').trim() === ${JSON.stringify(LABEL)});
          if (el) { el.click(); return 'clicked'; }
          return 'notfound';
        })()`,
      );
      if (clicked === "clicked") break;
      await sleep(800);
    }
    console.log("click:", clicked);
    await sleep(2600); // let the view render + fetch its data

    if (SCROLL_TEXT) {
      const scrolled = await evalJs(
        call,
        `(() => {
          const want = ${JSON.stringify(SCROLL_TEXT.toLowerCase())};
          const els = [...document.querySelectorAll('h1,h2,h3,h4,label,span,div')];
          const el = els.find(e => (e.textContent || '').trim().toLowerCase() === want)
                  || els.find(e => (e.textContent || '').toLowerCase().includes(want));
          if (el) { el.scrollIntoView({ block: 'center' }); return 'scrolled'; }
          return 'noscrolltarget';
        })()`,
      );
      console.log("scroll:", scrolled);
      await sleep(1200);
    }

    const shot = await call("Page.captureScreenshot", { format: "png" });
    writeFileSync(OUT, Buffer.from(shot.data, "base64"));
    console.log("wrote", OUT);
    ws.close();
  } finally {
    chrome.kill();
  }
}

main().catch((e) => {
  console.error("capture failed:", e.message);
  process.exit(1);
});
