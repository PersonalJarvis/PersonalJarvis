/**
 * The little bit of formatting a chat answer actually uses.
 *
 * A model writes `**bold**` and `` `code` `` in prose whatever you tell it, and
 * a panel that prints the asterisks reads as broken. This is deliberately NOT a
 * markdown renderer: the assistant's answer is a paragraph or three of prose,
 * so bold, italic and inline code are the whole vocabulary. Anything else
 * (headings, tables, links) is left as written rather than half-supported.
 *
 * `++text++` is folded into bold too — Gemini's own emphasis marker, seen in
 * every setup answer on 2026-08-25 and meaningless to a reader.
 */
import type { ReactNode } from "react";

/** `**a**`, `++a++`, `` `a` `` and `*a*` — in that order of precedence. */
const TOKEN = /(\*\*[^*]+\*\*|\+\+[^+]+\+\+|`[^`]+`|\*[^*\n]+\*)/g;

export function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(TOKEN)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const token = match[0];
    if (token.startsWith("**") || token.startsWith("++")) {
      out.push(
        <strong key={key++} className="font-semibold">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("`")) {
      out.push(
        <code
          key={key++}
          className="rounded bg-muted px-1 py-0.5 font-mono text-[12.5px] text-foreground"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      out.push(
        <em key={key++} className="italic">
          {token.slice(1, -1)}
        </em>,
      );
    }
    last = at + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
