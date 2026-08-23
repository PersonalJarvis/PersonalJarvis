import { describe, expect, it } from "vitest";

import {
  artifactKind,
  artifactLanguage,
  formatBytes,
  hasRenderedView,
  parseCsv,
} from "@/lib/artifactKind";

describe("artifactKind", () => {
  it("maps deliverables to their in-app renderer by extension", () => {
    expect(artifactKind("tasks/a/artifacts/files/report.md", true)).toBe("markdown");
    expect(artifactKind("site/index.html", true)).toBe("html");
    expect(artifactKind("chart.PNG", false)).toBe("image");
    expect(artifactKind("paper.pdf", false)).toBe("pdf");
    expect(artifactKind("data.csv", true)).toBe("csv");
    expect(artifactKind("script.py", true)).toBe("code");
    expect(artifactKind("notes.txt", true)).toBe("text");
  });

  it("trusts the server's text verdict for unknown extensions", () => {
    expect(artifactKind("Makefile", true)).toBe("text");
    expect(artifactKind("model.bin", false)).toBe("binary");
  });

  it("only documents and tables own a rendered view distinct from source", () => {
    expect(hasRenderedView("markdown")).toBe(true);
    expect(hasRenderedView("html")).toBe(true);
    expect(hasRenderedView("csv")).toBe(true);
    expect(hasRenderedView("code")).toBe(false);
    expect(hasRenderedView("image")).toBe(false);
  });

  it("resolves the Shiki language from the extension", () => {
    expect(artifactLanguage("a/b/main.py")).toBe("python");
    expect(artifactLanguage("x.yml")).toBe("yaml");
    expect(artifactLanguage("README")).toBe("txt");
  });
});

describe("parseCsv", () => {
  it("handles quoted fields, doubled quotes and CRLF rows", () => {
    const rows = parseCsv('name,note\r\n"Smith, J","said ""hi"""\r\nplain,row\n');
    expect(rows).toEqual([
      ["name", "note"],
      ["Smith, J", 'said "hi"'],
      ["plain", "row"],
    ]);
  });

  it("accepts a tab delimiter", () => {
    expect(parseCsv("a\tb\n1\t2", "\t")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });
});

describe("formatBytes", () => {
  it("picks a readable unit", () => {
    expect(formatBytes(12)).toBe("12 B");
    expect(formatBytes(2048)).toBe("2.0 KiB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MiB");
  });
});
