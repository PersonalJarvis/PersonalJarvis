import { describe, expect, it } from "vitest";

import { parseQuickOpen, rankPaths, scorePath } from "./quickOpenMatch";

const PATHS = [
  "jarvis/ui/web/frontend/src/views/AgenticIdeView.tsx",
  "jarvis/agentic_ide/file_editing.py",
  "tests/unit/agentic_ide/test_file_editing.py",
  "docs/editing-guide.md",
  "README.md",
];

describe("quick open matching", () => {
  it("needs the letters in order", () => {
    expect(scorePath("README.md", "rdm")).not.toBeNull();
    expect(scorePath("README.md", "mdr")).toBeNull();
  });

  it("ranks a file-name hit above a hit spread over the folders", () => {
    expect(rankPaths(PATHS, "fileediting")[0]).toBe("jarvis/agentic_ide/file_editing.py");
    expect(rankPaths(PATHS, "readme")[0]).toBe("README.md");
    expect(rankPaths(PATHS, "ideview")[0]).toBe("jarvis/ui/web/frontend/src/views/AgenticIdeView.tsx");
  });

  it("reads a line and column after the name", () => {
    expect(parseQuickOpen("app.ts:42")).toEqual({ needle: "app.ts", line: 42, column: undefined });
    expect(parseQuickOpen(" src\\App .ts:3:9 ")).toEqual({ needle: "src/app.ts", line: 3, column: 9 });
    expect(parseQuickOpen("app.ts")).toEqual({ needle: "app.ts", line: undefined, column: undefined });
  });
});
