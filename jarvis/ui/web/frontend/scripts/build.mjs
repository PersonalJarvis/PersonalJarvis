import { mkdtemp, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { build } from "vite";
import { publishBuild } from "./publish-build.mjs";
import { checkRealtimeWorklet } from "./check-realtime-worklet.mjs";

const frontend = fileURLToPath(new URL("..", import.meta.url));
const web = path.dirname(frontend);
const staging = await mkdtemp(path.join(web, ".frontend-build-"));
try {
  await build({ root: frontend, build: { outDir: staging, emptyOutDir: true } });
  await checkRealtimeWorklet(path.join(staging, "assets"));
  await publishBuild(staging, path.join(web, "dist"));
} finally {
  // Only the directory created by this invocation is ever removed.
  if (path.dirname(staging) !== web || !path.basename(staging).startsWith(".frontend-build-")) {
    throw new Error("Refusing to remove an unexpected staging directory");
  }
  await rm(staging, { recursive: true, force: true });
}
