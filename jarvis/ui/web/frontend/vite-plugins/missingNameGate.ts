/**
 * Refuse to ship a bundle that uses a name nothing defines.
 *
 * `npm run build` type-checks first, but the working tree is shared between
 * agent sessions and one sibling's half-written test file fails `tsc -b` for
 * everyone — so a session that has to ship a fix runs `vite build` on its own.
 * Rollup treats an identifier without a declaration as a browser global and
 * emits it verbatim: a component whose import line had not been written yet
 * became `PANE_ACTIVITY_EVENT is not defined` in the desktop app, whose window
 * reloads itself onto every fresh bundle, and the Agentic IDE crashed for the
 * maintainer (2026-08-27, BUG-197). Most type errors are noise at runtime; a
 * name TypeScript cannot find in a VALUE position is a guaranteed crash. So
 * this gate runs the type check beside the bundling and fails the build on
 * exactly those diagnostics, letting foreign type mismatches — and a missing
 * type-only name, which the bundle erases — through the way a bare build
 * always did.
 *
 * The check starts with the build and is awaited before Vite empties the
 * output directory, so a refused build leaves the previous bundle in place
 * for the app that is serving it. Under `npm run build` the whole tree has
 * just passed `tsc -b`, and the gate skips its redundant second run.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import type { Plugin } from "vite";

/** One tsc diagnostic: `src/x.ts(12,3): error TS2304: Cannot find name 'Y'.` */
const TSC_DIAGNOSTIC = /^(.+?)\((\d+),(\d+)\): error (TS\d+): (.*)$/;

/**
 * The diagnostics that MAY be a ReferenceError waiting to happen. TS2304 and
 * TS2552 are both "Cannot find name": an identifier nothing declares or
 * imports. Whether it survives into JavaScript depends on where it stands.
 */
const CANNOT_FIND_NAME: ReadonlySet<string> = new Set(["TS2304", "TS2552"]);

/** Where tsc found a name it could not resolve. */
export interface MissingName {
  /** As tsc printed it: relative to the directory tsc ran in. */
  file: string;
  /** 1-based. */
  line: number;
  /** 1-based. */
  column: number;
  message: string;
}

/** The "Cannot find name" diagnostics in tsc's output, in order. */
export function missingNames(tscOutput: string): MissingName[] {
  const hits: MissingName[] = [];
  for (const raw of tscOutput.split(/\r?\n/)) {
    const match = TSC_DIAGNOSTIC.exec(raw);
    if (match === null || !CANNOT_FIND_NAME.has(match[4])) continue;
    hits.push({
      file: match[1],
      line: Number(match[2]),
      column: Number(match[3]),
      message: match[5],
    });
  }
  return hits;
}

/** The deepest node whose span covers `position`, or the file itself. */
function nodeAt(file: ts.SourceFile, position: number): ts.Node {
  let node: ts.Node = file;
  for (;;) {
    const child = ts.forEachChild(node, (candidate) =>
      candidate.getStart(file) <= position && position < candidate.getEnd()
        ? candidate
        : undefined,
    );
    if (child === undefined) return node;
    node = child;
  }
}

/**
 * Does the name at (line, column) stand where JavaScript will still read it?
 *
 * A name inside a type node — an annotation, a generic argument, an
 * interface body, `typeof X` in a type — is erased by the compiler and can be
 * missing without consequence. `class A extends Missing` is the one heritage
 * clause that runs; `implements` and an interface's `extends` do not.
 */
export function isRuntimePosition(
  fileName: string,
  sourceText: string,
  line: number,
  column: number,
): boolean {
  const file = ts.createSourceFile(fileName, sourceText, ts.ScriptTarget.Latest, true);
  let position: number;
  try {
    position = file.getPositionOfLineAndCharacter(line - 1, column - 1);
  } catch {
    // The file changed under tsc (a sibling session is mid-edit) and the
    // reported line is gone. A name we cannot place is treated as live: the
    // gate errs toward refusing a build, never toward shipping one.
    return true;
  }
  for (let node: ts.Node | undefined = nodeAt(file, position); node; node = node.parent) {
    if (ts.isExpressionWithTypeArguments(node) && ts.isHeritageClause(node.parent)) {
      const clause = node.parent;
      return clause.token === ts.SyntaxKind.ExtendsKeyword && ts.isClassLike(clause.parent);
    }
    if (ts.isTypeNode(node)) return false;
    if (
      ts.isTypeAliasDeclaration(node) ||
      ts.isInterfaceDeclaration(node) ||
      ts.isTypeParameterDeclaration(node)
    ) {
      return false;
    }
  }
  return true;
}

/** The subset of `names` the emitted JavaScript would still reference. */
export function runtimeMissingNames(names: MissingName[], root: string): MissingName[] {
  return names.filter((name) => {
    const absolute = path.resolve(root, name.file);
    let text: string;
    try {
      text = fs.readFileSync(absolute, "utf8");
    } catch {
      // Gone between tsc and now — a sibling deleted it. Nothing to ship, so
      // nothing to refuse.
      return false;
    }
    return isRuntimePosition(absolute, text, name.line, name.column);
  });
}

function runTsc(root: string): Promise<string> {
  return new Promise((resolve) => {
    const tsc = path.resolve(root, "node_modules/typescript/lib/tsc.js");
    const child = spawn(process.execPath, [tsc, "-b", "--pretty", "false"], {
      cwd: root,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const chunks: Buffer[] = [];
    child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => chunks.push(chunk));
    child.on("error", (error) => {
      // A type check that cannot start must not pass for one that passed:
      // surface it as a diagnostic the gate reports, never as silence.
      resolve(`(0,0): error TS2304: Cannot find name check: ${error.message}`);
    });
    child.on("close", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });
}

export function missingNameGate(root: string): Plugin {
  let pending: Promise<string> | null = null;

  return {
    name: "missing-name-gate",
    apply: "build",
    buildStart() {
      if (process.env.npm_lifecycle_event === "build") return;
      pending = runTsc(root);
    },
    async buildEnd(error) {
      if (pending === null || error !== undefined) return;
      const output = await pending;
      pending = null;
      const live = runtimeMissingNames(missingNames(output), root);
      if (live.length === 0) return;
      this.error(
        [
          `${live.length} name(s) nothing defines would crash the app at runtime:`,
          ...live.map((name) => `  ${name.file}(${name.line},${name.column}): ${name.message}`),
          "A missing import? Rollup would have shipped it as a bare global.",
        ].join("\n"),
      );
    },
  };
}
