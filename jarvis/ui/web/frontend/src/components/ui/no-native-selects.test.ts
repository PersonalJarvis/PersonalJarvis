import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import * as ts from "typescript";
import { describe, expect, it } from "vitest";

const SOURCE_ROOT = join(process.cwd(), "src");

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    if (!entry.name.endsWith(".tsx") || entry.name.includes(".test.")) return [];
    return [path];
  });
}

describe("dropdown design guard", () => {
  it("keeps operating-system select popups out of product code", () => {
    const violations: string[] = [];

    for (const path of sourceFiles(SOURCE_ROOT)) {
      const source = ts.createSourceFile(
        path,
        readFileSync(path, "utf8"),
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.TSX,
      );

      function visit(node: ts.Node): void {
        if (
          (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
          node.tagName.getText(source) === "select"
        ) {
          const position = source.getLineAndCharacterOfPosition(node.getStart(source));
          violations.push(
            `${relative(SOURCE_ROOT, path)}:${position.line + 1}`,
          );
        }
        ts.forEachChild(node, visit);
      }

      visit(source);
    }

    expect(
      violations,
      "Use BrandedSelect or Combobox so the opened menu follows the app theme.",
    ).toEqual([]);
  });
});
