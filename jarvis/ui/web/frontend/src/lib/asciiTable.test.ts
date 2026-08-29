/**
 * The ASCII grid parser: it recognises the `+---+` / `| a | b |` art a worker
 * writes as a table, and — just as importantly — refuses everything else, so
 * ordinary code and prose never get mangled into a table.
 */
import { describe, expect, it } from "vitest";

import { fenceLooseAsciiTables, parseAsciiTable } from "@/lib/asciiTable";

const lines = (...rows: string[]) => rows.join("\n");

const SIMPLE = lines(
  "+------+----------+",
  "| ID   | Priority |",
  "+------+----------+",
  "| 01   | P1       |",
  "| 02   | P2       |",
  "+------+----------+",
);

describe("parseAsciiTable", () => {
  it("reads head and body out of a drawn grid", () => {
    const grid = parseAsciiTable(SIMPLE);
    expect(grid).not.toBeNull();
    expect(grid?.columns).toBe(2);
    expect(grid?.head).toEqual(["ID", "Priority"]);
    expect(grid?.rows).toEqual([
      ["01", "P1"],
      ["02", "P2"],
    ]);
    expect(grid?.caption).toBeNull();
  });

  it("takes a one-cell opening row as the banner above the head", () => {
    const grid = parseAsciiTable(
      lines(
        "+---------------------------+",
        "|  MASTER EXECUTION MATRIX  |",
        "+------+----------+---------+",
        "| ID   | Priority | Item    |",
        "+------+----------+---------+",
        "| 01   | P1       | Fix CI  |",
        "+------+----------+---------+",
      ),
    );
    expect(grid?.caption).toBe("MASTER EXECUTION MATRIX");
    expect(grid?.head).toEqual(["ID", "Priority", "Item"]);
    expect(grid?.rows).toEqual([["01", "P1", "Fix CI"]]);
  });

  it("keeps a one-cell row inside the body as a spanning row", () => {
    const grid = parseAsciiTable(
      lines(
        "+------+----------+",
        "| ID   | Priority |",
        "+------+----------+",
        "| 01   | P1       |",
        "| Later this week |",
        "| 02   | P3       |",
        "+------+----------+",
      ),
    );
    expect(grid?.columns).toBe(2);
    expect(grid?.rows).toEqual([["01", "P1"], ["Later this week"], ["02", "P3"]]);
  });

  it("accepts a GFM table that ended up inside a fence", () => {
    const grid = parseAsciiTable(lines("| a | b |", "|---|---|", "| 1 | 2 |"));
    expect(grid?.head).toEqual(["a", "b"]);
    expect(grid?.rows).toEqual([["1", "2"]]);
  });

  it("refuses anything that is not a pure grid", () => {
    // Prose and code must never be read as a table.
    expect(parseAsciiTable("hello\nworld\nagain")).toBeNull();
    expect(parseAsciiTable(lines("| a | b |", "|---|---|", "not a row"))).toBeNull();
    // A box drawn around a paragraph is one column, not a table.
    expect(parseAsciiTable(lines("+-------+", "| hi    |", "+-------+"))).toBeNull();
    // Rows with no rule between them are just pipes in text.
    expect(parseAsciiTable(lines("| a | b |", "| 1 | 2 |", "| 3 | 4 |"))).toBeNull();
    expect(parseAsciiTable("")).toBeNull();
  });
});

describe("fenceLooseAsciiTables", () => {
  it("fences a drawn grid the author left bare", () => {
    const out = fenceLooseAsciiTables(`## Matrix\n\n${SIMPLE}\n\ndone`);
    expect(out).toContain("```\n+------+----------+");
    expect(out.endsWith("+------+----------+\n```\n\ndone")).toBe(true);
  });

  it("leaves a GFM table alone — Markdown already renders it", () => {
    const gfm = lines("| a | b |", "|---|---|", "| 1 | 2 |");
    expect(fenceLooseAsciiTables(gfm)).toBe(gfm);
  });

  it("never reaches inside an existing fence", () => {
    const doc = `\`\`\`python\n${SIMPLE}\n\`\`\`\n`;
    expect(fenceLooseAsciiTables(doc)).toBe(doc);
  });

  it("leaves a document with no grid byte-identical", () => {
    const doc = "# Title\n\nSome prose with a | pipe in it.\n\n- a\n- b\n";
    expect(fenceLooseAsciiTables(doc)).toBe(doc);
  });
});
