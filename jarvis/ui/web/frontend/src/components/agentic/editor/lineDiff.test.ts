import { describe, expect, it } from "vitest";

import { gutterChanges } from "./lineDiff";

describe("gutter changes", () => {
  it("marks nothing for an unchanged file", () => {
    expect(gutterChanges("a\nb\n", "a\nb\n")).toEqual([]);
  });

  it("marks added, modified and deleted lines in current-file coordinates", () => {
    const base = ["one", "two", "three", "four", "five"].join("\n");
    const current = ["one", "TWO", "three", "new a", "new b", "four"].join("\n");
    expect(gutterChanges(base, current)).toEqual([
      { kind: "modified", start: 2, end: 2 },
      { kind: "added", start: 4, end: 5 },
      { kind: "deleted", start: 7, end: 7 },
    ]);
  });

  it("treats CRLF and LF alike", () => {
    expect(gutterChanges("a\r\nb\r\n", "a\nb\n")).toEqual([]);
  });

  it("marks a deletion at the start before the first remaining line", () => {
    expect(gutterChanges("x\na\nb", "a\nb")).toEqual([{ kind: "deleted", start: 1, end: 1 }]);
  });

  it("reads an empty base as a rewrite of its one empty line", () => {
    expect(gutterChanges("", "a\nb")).toEqual([{ kind: "modified", start: 1, end: 2 }]);
  });
});
