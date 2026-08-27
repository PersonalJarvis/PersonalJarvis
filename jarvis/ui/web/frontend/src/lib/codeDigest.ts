/**
 * What a code deliverable IS, read from its text — pure, by filename and
 * content, no highlighter involved.
 *
 * The output page (`OutputPreview`) draws a script, a patch or a data file as
 * a card that says what the file does BEFORE it shows a line of it: the
 * module's own description (a Python docstring, a leading `/** … *\/` or `#`
 * comment block), the definitions it holds, a patch's file list with its
 * `+`/`−` counts, a JSON document's shape. The reader scrolling through a run
 * sees what was made; the source itself stays one click away, on the same
 * card and under Code / Files. Everything here degrades to "nothing found"
 * rather than guessing — an unknown language yields the line count alone.
 */
import { artifactLanguage } from "@/lib/artifactKind";

export interface CodeSymbol {
  kind: "function" | "class" | "const";
  name: string;
}

export interface DiffFile {
  path: string;
  added: number;
  removed: number;
}

export type JsonShape =
  | { kind: "object"; keys: string[]; count: number }
  | { kind: "array"; count: number };

export interface CodeDigest {
  /** The Shiki language of the file ("txt" when unknown). */
  language: string;
  lines: number;
  /** The file's own description — the first paragraph of its leading doc comment. */
  description: string | null;
  /** Top-level definitions in file order (functions, classes, exported constants). */
  symbols: CodeSymbol[];
  /** A JSON / JSONL document's shape, when the file is one. */
  json: JsonShape | null;
  /** A patch's touched files, when the file is one. */
  diff: DiffFile[] | null;
}

const MAX_DESCRIPTION = 320;
const MAX_SYMBOLS = 40;
const MAX_JSON_KEYS = 24;

function countLines(text: string): number {
  if (text.length === 0) return 0;
  const trimmed = text.endsWith("\n") ? text.slice(0, -1) : text;
  return trimmed.split("\n").length;
}

/** First paragraph of a comment body, joined to one line and capped. */
function firstParagraph(lines: string[]): string | null {
  const out: string[] = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (line.length === 0) {
      if (out.length > 0) break;
      continue;
    }
    out.push(line);
  }
  if (out.length === 0) return null;
  const text = out.join(" ").replace(/\s+/g, " ").trim();
  if (text.length <= MAX_DESCRIPTION) return text;
  return `${text.slice(0, MAX_DESCRIPTION - 1).trimEnd()}…`;
}

// --- Python -----------------------------------------------------------------

