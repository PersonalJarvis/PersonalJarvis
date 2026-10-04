import { mkdirSync, mkdtempSync } from "node:fs";
import { rm } from "node:fs/promises";
import path from "node:path";
import type { Plugin } from "vite";
import { publishBuild } from "../scripts/publish-build.mjs";
import { checkRealtimeWorklet } from "../scripts/check-realtime-worklet.mjs";

/** Protect the live app even when a caller invokes Vite directly. */
export function safePublication(root: string): Plugin {
  let destination = "";
  let staging = "";
  return {
    name: "safe-frontend-publication",
    apply: "build",
    enforce: "pre",
    config(config) {
      destination = path.resolve(root, config.build?.outDir ?? "../dist");
      mkdirSync(path.dirname(destination), { recursive: true });
      staging = mkdtempSync(path.join(path.dirname(destination), ".frontend-build-"));
      return { build: { outDir: staging, emptyOutDir: true } };
    },
    writeBundle: {
      order: "post",
      sequential: true,
      async handler() {
        // Other writers (including public icon copying) must finish first.
        await checkRealtimeWorklet(path.join(staging, "assets"));
        await publishBuild(staging, destination);
      },
    },
    async closeBundle() {
      if (!staging) return;
      if (path.dirname(staging) !== path.dirname(destination) || !path.basename(staging).startsWith(".frontend-build-")) {
        throw new Error("Refusing to remove an unexpected staging directory");
      }
      await rm(staging, { recursive: true, force: true });
    },
  };
}
