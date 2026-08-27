import { describe, expect, it } from "vitest";

import { codeDigest } from "@/lib/codeDigest";

describe("codeDigest", () => {
  it("reads a Python module's docstring and its top-level definitions", () => {
    const text = [
      "#!/usr/bin/env python3",
      '"""Gmail Deep Clean CLI utility.',
      "",
      "Scans the connected Gmail account and proposes deletions.",
      '"""',
      "from __future__ import annotations",
      "",
      "import argparse",
      "",
      "class Cleaner:",
      "    def run(self) -> None:",
      "        pass",
      "",
      "async def async_main(args: argparse.Namespace) -> int:",
      "    return 0",
      "",
      "def _private() -> None:",
      "    pass",
      "",
      "def main() -> int:",
      "    return 0",
      "",
    ].join("\n");
    const digest = codeDigest("scripts/gmail_deep_clean.py", text);
    expect(digest.language).toBe("python");
    expect(digest.lines).toBe(21);
    expect(digest.description).toBe("Gmail Deep Clean CLI utility.");
    expect(digest.symbols).toEqual([
      { kind: "class", name: "Cleaner" },
      { kind: "function", name: "async_main" },
      { kind: "function", name: "main" },
    ]);
    expect(digest.json).toBeNull();
    expect(digest.diff).toBeNull();
  });

  it("reads a docstring that opens after leading comments and closes on one line", () => {
    const text = '# -*- coding: utf-8 -*-\n"""One-liner."""\n\ndef go():\n    pass\n';
    expect(codeDigest("x.py", text).description).toBe("One-liner.");
  });

  it("reads a TypeScript module's leading block comment and exported definitions", () => {
    const text = [
      "/**",
      " * The rail — every run, newest first.",
      " *",
      " * Second paragraph, not part of the summary.",
      " */",
      'import { x } from "y";',
      "",
      "export const LIMIT = 12;",
      "export function toRows(a: string): string[] { return [a]; }",
      "const helper = async (a: number): Promise<void> => {};",
      "export default class Rail {}",
      "",
    ].join("\n");
    const digest = codeDigest("src/rail.ts", text);
    expect(digest.description).toBe("The rail — every run, newest first.");
    expect(digest.symbols).toEqual([
      { kind: "const", name: "LIMIT" },
      { kind: "function", name: "toRows" },
      { kind: "function", name: "helper" },
      { kind: "class", name: "Rail" },
    ]);
  });

  it("reads a shell script's comment header and its functions", () => {
    const text = "#!/usr/bin/env bash\n# Rebuild the bundle.\n# Then restart.\n\nset -e\nbuild() {\n  npm run build\n}\nfunction restart {\n  :\n}\n";
    const digest = codeDigest("rebuild.sh", text);
    expect(digest.description).toBe("Rebuild the bundle. Then restart.");
    expect(digest.symbols.map((s) => s.name)).toEqual(["build", "restart"]);
  });

  it("reads a PowerShell help block's synopsis", () => {
    const text = "<#\n.SYNOPSIS\nInstalls the thing.\n.DESCRIPTION\nLong text.\n#>\nfunction Install-Thing {\n}\n";
    const digest = codeDigest("install.ps1", text);
    expect(digest.description).toBe("Installs the thing.");
    expect(digest.symbols).toEqual([{ kind: "function", name: "Install-Thing" }]);
  });

  it("describes a JSON document by its shape", () => {
    expect(codeDigest("out.json", '{"a": 1, "b": [1, 2], "c": null}').json).toEqual({
      kind: "object",
      keys: ["a", "b", "c"],
      count: 3,
    });
    expect(codeDigest("rows.json", "[1, 2, 3]").json).toEqual({ kind: "array", count: 3 });
    expect(codeDigest("events.jsonl", '{"a":1}\n{"a":2}\n\n').json).toEqual({
      kind: "array",
      count: 2,
    });
    expect(codeDigest("broken.json", "{not json").json).toBeNull();
  });

  it("lists a patch's files with their added and removed lines", () => {
    const text = [
      "From abc Mon Sep 17 00:00:00 2001",
      "Subject: [PATCH 1/2] fix(rail): keep the newest run on top",
      "",
      "diff --git a/src/rail.ts b/src/rail.ts",
      "--- a/src/rail.ts",
      "+++ b/src/rail.ts",
      "@@ -1,3 +1,4 @@",
      " keep",
      "-old",
      "+new",
      "+newer",
      "diff --git a/README.md b/README.md",
      "--- a/README.md",
      "+++ b/README.md",
      "@@ -1 +1 @@",
      "-a",
      "+b",
      "",
    ].join("\n");
    const digest = codeDigest("0001-fix.patch", text);
    expect(digest.description).toBe("fix(rail): keep the newest run on top");
    expect(digest.diff).toEqual([
      { path: "src/rail.ts", added: 2, removed: 1 },
      { path: "README.md", added: 1, removed: 1 },
    ]);
  });

  it("reads a plain unified diff without git headers", () => {
    const text = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n";
    expect(codeDigest("change.diff", text).diff).toEqual([{ path: "x.py", added: 1, removed: 1 }]);
  });

  it("yields the line count alone for an unknown language", () => {
    const digest = codeDigest("notes.txt", "a\nb\nc");
    expect(digest).toEqual({
      language: "txt",
      lines: 3,
      description: null,
      symbols: [],
      json: null,
      diff: null,
    });
    expect(codeDigest("empty.txt", "").lines).toBe(0);
  });

  it("caps a long description with an ellipsis", () => {
    const long = "x".repeat(600);
    const description = codeDigest("a.py", `"""${long}"""`).description ?? "";
    expect(description.length).toBeLessThanOrEqual(320);
    expect(description.endsWith("…")).toBe(true);
  });
});
