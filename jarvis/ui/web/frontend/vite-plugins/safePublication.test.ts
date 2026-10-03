// @vitest-environment node
import { afterEach, expect, it } from "vitest";
import { mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { build, type Plugin } from "vite";
import { safePublication } from "./safePublication";

const roots: string[] = [];
afterEach(async () => {
  for (const root of roots.splice(0)) {
    if (path.dirname(root) !== await realpath(os.tmpdir()) || !path.basename(root).startsWith("jarvis-vite-test-")) {
      throw new Error("Unexpected test directory");
    }
    await rm(root, { recursive: true, force: true });
  }
});

it("keeps the live entry and lazy chunks available through direct Vite builds and failed builds", async () => {
  const root = await mkdtemp(path.join(await realpath(os.tmpdir()), "jarvis-vite-test-"));
  roots.push(root);
  await mkdir(path.join(root, "public/assets"), { recursive: true });
  await writeFile(path.join(root, "public/assets/pcm-worklet-12345678.js"),
    ["pcm-capture", "pcm-playback", "pcm-level", "pcm-startup"].map((name) => `registerProcessor("${name}", class {});`).join("\n"));
  await writeFile(path.join(root, "index.html"), '<html><script type="module" src="/main.js"></script></html>');
  await writeFile(path.join(root, "main.js"), 'window.loadSection = () => import("./lazy.js");');
  await writeFile(path.join(root, "lazy.js"), 'export default "old section";');
  const compile = (probe?: Plugin) => build({
    configFile: false, root, logLevel: "silent",
    plugins: [safePublication(root), ...(probe ? [probe] : [])],
    // These are the destructive flags used by direct callers in the shared checkout.
    build: { outDir: "dist", emptyOutDir: true },
  });
  await compile();
  const oldHtml = await readFile(path.join(root, "dist/index.html"), "utf8");
  const oldLazy = (await readdir(path.join(root, "dist/assets"))).find((name) => name.startsWith("lazy-"))!;
  await writeFile(path.join(root, "lazy.js"), 'export default "new section";');
  let observed = false;
  await compile({
    name: "observe-live-build",
    async generateBundle() {
      expect(await readFile(path.join(root, "dist/index.html"), "utf8")).toBe(oldHtml);
      expect(await readFile(path.join(root, "dist/assets", oldLazy), "utf8")).toContain("old section");
      observed = true;
    },
  });
  expect(observed).toBe(true);
  const currentHtml = await readFile(path.join(root, "dist/index.html"), "utf8");
  expect(currentHtml).not.toBe(oldHtml);
  expect(await readFile(path.join(root, "dist/assets", oldLazy), "utf8")).toContain("old section");
  await writeFile(path.join(root, "main.js"), 'import "./missing-module.js";');
  await expect(compile()).rejects.toThrow();
  expect(await readFile(path.join(root, "dist/index.html"), "utf8")).toBe(currentHtml);
  expect((await readdir(root)).filter((name) => name.startsWith(".frontend-build-"))).toEqual([]);
});
