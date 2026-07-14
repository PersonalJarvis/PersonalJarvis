import { readFile, readdir } from "node:fs/promises";

const assetsUrl = new URL("../../dist/assets/", import.meta.url);
const assets = await readdir(assetsUrl);
const worklets = assets.filter(
  (name) => name.startsWith("pcm-worklet-") && name.endsWith(".js"),
);

if (worklets.length !== 1) {
  throw new Error(
    `Expected one compiled PCM AudioWorklet asset, found ${worklets.length}: ${worklets.join(", ")}`,
  );
}

const source = await readFile(new URL(worklets[0], assetsUrl), "utf8");
for (const processor of ["pcm-capture", "pcm-playback"]) {
  if (!source.includes(`registerProcessor("${processor}"`)) {
    throw new Error(`Compiled PCM AudioWorklet is missing ${processor}`);
  }
}

if (/\bdeclare\s+(?:class|const|function|let|var)\b/.test(source)) {
  throw new Error("Compiled PCM AudioWorklet still contains TypeScript declarations");
}
