import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const distPath = fileURLToPath(new URL("../../dist/", import.meta.url));
const textExtensions = new Set([".css", ".html", ".js", ".json", ".map"]);

function normalizeDirectory(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      normalizeDirectory(path);
      continue;
    }
    if (!entry.isFile() || !textExtensions.has(extname(entry.name))) continue;

    const content = readFileSync(path, "utf8");
    const normalized = content
      .replace(/\r+\n/g, "\n")
      .replace(/\r/g, "\n")
      .replace(/^[\t ]+$/gm, "")
      .replace(/\n*$/, "\n");

    if (normalized !== content) {
      writeFileSync(path, normalized, "utf8");
    }
  }
}

normalizeDirectory(distPath);
