/**
 * The composer's typeahead, the pure part: which token under the caret opens
 * a list, and how a pick lands back in the text.
 *
 * Three gestures, the ones every coding CLI has: `/` for skills and commands,
 * `@` for files (and Claude Code's subagents), `$` for Codex's explicit skill
 * mention. Which of them a chat honours is the backend's word — the catalog
 * row's `typeahead` list, decided from the seat's runner — so the box never
 * opens a `/` list for a seat that would read the slash as plain text.
 */

export interface TypeaheadItem {
  /** What lands after the trigger character — `commit`, `github:issue`, `src/app.py`. */
  value: string;
  label: string;
  hint: string;
  /** skill | command | agent | file | folder */
  kind: string;
  /** project | account | plugins | jarvis | agents | files */
  group: string;
}

export interface TypeaheadResponse {
  trigger: string;
  items: TypeaheadItem[];
  truncated: boolean;
}

export interface ActiveToken {
  trigger: string;
  /** The text typed after the trigger, up to the caret. */
  query: string;
  /** Index of the trigger character. */
  start: number;
  /** The caret. */
  end: number;
}

/**
 * The token the caret sits in, when a trigger character opens it.
 *
 * A token runs from the last whitespace before the caret to the caret. `@`
 * and `$` open anywhere a word starts; `/` only at the very start of the
 * message, because that is the only place a CLI reads a slash command — a
 * `/` inside a path or a URL must not throw a list over the sentence. An
 * address like `a@b` never opens: the token's first character is `a`.
 */
export function activeToken(
  text: string,
  caret: number,
  triggers: readonly string[],
): ActiveToken | null {
  if (!triggers.length) return null;
  const at = Math.max(0, Math.min(caret, text.length));
  let start = at;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  const trigger = text[start];
  if (!trigger || !triggers.includes(trigger)) return null;
  if (trigger === "/" && text.slice(0, start).trim() !== "") return null;
  return { trigger, query: text.slice(start + 1, at), start, end: at };
}

/**
 * The text with `item` written over the token, and where the caret goes.
 *
 * A file or a name is followed by one space so typing continues past it. A
 * folder is not: it ends in `/`, and the next keystroke should narrow the
 * list to what is inside it, the way a shell completes a path.
 */
export function applyPick(
  text: string,
  token: ActiveToken,
  item: TypeaheadItem,
): { text: string; caret: number } {
  const trailing = item.kind === "folder" ? "" : " ";
  const inserted = `${token.trigger}${item.value}${trailing}`;
  const next = text.slice(0, token.start) + inserted + text.slice(token.end);
  return { text: next, caret: token.start + inserted.length };
}

/** A prefix match on the value or the label first, then a substring, then the hint. */
export function filterItems(items: readonly TypeaheadItem[], query: string): TypeaheadItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...items];
  const ranked: { score: number; index: number; item: TypeaheadItem }[] = [];
  items.forEach((item, index) => {
    const value = item.value.toLowerCase();
    const label = item.label.toLowerCase();
    let score: number;
    if (value.startsWith(q) || label.startsWith(q)) score = 0;
    else if (value.includes(q) || label.includes(q)) score = 1;
    else if (item.hint.toLowerCase().includes(q)) score = 2;
    else return;
    ranked.push({ score, index, item });
  });
  ranked.sort((a, b) => a.score - b.score || a.index - b.index);
  return ranked.map((r) => r.item);
}

/** Triggers whose list is read once and filtered here; `@` asks the backend per keystroke. */
export function isStaticTrigger(trigger: string): boolean {
  return trigger === "/" || trigger === "$";
}

/** Items in the order they arrived, cut into runs of one group for headings. */
export function groupRuns(items: readonly TypeaheadItem[]): { group: string; items: TypeaheadItem[] }[] {
  const runs: { group: string; items: TypeaheadItem[] }[] = [];
  for (const item of items) {
    const last = runs[runs.length - 1];
    if (last && last.group === item.group) last.items.push(item);
    else runs.push({ group: item.group, items: [item] });
  }
  return runs;
}
