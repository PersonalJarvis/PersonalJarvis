import { bundle } from "@remotion/bundler";
import { renderMedia, renderStill, selectComposition } from "@remotion/renderer";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import "./styles.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const app = path.resolve(root, "../../jarvis/ui/web/frontend/src");
const modules = path.join(root, "node_modules");
const output = path.join(root, "out");
await mkdir(output, { recursive: true });
const serveUrl = await bundle({
  entryPoint: path.join(root, "src/index.tsx"),
  webpackOverride: config => ({ ...config, resolve: { ...config.resolve, alias: { ...config.resolve?.alias, "@app": app, "@": app, react: path.join(modules, "react"), "react-dom": path.join(modules, "react-dom") }, modules: [modules, "node_modules"] } }),
});
const requested = process.argv.find(arg => arg.startsWith("--id="))?.slice(5);
for (const id of requested ? [requested] : ["JarvisOrchestrator", "JarvisAgents", "UltraSwarm"]) {
  const composition = await selectComposition({ serveUrl, id });
  for (const frame of [0, 90, 180, 270, 360, 449]) {
    await renderStill({ serveUrl, composition, output: path.join(output, `${id}-${frame}.png`), frame, imageFormat: "png" });
  }
  if (process.argv.includes("--stills")) continue;
  console.log(`Rendering ${id} (15 seconds, 4K master)`);
  await renderMedia({ serveUrl, composition, codec: "h264", outputLocation: path.join(output, `${id}.mp4`), scale: 2.4, crf: 16, colorSpace: "bt709", concurrency: 4, onProgress: ({ progress }) => { if (Math.round(progress * 100) % 20 === 0) process.stdout.write("."); } });
  console.log(`\nFinished ${id}`);
}
