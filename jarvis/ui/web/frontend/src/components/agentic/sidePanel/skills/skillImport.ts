/**
 * Turning a Markdown file or a pasted text into a skill draft.
 *
 * Title and summary follow the backend's `derive_title` / `derive_description`
 * (frontmatter first, then the first heading or prose line) so what the editor
 * suggests is what the library would pick; the backend stays the authority for
 * files saved without the editor.
 */

/** What the file pickers offer. `.txt` too: plenty of prompts live in plain text. */
export const MARKDOWN_ACCEPT = ".md,.markdown,.mdx,.txt,text/markdown,text/plain";

/** Larger than any prompt a terminal should receive in one paste. */
export const MAX_IMPORT_BYTES = 400_000;

const MARKDOWN_NAME = /\.(md|markdown|mdx|txt)$/i;
const BYTE_ORDER_MARK = 0xfeff;
const FRONTMATTER = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/;
const HEADING = /^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/;

export function isMarkdownFile(file: File): boolean {
  return MARKDOWN_NAME.test(file.name) || file.type === "text/markdown" || file.type === "text/plain";
}

export class SkillImportError extends Error {}

function withoutBom(text: string): string {
  return text.charCodeAt(0) === BYTE_ORDER_MARK ? text.slice(1) : text;
}

/** `File.text()`, with the older reader for engines that lack it. */
function readText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new SkillImportError(`${file.name}: could not be read.`));
    reader.readAsText(file);
  });
}

/** A file's text, refusing binaries and anything too large to paste. */
export async function readMarkdownFile(file: File): Promise<string> {
  if (!isMarkdownFile(file)) throw new SkillImportError(`${file.name}: not a Markdown or text file.`);
  if (file.size > MAX_IMPORT_BYTES) throw new SkillImportError(`${file.name}: larger than ${Math.round(MAX_IMPORT_BYTES / 1000)} KB.`);
  const text = await readText(file);
  if (text.includes(String.fromCharCode(0))) throw new SkillImportError(`${file.name}: not a text file.`);
  return withoutBom(text).replace(/\r\n?/g, "\n");
}

function clean(value: string, limit: number): string {
  return value.replace(/\s+/g, " ").trim().slice(0, limit);
}

/** The frontmatter block's text and the body after it. */
function splitFront(content: string): { front: string | null; body: string } {
  const text = withoutBom(content);
  const match = FRONTMATTER.exec(text);
  return match ? { front: match[1], body: text.slice(match[0].length) } : { front: null, body: text };
}

/** One scalar field of a frontmatter block, folded block scalars included. */
function frontField(front: string, key: string): string {
  const match = new RegExp(`^${key}\\s*:\\s*(.+?)\\s*$`, "m").exec(front);
  if (!match) return "";
  const value = match[1].replace(/^["']|["']$/g, "");
  if (!/^[>|]-?$/.test(value)) return value;
  const folded: string[] = [];
  for (const line of front.slice(match.index + match[0].length).split(/\r?\n/)) {
    if (line.trim() && !/^[ \t]/.test(line)) break;
    if (line.trim()) folded.push(line.trim());
  }
  return folded.join(" ");
}

/** "pr-review.md" → "pr review"; a bare SKILL.md names nothing. */
function titleFromFilename(filename: string): string {
  const stem = filename.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, "") ?? "";
  if (stem.toUpperCase() === "SKILL") return "";
  return clean(stem.replace(/[-_]+/g, " "), 120);
}

export function suggestSkillTitle(content: string, filename = ""): string {
  const { front, body } = splitFront(content);
  const name = front ? frontField(front, "name") : "";
  if (name) return clean(name, 120);
  for (const line of body.split("\n")) {
    const heading = HEADING.exec(line);
    if (heading) return clean(heading[1], 120);
  }
  const fromName = titleFromFilename(filename);
  if (fromName) return fromName;
  const first = body.split("\n").find((line) => line.trim());
  return first ? clean(first.replace(/^[#>\-*`\s]+/, ""), 120) : "";
}

/** The one-line summary the library derives: frontmatter `description`, else the first prose line. */
export function deriveSkillDescription(content: string): string {
  const { front, body } = splitFront(content);
  const described = front ? frontField(front, "description") : "";
  if (described) return clean(described, 280);
  let fence = false;
  for (const line of body.split("\n")) {
    const trimmed = line.trim();
    if (/^(```|~~~)/.test(trimmed)) {
      fence = !fence;
      continue;
    }
    if (fence || !trimmed || HEADING.test(line) || /^(\||---|<!--)/.test(trimmed)) continue;
    return clean(trimmed.replace(/^[>\-*+ ]+/, "").replace(/\*\*|`/g, ""), 280);
  }
  return "";
}
