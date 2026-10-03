// @vitest-environment node
import { afterEach, describe, expect, it } from "vitest";
import { mkdtemp, mkdir, readFile, readdir, rm, utimes, writeFile } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { ASSET_GRACE_MS, publishBuild } from "./publish-build.mjs";

const roots = [];
afterEach(async () => {
  for (const root of roots.splice(0)) {
    if (path.dirname(root) !== os.tmpdir() || !path.basename(root).startsWith("jarvis-publish-test-")) {
      throw new Error("Unexpected test directory");
    }
    await rm(root, { recursive: true, force: true });
  }
});

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "jarvis-publish-test-"));
  roots.push(root);
  return { root, live: path.join(root, "dist") };
}

async function bundle(root, name) {
  const directory = path.join(root, name);
  await mkdir(path.join(directory, "assets"), { recursive: true });
  await writeFile(path.join(directory, "assets", `entry-${name}.js`), `import('./lazy-${name}.js')`);
  await writeFile(path.join(directory, "assets", `lazy-${name}.js`), name);
  await writeFile(path.join(directory, "index.html"), `<script src="/assets/entry-${name}.js"></script>`);
  return directory;
}

describe("build publication", () => {
  it("keeps the open window's lazy chunks across rebuilds and expires them after retirement", async () => {
    const { root, live } = await fixture();
    const old = await bundle(root, "old00000");
    const next = await bundle(root, "new00000");
    await publishBuild(old, live, 0);
    await utimes(path.join(live, "assets/lazy-old00000.js"), new Date(0), new Date(0));
    await publishBuild(next, live, ASSET_GRACE_MS * 20);
    expect(await readFile(path.join(live, "assets/lazy-old00000.js"), "utf8")).toBe("old00000");
    expect(await readFile(path.join(live, "index.html"), "utf8")).toContain("new00000");
    await publishBuild(next, live, ASSET_GRACE_MS * 21);
    expect(await readdir(path.join(live, "assets"))).not.toContain("lazy-old00000.js");
    expect(await readFile(path.join(live, "assets/lazy-new00000.js"), "utf8")).toBe("new00000");
  });

  it("leaves the current build intact when the replacement is incomplete", async () => {
    const { root, live } = await fixture();
    await publishBuild(await bundle(root, "old00000"), live);
    const broken = await bundle(root, "bad00000");
    await rm(path.join(broken, "assets/entry-bad00000.js"));
    await expect(publishBuild(broken, live)).rejects.toThrow("missing assets");
    expect(await readFile(path.join(live, "index.html"), "utf8")).toContain("old00000");
    expect(await readFile(path.join(live, "assets/lazy-old00000.js"), "utf8")).toBe("old00000");
  });
});
