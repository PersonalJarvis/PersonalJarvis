/**
 * A code-editing tool call → the diff it describes, ready to paint.
 *
 * A row that says `Edit src/app.ts` tells you a file changed and nothing
 * about WHAT changed, and the raw arguments underneath are worse: an `Edit`
 * carries its before and after as two JSON string literals, escapes and all,
 * so the one thing worth reading is the one thing you cannot (maintainer,
 * 2026-08-27, against Claude Code and Codex, which both paint the change).
 *
 * So this module turns whatever the agent's own tool vocabulary called it
 * into the same small shape: a list of files, each a list of lines marked
 * added / removed / context. Every coding CLI is covered because they
 * disagree about the field names and agree about nothing else:
 *
 * - Claude Code `Edit`            — `old_string` / `new_string`
 * - Claude Code `MultiEdit`       — `edits: [{old_string, new_string}]`
 * - Claude Code `Write`           — `content`, whole file, all additions
 * - Anthropic text editor         — `command: str_replace|create`, `old_str`
 * - Codex / OpenAI `apply_patch`  — a unified-ish patch as ONE string
 * - Notebook edits                — `new_source` against `old_source`
 *
 * Pure — no React — so the mapping is unit-testable and identical wherever a
 * diff is drawn.
 */

/** One printed line of a diff. `gap` stands for context that was skipped. */
export interface DiffLine {
  kind: "add" | "del" | "ctx" | "gap";
  text: string;
}

export interface DiffFile {
  /** The path the tool named, or "" when it named none. */
  path: string;
  lines: DiffLine[];
  added: number;
  removed: number;
  /** A file being written whole: there is no "before" to show. */
  created: boolean;
  /** Lines dropped from the end because the change is enormous. */
  truncated: number;
}

/**
 * How much unchanged code stays around a change.
 *
 * Three lines is the unified-diff convention and it is the right amount for
 * reading: enough to recognise where in the file you are, not so much that a
 * one-line fix arrives inside a screenful of things that did not move.
 */
const CONTEXT_LINES = 3;

/** Past this, painting every line costs more than it tells. */
const MAX_LINES = 400;

/**
 * Above this many lines on either side, the line-by-line match is dropped.
 *
 * The matcher is quadratic. That is free for the hunk-sized before/after an
 * `Edit` carries and ruinous for a 5000-line file pasted in whole, so past
 * the cap the change is shown honestly as "all of that, then all of this"
 * rather than freezing the column to compute a prettier answer.
 */
const MATCH_CAP = 1200;

export function isDiffTool(name: string): boolean {
  return /^(edit|multi_?edit|write|create_?file|write_?file|apply_?patch|str_replace(_based_edit_tool)?|notebook_?edit|update_?file|patch_?file)$/i.test(
    (name ?? "").trim(),
  );
}

/** Split keeping no trailing empty line, so a file ending in \n has no ghost row. */
function lines(text: string): string[] {
  const out = text.replace(/\r\n/g, "\n").split("\n");
  if (out.length > 1 && out[out.length - 1] === "") out.pop();
  return out;
}

function str(obj: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const v = obj[key];
    if (typeof v === "string") return v;
  }
  return null;
}

/**
 * Longest-common-subsequence line match, then rendered as a unified diff.
 *
 * The match is what separates this from "print the old, print the new": the
 * lines both sides share stay grey and only what actually moved is coloured,
 * which is the difference between reading a change and re-reading a file.
 */
function diffLines(before: string, after: string): DiffLine[] {
  const a = lines(before);
  const b = lines(after);

  if (a.length > MATCH_CAP || b.length > MATCH_CAP) {
    return [
      ...a.map((text): DiffLine => ({ kind: "del", text })),
      ...b.map((text): DiffLine => ({ kind: "add", text })),
    ];
  }

  // table[i][j] = length of the LCS of a[i:] and b[j:]
  const table: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] =
        a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: "ctx", text: a[i] });
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      out.push({ kind: "del", text: a[i] });
      i++;
    } else {
      out.push({ kind: "add", text: b[j] });
      j++;
    }
  }
  while (i < a.length) out.push({ kind: "del", text: a[i++] });
  while (j < b.length) out.push({ kind: "add", text: b[j++] });
  return out;
}

/**
 * Keep the changes and `CONTEXT_LINES` around each, replacing the rest with
 * one `gap` row. A diff of a long file is mostly lines that did not change.
 */
export function trimContext(all: DiffLine[]): DiffLine[] {
  const keep = new Array<boolean>(all.length).fill(false);
  let anyChange = false;
  for (let i = 0; i < all.length; i++) {
    if (all[i].kind === "ctx") continue;
    anyChange = true;
    for (let k = Math.max(0, i - CONTEXT_LINES); k <= Math.min(all.length - 1, i + CONTEXT_LINES); k++) {
      keep[k] = true;
    }
  }
  // Nothing moved (an Edit whose strings are equal): say so with the text
  // itself rather than with an empty frame.
  if (!anyChange) return all.slice(0, CONTEXT_LINES * 2);

  const out: DiffLine[] = [];
  let skipped = 0;
  for (let i = 0; i < all.length; i++) {
    if (keep[i]) {
      if (skipped > 0) {
        out.push({ kind: "gap", text: String(skipped) });
        skipped = 0;
      }
      out.push(all[i]);
    } else {
      skipped++;
    }
  }
  // A trailing gap too, unlike a unified diff: on screen the last printed
  // line otherwise reads as the end of the file rather than as the end of
  // the part worth showing.
  if (skipped > 0) out.push({ kind: "gap", text: String(skipped) });
  return out;
}

