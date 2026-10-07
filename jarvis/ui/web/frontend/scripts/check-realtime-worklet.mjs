import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export async function checkRealtimeWorklet(assetsDirectory) {
  const assets = await readdir(assetsDirectory);
  const worklets = assets.filter(
    (name) => name.startsWith("pcm-worklet-") && name.endsWith(".js"),
  );

  if (worklets.length !== 1) {
    throw new Error(
      `Expected one compiled PCM AudioWorklet asset, found ${worklets.length}: ${worklets.join(", ")}`,
    );
  }

  const source = await readFile(path.join(assetsDirectory, worklets[0]), "utf8");
  for (const processor of ["pcm-capture", "pcm-playback", "pcm-level", "pcm-startup"]) {
    if (!source.includes(`registerProcessor("${processor}"`)) {
      throw new Error(`Compiled PCM AudioWorklet is missing ${processor}`);
    }
  }

  if (/\bdeclare\s+(?:class|const|function|let|var)\b/.test(source)) {
    throw new Error("Compiled PCM AudioWorklet still contains TypeScript declarations");
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await checkRealtimeWorklet(fileURLToPath(new URL("../../dist/assets/", import.meta.url)));
}
