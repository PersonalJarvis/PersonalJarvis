// @vitest-environment node
import { afterEach, expect, it } from "vitest";
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import os from "node:os";
import { build } from "vite";
import { NoticeCollector, thirdPartyNotices } from "./thirdPartyNotices";

const roots: string[] = [];
const license = readFileSync(new URL("../../../../../third_party/t3code/LICENSE", import.meta.url), "utf8").replace(/\r\n?/g, "\n");
const fontLicense = readFileSync(new URL("../node_modules/@fontsource-variable/inter/LICENSE", import.meta.url), "utf8").replace(/\r\n?/g, "\n");

afterEach(async () => {
  for (const root of roots.splice(0)) {
    if (path.dirname(root) !== await realpath(os.tmpdir()) || !path.basename(root).startsWith("jarvis-notice-test-")) {
      throw new Error("Unexpected test directory");
    }
    await rm(root, { recursive: true, force: true });
  }
});

async function fixture(): Promise<{ root: string; repository: string }> {
  const repository = await mkdtemp(path.join(await realpath(os.tmpdir()), "jarvis-notice-test-"));
  roots.push(repository);
  const root = path.join(repository, "jarvis/ui/web/frontend");
  await mkdir(path.join(root, "public"), { recursive: true });
  await writeFile(path.join(root, "public/THIRD_PARTY_NOTICES.txt"), "Copied material notices\r\n");
  return { root, repository };
}

async function packageFile(root: string, name: string, file: string, content: string | Uint8Array): Promise<string> {
  const target = path.join(root, "node_modules", name, file);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, content);
  return target;
}

async function makePackage(root: string, name: string, code = "export default 42;", terms: string | null = license): Promise<string> {
  await packageFile(root, name, "package.json", JSON.stringify({ name, version: "1.0.0", type: "module", main: "index.js", sideEffects: false }));
  if (terms) await packageFile(root, name, "LICENSE", terms);
  return packageFile(root, name, "index.js", code);
}

it("includes emitted modules, worker-only code, extracted font assets and copied icons, excluding unused dependencies", async () => {
  const { root } = await fixture();
  await makePackage(root, "runtime", "export const used = 42;");
  await makePackage(root, "unused");
  await makePackage(root, "build-tool");
  await makePackage(root, "worker-only");
  await makePackage(root, "material-icon-theme");
  await makePackage(root, "@fontsource-variable/inter", "export default 42;", fontLicense);
  await packageFile(root, "@fontsource-variable/inter", "font.css", '@font-face{font-family:fixture;src:url("./font.woff2")}');
  await packageFile(root, "@fontsource-variable/inter", "font.woff2", new Uint8Array(8192));
  await writeFile(path.join(root, "index.html"), '<script type="module" src="/main.js"></script>');
  await writeFile(path.join(root, "main.js"), [
    'import {used} from "runtime";',
    'import ignored from "unused";',
    'import "@fontsource-variable/inter/font.css";',
    'console.log(used);',
    'new Worker(new URL("./worker.js", import.meta.url), {type:"module"});',
  ].join("\n"));
  await writeFile(path.join(root, "worker.js"), 'import value from "worker-only"; postMessage(value);');
  const compile = async () => {
    const notices = thirdPartyNotices(root);
    await build({ configFile: false, root, logLevel: "silent", plugins: [notices.plugin],
      worker: { plugins: () => [notices.workerPlugin()] }, build: { outDir: "dist" } });
    return readFile(path.join(root, "dist/THIRD_PARTY_NOTICES.txt"), "utf8");
  };
  const result = await compile();
  for (const name of ["runtime", "worker-only", "material-icon-theme", "@fontsource-variable/inter"]) {
    expect(result).toContain(`${name}@1.0.0`);
  }
  expect(result).toContain(fontLicense.trim());
  expect(result).not.toContain("unused@1.0.0");
  expect(result).not.toContain("build-tool@1.0.0");
  expect(result).not.toContain(root);
  expect(result).not.toContain("\r");
  expect(await compile()).toBe(result);
});

