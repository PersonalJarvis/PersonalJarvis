import { readFileSync } from "node:fs";
import { join } from "node:path";
import * as ts from "typescript";
import { describe, expect, it } from "vitest";

/**
 * The shell must not build a stacking context around the section stage.
 *
 * A full-screen overlay inside a view (the wallpaper preview, a view's own
 * dialog and its scrim) is `position: fixed` and climbs to z-40/z-50/z-70 to
 * cover the app. All of that is void the moment an ANCESTOR between it and the
 * shell root carries a z-index of its own: the overlay is then trapped at the
 * ancestor's level, and the nav column beside it (z-20) paints straight over
 * it — which is how the sidebar logo, the assistant name and the voice state
 * ended up printed on top of the wallpaper preview's own header.
 *
 * The chain guarded here is the whole path from the shell root down to a
 * section: <main> in App.tsx, the SectionStage it wraps the section in, and
 * MainView's per-section wrappers.
 */

const SOURCE_ROOT = join(process.cwd(), "src");

/** A Tailwind z-index utility that would open a stacking context (z-0 does). */
const STACKING_Z = /(?:^|[\s"'`])z-(?:\d+|\[[^\]]*\])/;

function read(relativePath: string): string {
  return readFileSync(join(SOURCE_ROOT, relativePath), "utf8");
}

/**
 * Every className text a JSX element in `path` is given, tag by tag.
 *
 * `within` narrows the walk to one function — App.tsx also renders layers that
 * live BESIDE the section stage rather than above it, and their z-index is
 * none of this guard's business.
 */
function classNamesByTag(
  path: string,
  within?: string,
): Array<{ tag: string; line: number; text: string }> {
  const source = ts.createSourceFile(
    path,
    read(path),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const found: Array<{ tag: string; line: number; text: string }> = [];

  function visit(node: ts.Node): void {
    if (
      within &&
      ts.isFunctionDeclaration(node) &&
      node.name?.getText(source) !== within
    ) {
      return;
    }
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      for (const attribute of node.attributes.properties) {
        if (!ts.isJsxAttribute(attribute)) continue;
        if (attribute.name.getText(source) !== "className") continue;
        found.push({
          tag: node.tagName.getText(source),
          line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1,
          text: attribute.initializer?.getText(source) ?? "",
        });
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(source);
  return found;
}

describe("overlay stacking guard", () => {
  it("keeps the stage column out of its own stacking context", () => {
    const offenders = classNamesByTag("App.tsx")
      .filter((entry) => entry.tag === "main" && STACKING_Z.test(entry.text))
      .map((entry) => `App.tsx:${entry.line}`);

    expect(
      offenders,
      "`relative` alone: a z-index on <main> traps every full-screen overlay a " +
        "section renders under the nav column (z-20).",
    ).toEqual([]);
  });

  it("keeps the section wrappers out of their own stacking context", () => {
    const onThePath: Array<[string, string | undefined]> = [
      ["App.tsx", "SectionStage"],
      ["components/layout/MainView.tsx", undefined],
    ];
    const offenders: string[] = [];

    for (const [path, within] of onThePath) {
      for (const entry of classNamesByTag(path, within)) {
        if (!STACKING_Z.test(entry.text)) continue;
        offenders.push(`${path}:${entry.line} <${entry.tag}>`);
      }
    }

    expect(
      offenders,
      "Anything on the path from the shell root to a section must stay " +
        "z-index-free, or the section's overlays cannot reach the app's " +
        "dialog levels.",
    ).toEqual([]);
  });
});
