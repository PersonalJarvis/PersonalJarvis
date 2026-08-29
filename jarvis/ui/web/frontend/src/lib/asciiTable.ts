/**
 * ASCII grid tables — the `+---+---+` / `| a | b |` art a model writes when it
 * wants a table but is producing plain text — parsed into rows a real
 * `<table>` can be drawn from.
 *
 * Why this exists: such a grid only survives in a monospace block with its
 * line breaks intact, and even then it stays a PICTURE of a table — nothing
 * the reader can scan at reading width or copy a row out of. Recognising the
 * shape lets the artifact renderer draw the table the author actually meant.
 *
 * The grammar accepted is deliberately narrow, because a false positive turns
 * ordinary text into a mangled table:
 * - a RULE line ends in `+` or `|` on both sides, carries only `-`, `=`, `+`,
 *   `|` and spaces between them, and contains at least one `-` or `=`;
 * - a ROW line starts and ends with `|`;
 * - EVERY non-blank line must be one or the other, and at least one rule must
 *   be present. Anything else returns `null` and the caller leaves the text
 *   exactly as it found it.
 */

export interface AsciiGrid {
  /** A single full-width banner row the grid opens with, if it has one. */
  caption: string | null;
  /** Header cells, when a rule separates the first row from the body. */
  head: string[] | null;
  /** Body rows. A one-cell row in a multi-column grid spans the whole table. */
  rows: string[][];
  /** The widest row — the column count a spanning row spans. */
  columns: number;
}

const RULE_SHAPE = /^[+|][-=+|\s]*[+|]$/;

/** A horizontal border line of the grid. */
function isRule(line: string): boolean {
  return RULE_SHAPE.test(line) && /[-=]/.test(line);
}

/** A content line of the grid. Rules are tested first, so they never reach this. */
function isRow(line: string): boolean {
  return line.length > 2 && line.startsWith("|") && line.endsWith("|");
}

/**
 * Split a row into trimmed cells. A `|` inside a cell would split it wrongly,
 * which is the one ambiguity ASCII art cannot express and we do not try to.
 */
function cellsOf(line: string): string[] {
  return line
    .slice(1, -1)
    .split("|")
    .map((cell) => cell.trim());
}

/**
 * Parse *text* as an ASCII grid table, or return `null` when it is not one.
 *
 * Also accepts a GFM table (`|---|---|` rules, no `+` corners) that ended up
 * inside a code fence, where Markdown would never have rendered it as a table.
 */
export function parseAsciiTable(text: string): AsciiGrid | null {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (lines.length < 3) return null;

  // Rules cut the grid into sections: an optional banner, an optional header,
  // and the body. Which section is which is decided once they are all in.
  const sections: string[][][] = [];
  let current: string[][] = [];
  let rules = 0;
  for (const line of lines) {
    if (isRule(line)) {
      rules += 1;
      if (current.length > 0) {
        sections.push(current);
        current = [];
      }
      continue;
    }
    if (!isRow(line)) return null;
    current.push(cellsOf(line));
  }
  if (current.length > 0) sections.push(current);
  if (rules === 0 || sections.length === 0) return null;

  const columns = Math.max(...sections.flat().map((row) => row.length));
  // A one-column "grid" is a box drawn around a paragraph, not a table.
  if (columns < 2) return null;

  let rest = sections;
  let caption: string | null = null;
  if (rest[0].length === 1 && rest[0][0].length === 1) {
    caption = rest[0][0][0];
    rest = rest.slice(1);
  }
  let head: string[] | null = null;
  if (rest.length > 1 && rest[0].length === 1 && rest[0][0].length === columns) {
    head = rest[0][0];
    rest = rest.slice(1);
  }

  const rows = rest.flat();
  if (rows.length === 0) return null;
  return { caption, head, rows, columns };
}

const FENCE_MARKER = /^\s*(?:```|~~~)/;

/**
 * Wrap ASCII grid tables the author never fenced in a fence, so the Markdown
 * parser stops folding them into one run-on paragraph.
 *
 * Only a block whose rule lines carry a `+` corner is touched. A GFM table
 * (`|---|---|`) is left exactly as it is — Markdown already renders that as a
 * real table, and fencing it would be a downgrade.
 */
export function fenceLooseAsciiTables(text: string): string {
  const out: string[] = [];
  let block: string[] = [];
  let insideFence = false;

  const flush = () => {
    if (block.length === 0) return;
    const looksDrawn = block.some((line) => {
      const trimmed = line.trim();
      return isRule(trimmed) && trimmed.includes("+");
    });
    if (looksDrawn && parseAsciiTable(block.join("\n")) !== null) {
      out.push("```", ...block, "```");
    } else {
      out.push(...block);
    }
    block = [];
  };

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (FENCE_MARKER.test(raw)) {
      flush();
      insideFence = !insideFence;
      out.push(raw);
      continue;
    }
    if (!insideFence && (isRule(line) || isRow(line))) {
      block.push(raw);
      continue;
    }
    flush();
    out.push(raw);
  }
  flush();
  return out.join("\n");
}
