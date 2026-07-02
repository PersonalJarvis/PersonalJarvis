// Bundle once, render many stills for the render-check loop.
// Usage: node scripts/check_stills.mjs "ReadmeFilm" 43 150 300 640 720 830 1050 1140 1220 1300 1390 1600
import path from "node:path";
import { fileURLToPath } from "node:url";
import { bundle } from "@remotion/bundler";
import { selectComposition, renderStill } from "@remotion/renderer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

const compId = process.argv[2] || "ReadmeFilm";
const frames = process.argv.slice(3).map(Number);

const serveUrl = await bundle({ entryPoint: path.resolve(ROOT, "src/index.ts") });
const composition = await selectComposition({ serveUrl, id: compId });
console.log(`composition ${compId}: ${composition.durationInFrames}f ${composition.width}x${composition.height}`);

for (const f of frames) {
  const out = path.resolve(ROOT, `out/checks/${compId}-${String(f).padStart(4, "0")}.png`);
  await renderStill({ composition, serveUrl, output: out, frame: f, scale: 1.5 });
  console.log("wrote", path.relative(ROOT, out));
}
process.exit(0);