it("retains package NOTICE and the separate licenses of emitted vendored submodules", async () => {
  const { root } = await fixture();
  await makePackage(root, "vendor", undefined, license.replace(/\n/g, "\r\n"));
  await packageFile(root, "vendor", "NOTICE", "Supplemental upstream attribution.\r\n".repeat(5));
  await packageFile(root, "vendor", "CopyrightNotice.txt", "Copyright (c) Additional notice owner.\r\n");
  const module = await packageFile(root, "vendor", "lib-vendor/d3/index.js", "export default 1;");
  const terms = license.replace("T3 Tools Inc.", "Vendored copyright owner");
  await packageFile(root, "vendor", "lib-vendor/d3/LICENSE", terms);
  const collector = new NoticeCollector(root);
  collector.addFile(module + "?commonjs-proxy");
  expect(collector.render()).toContain("Supplemental upstream attribution.");
  expect(collector.render()).toContain("Copyright (c) Additional notice owner.");
  expect(collector.render()).toContain("lib-vendor/d3/LICENSE");
  expect(collector.render()).toContain(terms.trim());
  expect(collector.render()).not.toContain("\r");
});

it("rejects missing or truncated license text instead of rendering a package SPDX identifier", async () => {
  const { root } = await fixture();
  const missing = await makePackage(root, "missing", undefined, null);
  await packageFile(root, "missing", "package.json", JSON.stringify({ name: "missing", version: "1.0.0", license: "MIT" }));
  expect(() => new NoticeCollector(root).addFile(missing)).toThrow("No full license document");
  await packageFile(root, "missing", "LICENSE", "MIT\nhttps://example.invalid/license");
  expect(() => new NoticeCollector(root).addFile(missing)).toThrow("Incomplete legal document");
});

it("requires both the exact version and the verified upstream text for a missing-license exception", async () => {
  const { root, repository } = await fixture();
  const module = await makePackage(root, "missing", undefined, null);
  await mkdir(path.join(repository, "third_party/missing"), { recursive: true });
  await writeFile(path.join(repository, "third_party/missing/LICENSE"), license.replace(/\n/g, "\r\n"));
  const manifest = path.join(repository, "third_party/frontend-notice-sources.json");
  const source = { version: "1.0.0", documents: [{ file: "third_party/missing/LICENSE", source: "https://example.invalid/pinned/LICENSE",
    sha256: createHash("sha256").update(license).digest("hex") }] };
  await writeFile(manifest, JSON.stringify({ packages: { missing: source } }));
  const collector = new NoticeCollector(root);
  collector.addFile(module);
  expect(collector.render()).toContain(license.trim());
  source.version = "2.0.0";
  await writeFile(manifest, JSON.stringify({ packages: { missing: source } }));
  expect(() => new NoticeCollector(root).addFile(module)).toThrow("Review notice source");
  source.version = "1.0.0";
  source.documents[0].sha256 = "wrong";
  await writeFile(manifest, JSON.stringify({ packages: { missing: source } }));
  expect(() => new NoticeCollector(root).addFile(module)).toThrow("Notice source digest changed");
  await writeFile(path.join(repository, "third_party/missing/LICENSE"), "MIT");
  source.documents[0].sha256 = createHash("sha256").update("MIT").digest("hex");
  await writeFile(manifest, JSON.stringify({ packages: { missing: source } }));
  expect(() => new NoticeCollector(root).addFile(module)).toThrow("Incomplete legal document");
  await writeFile(manifest, JSON.stringify({ packages: { missing: {
    version: "1.0.0", reason: "A long provenance explanation. ".repeat(8), documents: [],
  } } }));
  expect(() => new NoticeCollector(root).addFile(module)).toThrow("No full license document");
});

it("reports every missing emitted-package license in one failed build", async () => {
  const { root } = await fixture();
  await makePackage(root, "missing-one", undefined, null);
  await makePackage(root, "missing-two", "export default 2;", null);
  await makePackage(root, "material-icon-theme");
  await writeFile(path.join(root, "index.html"), '<script type="module" src="/main.js"></script>');
  await writeFile(path.join(root, "main.js"), 'import one from "missing-one"; import two from "missing-two"; console.log(one, two);');
  const notices = thirdPartyNotices(root);
  try {
    await build({ configFile: false, root, logLevel: "silent", plugins: [notices.plugin], build: { outDir: "dist" } });
    expect.fail("A build without shipped dependency licenses must fail");
  } catch (error) {
    expect(String(error)).toContain("No full license document for bundled missing-one@1.0.0");
    expect(String(error)).toContain("No full license document for bundled missing-two@1.0.0");
  }
});
