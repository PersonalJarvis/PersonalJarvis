/*
 * What a coding tool call turns into on screen.
 *
 * The claim under test is not "a diff was produced" but "the lines that did
 * not move stayed grey": a change is readable exactly to the degree that the
 * unchanged code around it is visibly unchanged. Every CLI spells its
 * arguments differently, so each vocabulary gets its own case.
 */
import { describe, expect, it } from "vitest";

import { diffStat, isDiffTool, parsePatchText, toolDiff } from "./toolDiff";

/** The diff as one string, the way a reader sees it: "-old", "+new", " ctx". */
function render(lines: Array<{ kind: string; text: string }>): string {
  return lines
    .map((l) =>
      l.kind === "add" ? `+${l.text}` : l.kind === "del" ? `-${l.text}` : l.kind === "gap" ? "…" : ` ${l.text}`,
    )
    .join("\n");
}

describe("which calls carry a diff", () => {
  it("knows the editing tools of every CLI, and leaves the rest alone", () => {
    for (const name of ["Edit", "MultiEdit", "Write", "apply_patch", "str_replace_based_edit_tool", "NotebookEdit"]) {
      expect(isDiffTool(name)).toBe(true);
    }
    for (const name of ["Bash", "Read", "Grep", "WebFetch", "Task"]) {
      expect(isDiffTool(name)).toBe(false);
    }
  });

  it("answers null for a call that edits nothing, rather than an empty diff", () => {
    expect(toolDiff("Bash", { command: "ls" })).toBeNull();
    // An edit whose arguments did not survive the transport: the row falls
    // back to its plain summary, which is honest. An empty diff would not be.
    expect(toolDiff("Edit", { file_path: "a.ts" })).toBeNull();
  });
});

describe("Claude Code's edits", () => {
  it("colours only the line that moved, keeping its neighbours grey", () => {
    const files = toolDiff("Edit", {
      file_path: "src/app.ts",
      old_string: "const a = 1;\nconst b = 2;\nconst c = 3;",
      new_string: "const a = 1;\nconst b = 22;\nconst c = 3;",
    });
    expect(files).not.toBeNull();
    expect(files![0].path).toBe("src/app.ts");
    expect(render(files![0].lines)).toBe(
      [" const a = 1;", "-const b = 2;", "+const b = 22;", " const c = 3;"].join("\n"),
    );
    expect(diffStat(files!)).toEqual({ added: 1, removed: 1 });
  });

  it("reads a MultiEdit as one file changed in several places, in order", () => {
    const files = toolDiff("MultiEdit", {
      file_path: "src/app.ts",
      edits: [
        { old_string: "one", new_string: "ONE" },
        { old_string: "two", new_string: "TWO" },
      ],
    });
    expect(files).toHaveLength(2);
    expect(files!.every((f) => f.path === "src/app.ts")).toBe(true);
    expect(diffStat(files!)).toEqual({ added: 2, removed: 2 });
  });

  it("treats a Write as a file being started: every line is an addition", () => {
    const files = toolDiff("Write", { file_path: "new.ts", content: "a\nb\n" });
    expect(files![0].created).toBe(true);
    expect(render(files![0].lines)).toBe("+a\n+b");
    expect(diffStat(files!)).toEqual({ added: 2, removed: 0 });
  });

  it("reads the text editor's own field names too", () => {
    const files = toolDiff("str_replace_based_edit_tool", {
      command: "str_replace",
      path: "x.py",
      old_str: "print(1)",
      new_str: "print(2)",
    });
    expect(files![0].path).toBe("x.py");
    expect(diffStat(files!)).toEqual({ added: 1, removed: 1 });
  });
});

describe("Codex's apply_patch", () => {
  const PATCH = [
    "*** Begin Patch",
    "*** Update File: src/main.py",
    "@@",
    " def main():",
    "-    print('old')",
    "+    print('new')",
    " ",
    "*** End Patch",
  ].join("\n");

  it("takes the file name out of the patch envelope", () => {
    const files = toolDiff("apply_patch", { input: PATCH });
    expect(files).toHaveLength(1);
    expect(files![0].path).toBe("src/main.py");
    expect(diffStat(files!)).toEqual({ added: 1, removed: 1 });
  });

  it("reads a plain unified header as well, and splits it per file", () => {
    const files = parsePatchText(
      [
        "--- a/one.ts",
        "+++ b/one.ts",
        "@@ -1,2 +1,2 @@",
        "-a",
        "+A",
        "--- a/two.ts",
        "+++ b/two.ts",
        "@@ -1,1 +1,1 @@",
        "-b",
        "+B",
      ].join("\n"),
    );
    expect(files.map((f) => f.path)).toEqual(["one.ts", "two.ts"]);
  });

  it("accepts the patch as a bare string argument", () => {
    expect(toolDiff("apply_patch", PATCH)![0].path).toBe("src/main.py");
  });
});

describe("long changes stay readable", () => {
  it("replaces untouched stretches with one gap rather than printing them", () => {
    const before = Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n");
    const after = before.replace("line 20", "line twenty");
    const files = toolDiff("Edit", { file_path: "big.txt", old_string: before, new_string: after });
    const shown = files![0].lines;
    // Three lines of context each side, the change itself, and a gap standing
    // in for everything else — not forty lines to find one.
    expect(shown.filter((l) => l.kind === "gap")).toHaveLength(2);
    expect(shown.length).toBeLessThan(12);
    expect(render(shown)).toContain("-line 20");
    expect(render(shown)).toContain("+line twenty");
  });

  it("does not try to match line-by-line past the size where it would stall", () => {
    const before = Array.from({ length: 1400 }, (_, i) => `a${i}`).join("\n");
    const after = Array.from({ length: 1400 }, (_, i) => `b${i}`).join("\n");
    const files = toolDiff("Edit", { file_path: "huge.txt", old_string: before, new_string: after });
    expect(files![0].added).toBe(1400);
    expect(files![0].removed).toBe(1400);
    // Painted up to the cap, and honest about the rest.
    expect(files![0].lines.length).toBeLessThanOrEqual(400);
    expect(files![0].truncated).toBeGreaterThan(0);
  });
});