const PY_PREAMBLE = /^(#.*|\s*|from __future__ import .*|import __future__.*)$/;

function pythonDescription(lines: string[]): string | null {
  let i = 0;
  while (i < lines.length && PY_PREAMBLE.test(lines[i])) i++;
  if (i >= lines.length) return null;
  const open = /^\s*[rRuUbB]{0,2}("""|''')/.exec(lines[i]);
  if (!open) return null;
  const quote = open[1];
  const first = lines[i].slice(open[0].length);
  const body: string[] = [];
  const endOnFirst = first.indexOf(quote);
  if (endOnFirst !== -1) return firstParagraph([first.slice(0, endOnFirst)]);
  body.push(first);
  for (let j = i + 1; j < lines.length; j++) {
    const end = lines[j].indexOf(quote);
    if (end !== -1) {
      body.push(lines[j].slice(0, end));
      break;
    }
    body.push(lines[j]);
  }
  return firstParagraph(body);
}

const PY_DEF = /^(?:async\s+)?def\s+([A-Za-z_]\w*)/;
const PY_CLASS = /^class\s+([A-Za-z_]\w*)/;

function pythonSymbols(lines: string[]): CodeSymbol[] {
  const out: CodeSymbol[] = [];
  for (const line of lines) {
    let m = PY_CLASS.exec(line);
    if (m) {
      out.push({ kind: "class", name: m[1] });
      continue;
    }
    m = PY_DEF.exec(line);
    if (m && !m[1].startsWith("_")) out.push({ kind: "function", name: m[1] });
  }
  return out;
}

// --- JavaScript / TypeScript ---------------------------------------------------

function blockCommentDescription(lines: string[]): string | null {
  let i = 0;
  while (i < lines.length && (/^\s*$/.test(lines[i]) || lines[i].startsWith("#!"))) i++;
  if (i >= lines.length) return null;
  const head = lines[i].trim();
  if (head.startsWith("/*")) {
    const body: string[] = [];
    const first = head.replace(/^\/\*+/, "");
    const closeOnFirst = first.indexOf("*/");
    if (closeOnFirst !== -1) return firstParagraph([first.slice(0, closeOnFirst)]);
    body.push(first);
    for (let j = i + 1; j < lines.length; j++) {
      const raw = lines[j].trim();
      const close = raw.indexOf("*/");
      body.push((close === -1 ? raw : raw.slice(0, close)).replace(/^\*\s?/, ""));
      if (close !== -1) break;
    }
    return firstParagraph(body);
  }
  if (head.startsWith("//")) {
    const body: string[] = [];
    for (let j = i; j < lines.length; j++) {
      const line = lines[j].trim();
      if (!line.startsWith("//")) break;
      body.push(line.replace(/^\/\/\s?/, ""));
    }
    return firstParagraph(body);
  }
  return null;
}

const JS_FUNCTION = /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)/;
const JS_CLASS = /^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)/;
const JS_ARROW =
  /^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+)?=>/;
const JS_EXPORT_CONST = /^export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)/;

function jsSymbols(lines: string[]): CodeSymbol[] {
  const out: CodeSymbol[] = [];
  for (const line of lines) {
    let m = JS_CLASS.exec(line);
    if (m) {
      out.push({ kind: "class", name: m[1] });
      continue;
    }
    m = JS_FUNCTION.exec(line);
    if (m) {
      out.push({ kind: "function", name: m[1] });
      continue;
    }
    m = JS_ARROW.exec(line);
    if (m) {
      out.push({ kind: "function", name: m[1] });
      continue;
    }
    m = JS_EXPORT_CONST.exec(line);
    if (m) out.push({ kind: "const", name: m[1] });
  }
  return out;
}

// --- Shell / PowerShell -------------------------------------------------------

function hashCommentDescription(lines: string[]): string | null {
  let i = 0;
  while (i < lines.length && (/^\s*$/.test(lines[i]) || lines[i].startsWith("#!"))) i++;
  if (i < lines.length && lines[i].trim().startsWith("<#")) {
    const body: string[] = [];
    for (let j = i; j < lines.length; j++) {
      const line = lines[j].replace(/^\s*<#/, "");
      const close = line.indexOf("#>");
      if (close !== -1) {
        body.push(line.slice(0, close));
        break;
      }
      body.push(line);
    }
    // A PowerShell help block leads with `.SYNOPSIS`; the text under it, up to
    // the next `.KEYWORD`, is the description.
    const keyword = (l: string) => /^\s*\.\w+/.test(l);
    const synopsis = body.findIndex((l) => /^\s*\.SYNOPSIS/i.test(l));
    if (synopsis === -1) return firstParagraph(body.filter((l) => !keyword(l)));
    const under = body.slice(synopsis + 1);
    const next = under.findIndex(keyword);
    return firstParagraph(next === -1 ? under : under.slice(0, next));
  }
  const body: string[] = [];
  for (let j = i; j < lines.length; j++) {
    const line = lines[j].trim();
    if (!line.startsWith("#")) break;
    if (/^#\s*-\*-/.test(line) || /^#\s*(?:shellcheck|noqa)/i.test(line)) continue;
    body.push(line.replace(/^#+\s?/, ""));
  }
  return firstParagraph(body);
}

const SH_FUNCTION = /^(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{?/;
const SH_FUNCTION_KW = /^function\s+([A-Za-z_][\w-]*)/;

function shellSymbols(lines: string[]): CodeSymbol[] {
  const out: CodeSymbol[] = [];
  for (const line of lines) {
    const m = SH_FUNCTION.exec(line) ?? SH_FUNCTION_KW.exec(line);
    if (m) out.push({ kind: "function", name: m[1] });
  }
  return out;
}

// --- JSON -----------------------------------------------------------------------

function jsonShape(text: string, jsonl: boolean): JsonShape | null {
  if (jsonl) {
    const records = text.split("\n").filter((l) => l.trim().length > 0).length;
    return { kind: "array", count: records };
  }
  try {
    const value: unknown = JSON.parse(text);
    if (Array.isArray(value)) return { kind: "array", count: value.length };
    if (value !== null && typeof value === "object") {
      const keys = Object.keys(value as Record<string, unknown>);
      return { kind: "object", keys: keys.slice(0, MAX_JSON_KEYS), count: keys.length };
    }
  } catch {
    // Not a JSON document after all (a fragment, a comment-laden config):
    // the card falls back to the line count, which is always true.
  }
  return null;
}

// --- Patches --------------------------------------------------------------------

const DIFF_GIT = /^diff --git a\/(.+?) b\/(.+)$/;
const DIFF_NEW = /^\+\+\+ (?:b\/)?(.+)$/;

function diffFiles(lines: string[]): DiffFile[] {
  const out: DiffFile[] = [];
  let current: DiffFile | null = null;
  let sawGitHeader = false;
  for (const line of lines) {
    let m = DIFF_GIT.exec(line);
    if (m) {
      current = { path: m[2], added: 0, removed: 0 };
      out.push(current);
      sawGitHeader = true;
      continue;
    }
    m = DIFF_NEW.exec(line);
    if (m) {
      // A plain unified diff has no `diff --git` line; `+++` names the file.
      if (!sawGitHeader || current === null) {
        current = { path: m[1].trim(), added: 0, removed: 0 };
        out.push(current);
      }
      continue;
    }
    if (line.startsWith("--- ")) continue;
    if (current === null) continue;
    if (line.startsWith("+")) current.added++;
    else if (line.startsWith("-")) current.removed++;
  }
  return out;
}

/** A `git format-patch` subject, without the `[PATCH n/m]` tag. */
function patchDescription(lines: string[]): string | null {
  for (const line of lines) {
    if (DIFF_GIT.test(line) || line.startsWith("--- ")) break;
    const m = /^Subject:\s*(?:\[PATCH[^\]]*\]\s*)?(.+)$/.exec(line);
    if (m) return m[1].trim();
  }
  return null;
}

// --- The digest -------------------------------------------------------------------

export function codeDigest(path: string, text: string): CodeDigest {
  const language = artifactLanguage(path);
  const lines = text.split("\n");
  const digest: CodeDigest = {
    language,
    lines: countLines(text),
    description: null,
    symbols: [],
    json: null,
    diff: null,
  };
  switch (language) {
    case "python":
      digest.description = pythonDescription(lines);
      digest.symbols = pythonSymbols(lines);
      break;
    case "typescript":
    case "tsx":
    case "javascript":
    case "jsx":
      digest.description = blockCommentDescription(lines);
      digest.symbols = jsSymbols(lines);
      break;
    case "bash":
    case "powershell":
      digest.description = hashCommentDescription(lines);
      digest.symbols = shellSymbols(lines);
      break;
    case "json":
      digest.json = jsonShape(text, path.toLowerCase().endsWith(".jsonl"));
      break;
    case "diff":
      digest.description = patchDescription(lines);
      digest.diff = diffFiles(lines);
      break;
    case "rust":
    case "go":
    case "java":
    case "c":
    case "cpp":
    case "csharp":
    case "kotlin":
    case "swift":
      digest.description = blockCommentDescription(lines);
      break;
    default:
      break;
  }
  digest.symbols = digest.symbols.slice(0, MAX_SYMBOLS);
  return digest;
}
