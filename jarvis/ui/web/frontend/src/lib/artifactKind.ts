/**
 * How an Outputs deliverable is SHOWN inside the app — pure, by filename.
 *
 * The Outputs view renders every file the way a person wants to look at it,
 * not the way it is stored: a Markdown report reads as a document, an HTML
 * page runs as a page, an image is an image, code is highlighted, a CSV is a
 * table. `classifyArtifact` in `hooks/useOutputs.ts` answers a different
 * question (which URL the *browser* gets); this module answers which in-app
 * renderer draws the file.
 */

export type ArtifactKind =
  | "markdown"
  | "html"
  | "image"
  | "pdf"
  | "code"
  | "csv"
  | "text"
  | "binary";

const IMAGE_EXT = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"];
const MARKDOWN_EXT = [".md", ".markdown", ".mdx"];
const HTML_EXT = [".html", ".htm"];
const CSV_EXT = [".csv", ".tsv"];
const TEXT_EXT = [".txt", ".log", ".env", ".cfg", ".ini", ".patch", ".diff"];

/** Extension -> Shiki language for the source / code renderer. */
const CODE_LANG: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  mjs: "javascript",
  cjs: "javascript",
  json: "json",
  jsonl: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  ini: "ini",
  cfg: "ini",
  xml: "xml",
  html: "html",
  htm: "html",
  css: "css",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  ps1: "powershell",
  rs: "rust",
  go: "go",
  java: "java",
  c: "c",
  h: "c",
  cpp: "cpp",
  hpp: "cpp",
  cs: "csharp",
  kt: "kotlin",
  swift: "swift",
  sql: "sql",
  diff: "diff",
  patch: "diff",
  md: "markdown",
  markdown: "markdown",
  mdx: "markdown",
};

function extOf(path: string): string {
  const name = path.split("/").pop() ?? path;
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
}

function hasExt(path: string, exts: string[]): boolean {
  const lower = path.toLowerCase();
  return exts.some((e) => lower.endsWith(e));
}

/** Decide which in-app renderer draws a deliverable. `isText` is the server's
 *  own verdict for the extension-less / unknown case. */
export function artifactKind(path: string, isText: boolean): ArtifactKind {
  if (hasExt(path, MARKDOWN_EXT)) return "markdown";
  if (hasExt(path, HTML_EXT)) return "html";
  if (hasExt(path, IMAGE_EXT)) return "image";
  if (hasExt(path, [".pdf"])) return "pdf";
  if (hasExt(path, CSV_EXT)) return "csv";
  if (CODE_LANG[extOf(path)]) return "code";
  if (hasExt(path, TEXT_EXT)) return "text";
  return isText ? "text" : "binary";
}

/** The Shiki language for a file's source view ("txt" when unknown). */
export function artifactLanguage(path: string): string {
  return CODE_LANG[extOf(path)] ?? "txt";
}

/** Kinds that own a "rendered" presentation distinct from their source text. */
export function hasRenderedView(kind: ArtifactKind): boolean {
  return kind === "markdown" || kind === "html" || kind === "csv";
}

/** Kinds whose bytes are text the viewer fetches through `/raw`. */
export function isTextKind(kind: ArtifactKind): boolean {
  return kind === "markdown" || kind === "html" || kind === "code" || kind === "csv" || kind === "text";
}

/**
 * Minimal RFC-4180-ish CSV parser: quoted fields, doubled quotes, CR/LF rows.
 * Tabs are accepted as the delimiter for `.tsv`. Good enough for a worker's
 * exported table; never used for anything but display.
 */
export function parseCsv(text: string, delimiter: "," | "\t" = ","): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') {
      quoted = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0] !== ""));
}

/** Human-readable byte count ("1.2 KiB"). */
export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
}