function build(path: string, before: string, after: string, created = false): DiffFile {
  const all = created
    ? lines(after).map((text): DiffLine => ({ kind: "add", text }))
    : diffLines(before, after);
  const trimmed = trimContext(all);
  const shown = trimmed.slice(0, MAX_LINES);
  return {
    path,
    lines: shown,
    added: all.filter((l) => l.kind === "add").length,
    removed: all.filter((l) => l.kind === "del").length,
    created,
    truncated: Math.max(0, trimmed.length - shown.length),
  };
}

/**
 * Parse a Codex-style `apply_patch` payload.
 *
 * The patch arrives as ONE string carrying its own file headers, so the file
 * names come out of the text rather than out of a field. Both the
 * `*** Update File: x` envelope and a plain `--- a/x` / `+++ b/x` unified
 * header are read, because the two live side by side in the wild.
 */
export function parsePatchText(patch: string): DiffFile[] {
  const files: DiffFile[] = [];
  let path = "";
  let created = false;
  let acc: DiffLine[] = [];

  const flush = () => {
    if (!acc.length && !path) return;
    const trimmed = trimContext(acc);
    const shown = trimmed.slice(0, MAX_LINES);
    files.push({
      path,
      lines: shown,
      added: acc.filter((l) => l.kind === "add").length,
      removed: acc.filter((l) => l.kind === "del").length,
      created,
      truncated: Math.max(0, trimmed.length - shown.length),
    });
    acc = [];
  };

  for (const raw of patch.replace(/\r\n/g, "\n").split("\n")) {
    const envelope = /^\*\*\* (Add|Update|Delete) File: (.+)$/.exec(raw);
    if (envelope) {
      flush();
      path = envelope[2].trim();
      created = envelope[1] === "Add";
      continue;
    }
    if (/^\*\*\* (Begin|End) Patch\s*$/.test(raw)) continue;
    const unified = /^\+\+\+ (?:b\/)?(.+)$/.exec(raw);
    if (unified) {
      // The "---" line came first and told us nothing new; the "+++" one
      // names the file as it will be, which is the name worth showing.
      if (acc.length) flush();
      path = unified[1].trim();
      continue;
    }
    if (/^--- /.test(raw) || /^(diff |index |new file|deleted file|similarity |rename )/.test(raw)) {
      continue;
    }
    if (/^@@/.test(raw)) {
      if (acc.length) acc.push({ kind: "gap", text: "" });
      continue;
    }
    if (raw.startsWith("+")) acc.push({ kind: "add", text: raw.slice(1) });
    else if (raw.startsWith("-")) acc.push({ kind: "del", text: raw.slice(1) });
    else if (raw.startsWith(" ")) acc.push({ kind: "ctx", text: raw.slice(1) });
    else if (raw.trim()) acc.push({ kind: "ctx", text: raw });
  }
  flush();
  return files.filter((f) => f.lines.length > 0);
}

/**
 * The diff a tool call describes, or `null` when it describes none.
 *
 * `null` is the honest answer for a call that edits nothing, and also for an
 * edit whose arguments did not survive the transport — a row falling back to
 * its plain summary is right, inventing an empty diff is not.
 */
export function toolDiff(name: string, input: unknown): DiffFile[] | null {
  const raw = (name ?? "").trim();
  if (!isDiffTool(raw)) return null;
  if (typeof input === "string") {
    const files = parsePatchText(input);
    return files.length ? files : null;
  }
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const obj = input as Record<string, unknown>;

  // Codex hands the whole patch as one string under one of several names.
  const patch = str(obj, "patch", "input", "diff", "content_patch");
  if (patch && /^\s*(\*\*\* |--- |diff --git|@@)/m.test(patch)) {
    const files = parsePatchText(patch);
    if (files.length) return files;
  }

  const path = str(obj, "file_path", "path", "notebook_path", "filename", "file") ?? "";

  // MultiEdit: several before/after pairs against one file, read in order.
  const edits = obj.edits;
  if (Array.isArray(edits) && edits.length) {
    const out: DiffFile[] = [];
    for (const entry of edits) {
      if (!entry || typeof entry !== "object") continue;
      const e = entry as Record<string, unknown>;
      const before = str(e, "old_string", "old_str", "old", "old_source");
      const after = str(e, "new_string", "new_str", "new", "new_source");
      if (before === null && after === null) continue;
      out.push(build(path, before ?? "", after ?? ""));
    }
    if (out.length) return out;
  }

  const before = str(obj, "old_string", "old_str", "old_source", "old_text");
  const after = str(obj, "new_string", "new_str", "new_source", "new_text");
  if (before !== null || after !== null) {
    // An Edit with an empty "before" is a file being started, not a change
    // with nothing on its left.
    const created = !before?.trim();
    return [build(path, before ?? "", after ?? "", created)];
  }

  // Write / create: the whole file arrives, and all of it is new.
  const content = str(obj, "content", "file_text", "text", "source");
  if (content !== null) return [build(path, "", content, true)];

  return null;
}

/** `+12 −3` for the row, or "" when the call added and removed nothing. */
export function diffStat(files: DiffFile[]): { added: number; removed: number } {
  return files.reduce(
    (acc, f) => ({ added: acc.added + f.added, removed: acc.removed + f.removed }),
    { added: 0, removed: 0 },
  );
}
