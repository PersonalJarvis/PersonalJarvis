import { bundle } from "@remotion/bundler";
import { openBrowser, renderMedia, renderStill, selectComposition } from "@remotion/renderer";
import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import "./styles.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const app = path.resolve(root, "../../jarvis/ui/web/frontend/src");
const modules = path.join(root, "node_modules");
const output = path.join(root, "out");
const settings = JSON.parse(await readFile(path.join(root, "settings.json"), "utf8"));
await mkdir(output, { recursive: true });
const serveUrl = await bundle({
  entryPoint: path.join(root, "src/index.tsx"),
  webpackOverride: config => ({ ...config, resolve: { ...config.resolve, alias: { ...config.resolve?.alias, "@app": app, "@": app, react: path.join(modules, "react"), "react-dom": path.join(modules, "react-dom") }, modules: [modules, "node_modules"] } }),
});
const requested = process.argv.find(arg => arg.startsWith("--id="))?.slice(5);
const browser = await openBrowser("chrome");
try {
for (const id of requested ? [requested] : settings.compositions.map(item => item.id)) {
  const composition = await selectComposition({ serveUrl, id, puppeteerInstance: browser });
  for (const frame of settings.stills) {
    await renderStill({ serveUrl, composition, puppeteerInstance: browser, output: path.join(output, `${id}-${frame}.png`), frame, imageFormat: "png" });
  }
  if (process.argv.includes("--stills")) continue;
  console.log(`Rendering ${id} (${settings.durationInFrames / settings.fps} seconds, ${settings.fps} fps, 4K master)`);
  let reported = -1;
  await renderMedia({ serveUrl, composition, puppeteerInstance: browser, codec: "h264", outputLocation: path.join(output, `${id}.mp4`), scale: settings.masterScale, crf: 14, imageFormat: "png", muted: true, colorSpace: "bt709", concurrency: 4, onProgress: ({ progress }) => { const bucket = Math.floor(progress * 10); if (bucket !== reported) { reported = bucket; console.log(`${id}: ${bucket * 10}%`); } } });
  console.log(`\nFinished ${id}`);
}
} finally {
  await browser.close({ silent: true });
}
