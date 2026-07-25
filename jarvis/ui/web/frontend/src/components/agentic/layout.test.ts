import { describe, expect, it } from "vitest";
import { MAX_PANES_PER_BAND, paneColumns, paneLines, paneRows } from "./layout";

describe("paneColumns", () => {
  it("keeps a small workspace on one line", () => {
    // What the grid actually does today for every one of these: one row,
    // panes side by side. The wizard preview has to say the same thing.
    for (const n of [1, 2, 3, 4, 6, 8, 10]) {
      expect(paneColumns(n)).toBe(n);
    }
  });

  it("wraps beyond the readable width instead of shrinking panes further", () => {
    // 12 panes on one line leaves each of them too narrow to read, so they
    // break into two even lines: 6 above, 6 below.
    expect(paneColumns(11)).toBe(6);
    expect(paneColumns(12)).toBe(6);
  });

  it("keeps the lines even rather than filling the first one up", () => {
    // 21 panes could be 10 + 10 + 1; an almost empty last line next to two
    // full ones looks broken, so every line gets the same width.
    expect(paneColumns(21)).toBe(7);
    expect(paneColumns(20)).toBe(10);
  });

  it("has no columns for an empty workspace", () => {
    expect(paneColumns(0)).toBe(0);
  });

  it("exposes the cap it wraps at", () => {
    expect(MAX_PANES_PER_BAND).toBe(10);
  });
});

describe("paneLines", () => {
  it("is one line while the row fits", () => {
    expect(paneLines(1)).toBe(1);
    expect(paneLines(10)).toBe(1);
  });

  it("grows with the wrap, so a wrapped row gets the height for it", () => {
    expect(paneLines(11)).toBe(2);
    expect(paneLines(12)).toBe(2);
    expect(paneLines(21)).toBe(3);
  });

  it("is nothing for an empty workspace", () => {
    expect(paneLines(0)).toBe(0);
  });
});

describe("paneRows", () => {
  const term = (name: string, row: number) => ({ name, row });

  it("groups the panes by the row the backend gave them", () => {
    const panes = [term("Mika", 0), term("Nova", 0), term("Aria", 1)];
    expect(paneRows(panes).map((r) => r.map((p) => p.name))).toEqual([
      ["Mika", "Nova"],
      ["Aria"],
    ]);
  });

  it("keeps an over-wide row whole — the grid wraps it, not this", () => {
    // Splitting it here would move panes into a new parent element on every
    // wrap, which unmounts them and kills the agents behind them.
    const panes = Array.from({ length: 12 }, (_, i) => term(`T${i + 1}`, 0));
    const rows = paneRows(panes);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveLength(12);
  });

  it("skips rows the backend left empty", () => {
    // close_terminal() re-packs rows, but a session read mid-change can still
    // carry a gap — an empty row would render as a blank stripe.
    const panes = [term("Mika", 0), term("Nova", 3)];
    expect(paneRows(panes).map((r) => r.length)).toEqual([1, 1]);
  });

  it("handles an empty workspace", () => {
    expect(paneRows([])).toEqual([]);
  });
});
